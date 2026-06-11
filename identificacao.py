#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
identificacao.py — Microserviço 2: Identificação de formato (E4).

Referência: Seções 5.2 e 6.3.2.1 do TCC.

A etapa E4 executa o DROID (padrão) ou o Siegfried (alternativa para grandes
volumes) sobre os arquivos do SIP, produzindo, para cada arquivo, o identificador
único PRONOM (PUID), o nome e a versão do formato. Esses dados são gravados
diretamente em <premis:format> (com <formatRegistry> apontando para o PRONOM
via PUID) dentro do <premis:object> de cada arquivo em metadata/premis.xml.

O evento agregado "format identification" é registrado em PREMIS (MS7).

Conforme a Seção 6.3.2.1, a ausência de identificação de algum arquivo gera um
alerta (informação relevante para decisões de preservação), mas NÃO interrompe
o fluxo — o objeto digital ainda é ingerido.

Integração:
  - DROID: ferramenta oficial do TNA, escrita em Java; integrada por subprocess.
    Opera em duas etapas (criação de perfil e exportação para CSV).
  - Siegfried: binário Go estático; saída JSON direta via 'sf -json'.

Uso (CLI):
    python3 identificacao.py --sip <dir> --pacote <pac> --objeto <id> \\
        [--ferramenta droid|siegfried]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

import comum
from eventos import RegistradorPREMIS


def _registrar(premis, resultado, detalhe, agente, versao):
    premis.registrar_evento(
        tipo="format identification", resultado=resultado,
        detalhe=detalhe, agente_software=agente, agente_versao=versao,
    )


def _identificar_siegfried(sip: Path, raiz_pacote: Path, carimbo: str,
                            logger) -> list[dict]:
    """Executa Siegfried (sf -json) e devolve a lista de registros por arquivo.
    Preserva o JSON bruto em logs/identificacao_<carimbo>.json."""
    proc = comum.executar_comando(
        ["sf", "-json", str(sip)], logger=logger, aceitar_codigos=(0,)
    )
    # Preserva a saída bruta como evidência (Seção 5.7).
    try:
        log_arq = comum.salvar_log_microservico(
            raiz_pacote, "identificacao", proc.stdout, sufixo=".json",
            carimbo=carimbo,
        )
        logger.info("JSON bruto do Siegfried preservado em %s", log_arq)
    except Exception as e:
        logger.warning("Não foi possível salvar o log do Siegfried: %s", e)
    dados = json.loads(proc.stdout)
    registros: list[dict] = []
    for arquivo in dados.get("files", []):
        correspondencias = arquivo.get("matches", [{}])
        m = correspondencias[0] if correspondencias else {}
        puid = m.get("id", "")
        reconhecido = bool(puid) and puid.lower() != "unknown"
        registros.append({
            "arquivo": arquivo.get("filename", ""),
            "puid": puid if reconhecido else "",
            "formato": m.get("format", ""),
            "versao": m.get("version", ""),
            "status": "reconhecido" if reconhecido else "nao_reconhecido",
        })
    return registros


def _identificar_droid(sip: Path, raiz_pacote: Path, carimbo: str,
                        logger) -> list[dict]:
    """Executa DROID em duas etapas (perfil + exportação CSV) e parseia o CSV.
    Preserva o CSV bruto em logs/identificacao_<carimbo>.csv."""
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        perfil = Path(tmp) / "perfil.droid"
        csv_saida = Path(tmp) / "droid.csv"
        comum.executar_comando(
            ["droid", "-R", "-a", str(sip), "-p", str(perfil)], logger=logger,
        )
        comum.executar_comando(
            ["droid", "-p", str(perfil), "-E", str(csv_saida)], logger=logger,
        )
        # Preserva o CSV bruto do DROID como evidência (Seção 5.7).
        try:
            destino_log = comum.caminho_logs(raiz_pacote) / (
                f"identificacao_{carimbo}.csv"
            )
            shutil.copy2(csv_saida, destino_log)
            logger.info("CSV bruto do DROID preservado em %s", destino_log)
        except Exception as e:
            logger.warning("Não foi possível salvar o log do DROID: %s", e)
        registros: list[dict] = []
        with open(csv_saida, newline="", encoding="utf-8") as f:
            for linha in csv.DictReader(f):
                if linha.get("TYPE") != "File":   # ignora pastas
                    continue
                puid = linha.get("PUID", "")
                registros.append({
                    "arquivo": linha.get("FILE_PATH", linha.get("NAME", "")),
                    "puid": puid,
                    "formato": linha.get("FORMAT_NAME", ""),
                    "versao": linha.get("FORMAT_VERSION", ""),
                    "status": "reconhecido" if puid else "nao_reconhecido",
                })
        return registros


def identificar_formato(
    sip: Path,
    raiz_pacote: Path,
    id_objeto: str,
    ferramenta: str = "droid",
    logger=None,
) -> int:
    """Identifica o formato de cada arquivo do SIP e o registra no PREMIS.

    Para cada arquivo, grava o <premis:format> com PUID, nome e versão na
    seção <objectCharacteristics> do <premis:object> correspondente em
    metadata/premis.xml.

    Retorna comum.RET_SUCESSO sempre que a identificação for executada (mesmo
    com arquivos não reconhecidos, que apenas geram alerta — Seção 6.3.2.1).
    Retorna comum.RET_FALHA apenas se a ferramenta não puder ser executada.
    """
    log = logger or comum.obter_logger("identificacao")
    premis = RegistradorPREMIS(raiz_pacote, id_objeto, logger=log)

    if ferramenta == "droid" and not comum.ferramenta_disponivel("droid"):
        if comum.ferramenta_disponivel("sf"):
            log.warning("DROID indisponível; usando Siegfried como alternativa.")
            ferramenta = "siegfried"
        else:
            msg = "Nenhuma ferramenta de identificação (DROID/Siegfried) disponível."
            log.error(msg)
            _registrar(premis, "failure", msg, "DROID", "indisponível")
            return comum.RET_FALHA
    if ferramenta == "siegfried" and not comum.ferramenta_disponivel("sf"):
        msg = "Siegfried (sf) não está instalado."
        log.error(msg)
        _registrar(premis, "failure", msg, "Siegfried", "indisponível")
        return comum.RET_FALHA

    try:
        carimbo = comum.carimbo_tempo()
        if ferramenta == "siegfried":
            registros = _identificar_siegfried(sip, raiz_pacote, carimbo, log)
            agente, versao = "Siegfried", "PRONOM"
        else:
            registros = _identificar_droid(sip, raiz_pacote, carimbo, log)
            agente, versao = "DROID", "PRONOM"
    except Exception as e:
        log.error("Falha na identificação de formato: %s", e)
        _registrar(premis, "failure", f"Erro de identificação: {e}",
                   ferramenta, "")
        return comum.RET_FALHA

    # Alimenta o PREMIS com o <premis:format> de cada arquivo.
    for r in registros:
        try:
            rel_path = Path(r["arquivo"]).resolve().relative_to(
                raiz_pacote.resolve()
            ).as_posix()
        except ValueError:
            # arquivo fora do pacote (situação atípica) — usa só o nome
            rel_path = Path(r["arquivo"]).name
        premis.registrar_formato(
            rel_path, puid=r["puid"], nome=r["formato"], versao=r["versao"],
        )
    premis.persistir()

    nao_reconhecidos = [r for r in registros if r["status"] == "nao_reconhecido"]
    if nao_reconhecidos:
        nomes = ", ".join(Path(r["arquivo"]).name for r in nao_reconhecidos)
        log.warning("Arquivos não reconhecidos pelo PRONOM: %s", nomes)
        _registrar(premis, "success",
                   f"Identificação concluída com {len(nao_reconhecidos)} "
                   f"arquivo(s) não reconhecido(s) (alerta): {nomes}.",
                   agente, versao)
    else:
        _registrar(premis, "success",
                   f"Todos os {len(registros)} arquivos identificados pelo PRONOM.",
                   agente, versao)
    log.info("Identificação de formato concluída: %d arquivo(s) "
             "registrado(s) em <premis:format>.", len(registros))
    return comum.RET_SUCESSO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MS2 — Identificação de formato por assinatura PRONOM (E4)."
    )
    parser.add_argument("--sip", required=True, help="Caminho do SIP.")
    parser.add_argument("--pacote", required=True, help="Raiz do pacote (PREMIS).")
    parser.add_argument("--objeto", required=True, help="Identificador do objeto.")
    parser.add_argument("--ferramenta", default="droid",
                        choices=["droid", "siegfried"])
    args = parser.parse_args(argv)
    return identificar_formato(Path(args.sip), Path(args.pacote),
                              args.objeto, ferramenta=args.ferramenta)


if __name__ == "__main__":
    sys.exit(main())
