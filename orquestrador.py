#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orquestrador.py — Controle do fluxo de ingestão (E1 a E9).

Referência: Seções 6.2, 6.3, 6.4 e 7.1 do TCC.

O orquestrador controla a execução do fluxo, encadeia as chamadas aos scripts dos
microserviços na sequência das etapas, registra mensagens de log e toma decisões
com base no retorno de cada script (Quadro 7). Implementa o princípio da
sequencialidade com dependências explícitas: nenhuma etapa começa sem que a
anterior tenha concluído com sucesso (Seção 6.1).

Parâmetros de linha de comando (Seção 7.1):
  - diretório de entrada, onde buscar matrizes ou pacotes para processamento;
  - diretório de saída, onde criar o pacote AIP final;
  - nome (opcional) para a sessão; recebe um sufixo universalmente único.

Sequência do fluxo de ingestão:
  E1  Recepção e preparação              (orquestração; cópia/descompactação)
  E2  Validação de admissão        (admissao.py)        -> 1º ponto de decisão
  E3  Constituição/validação do SIP (empacotamento.py)  -> 2º ponto de decisão
        (apoio: metadados_descritivos.py)
  E4  Identificação de formato      (identificacao.py)
  E5  Validação de conformidade     (conformidade.py)
  E6  Extração de metadados (matrizes)  (caracterizacao.py)
  E7  Geração de derivadas          (derivadas.py)
        + reextração de metadados (derivadas) (caracterizacao.py)
  E8  Empacotamento do AIP          (empacotamento.py)
  E9  Armazenamento e replicação    (replicacao.py)     -> 3º ponto de decisão

Estratégia de falha (Seção 6.4): falha explícita — o fluxo interrompe e gera
eventos/mensagens de alerta, para não processar objeto cujo estado é incerto.

Uso (CLI):
    python3 orquestrador.py --entrada <dir_entrada> --saida <dir_saida> \\
        [--nome lote_2026_03] [--destinos destinos.json]
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tarfile
import zipfile
from pathlib import Path

import bagit

import comum
import admissao
import metadados_descritivos
import empacotamento
import identificacao
import conformidade
import caracterizacao
import derivadas
import replicacao
from eventos import RegistradorPREMIS


# --------------------------------------------------------------------------- #
# E1 — Recepção e preparação do material
# --------------------------------------------------------------------------- #

def _eh_bag(diretorio: Path) -> bool:
    return (Path(diretorio) / "bagit.txt").exists()


def etapa_e1(entrada: Path, area_trabalho: Path, id_objeto: str,
             logger) -> dict:
    """Recepção, preparação e diagnóstico do material recebido (E1).

    Move o material para uma área de trabalho isolada (descompactando ZIP/TAR/
    TAR.GZ quando necessário, copiando diretórios abertos ou pacotes BagIt),
    preservando o original na entrada. Devolve um diagnóstico para a E3.
    """
    logger.info("=== E1 — Recepção e preparação ===")
    copia = Path(area_trabalho) / id_objeto
    copia.mkdir(parents=True, exist_ok=True)

    itens = list(Path(entrada).iterdir())
    for item in itens:
        if item.suffix.lower() in (".zip",):
            logger.info("Descompactando ZIP: %s", item.name)
            with zipfile.ZipFile(item) as z:
                z.extractall(copia)
        elif item.name.lower().endswith((".tar", ".tar.gz", ".tgz")):
            logger.info("Descompactando TAR: %s", item.name)
            with tarfile.open(item) as t:
                t.extractall(copia)
        elif item.is_dir():
            shutil.copytree(item, copia / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, copia / item.name)

    # Diagnóstico: pacote BagIt? planilha de metadados? manifestos?
    raiz_material = copia
    subdirs = [p for p in copia.iterdir() if p.is_dir()]
    if not _eh_bag(copia) and len(subdirs) == 1 and not any(
        p.is_file() for p in copia.iterdir()
    ):
        raiz_material = subdirs[0]   # material aninhado em um único subdiretório

    diagnostico = {
        "copia": copia,
        "raiz_material": raiz_material,
        "eh_bag": _eh_bag(raiz_material),
        "planilha": metadados_descritivos.localizar_planilha(copia),
    }
    logger.info("Diagnóstico E1 — pacote BagIt: %s | planilha de metadados: %s",
                diagnostico["eh_bag"],
                diagnostico["planilha"].name if diagnostico["planilha"] else "—")
    return diagnostico


# --------------------------------------------------------------------------- #
# Fluxo completo de ingestão (E1–E9)
# --------------------------------------------------------------------------- #

def executar(
    entrada: Path,
    saida: Path,
    nome: str | None = None,
    destinos_json: Path | None = None,
) -> int:
    """Executa o fluxo de ingestão completo, do E1 ao E9."""
    id_objeto = comum.gerar_identificador(nome)

    # Diretórios da sessão.
    saida = Path(saida)
    pacote = saida / id_objeto           # raiz do pacote BagIt (SIP -> AIP)
    area_trabalho = saida / "_trabalho"
    # Quarentena para arquivos infectados: vive DENTRO do _trabalho, é
    # transitória. O workflow é interrompido em caso de detecção (Seção 6.4),
    # então o material infectado não entra no SIP/AIP e não é preservado —
    # apenas fica acessível para diagnóstico no diretório de trabalho até a
    # próxima limpeza.
    quarentena = area_trabalho / "quarentena"
    pacote.mkdir(parents=True, exist_ok=True)

    # Log narrativo da sessão: vive em pacote/logs/, junto dos demais logs do
    # AIP (admissao_*, identificacao_*, conformidade_*, etc.). Para que o
    # tagmanifest reflita o conteúdo final do log — que cresce até o fim do
    # E9 —, _finalizar_sessao() fecha o handler e regrava o tagmanifest uma
    # última vez no bloco finally desta função.
    log = comum.configurar_logging(comum.caminho_logs(pacote), id_objeto)
    log.info("Sessão de ingestão iniciada. Objeto: %s", id_objeto)
    log.info("Entrada: %s | Saída: %s", entrada, saida)

    # Container mutável para comunicar, do corpo das etapas ao bloco finally,
    # os destinos que devem ser refrescados ao final (só preenchido se o E9
    # efetivamente replicou — ver _executar_etapas).
    estado = {"destinos_refresh": []}
    try:
        return _executar_etapas(
            entrada, pacote, area_trabalho, quarentena, id_objeto, log,
            destinos_json, estado,
        )
    finally:
        _finalizar_sessao(log, pacote, estado["destinos_refresh"])


def _finalizar_sessao(
    log: logging.Logger, pacote: Path, destinos_refresh: list[dict],
) -> None:
    """Encerra a sessão de modo que o AIP fique íntegro no disco e as cópias
    de destino fiquem idênticas ao fonte.

    O log do workflow vive em pacote/logs/ e cresce durante toda a execução
    — também depois do bag.save() de E8. Aqui fechamos o handler de arquivo
    (forçando o flush ao disco) e, se já há um bag, regravamos o tagmanifest
    para que ele capture o conteúdo final do log. Assim, bag.validate() volta
    a passar no AIP-fonte ao término da sessão.

    Em seguida, se o E9 replicou para destinos, re-sincroniza o fonte já
    finalizado para esses destinos (refresh incremental). É o que garante que
    as cópias contenham o log completo, os logs de replicação e os eventos
    PREMIS de replication — que a cópia inicial, feita no meio do E9, não
    incluía. Como o handler de log já está fechado, o refresh não realimenta
    o log do AIP.

    Em execuções interrompidas antes do E3 (ainda não há bag), apenas o log
    é fechado — o diretório do pacote contém o log da tentativa frustrada,
    mas não é um BagIt, e não há destinos a refrescar.
    """
    log.info("Encerrando sessão de ingestão.")
    for h in list(log.handlers):
        if isinstance(h, logging.FileHandler):
            h.flush()
            h.close()
            log.removeHandler(h)
    if not (Path(pacote) / "bagit.txt").exists():
        return
    try:
        bag = bagit.Bag(str(pacote))
        bag.save(manifests=True)
    except Exception as e:
        sys.stderr.write(
            f"AVISO: não foi possível selar o tagmanifest final do AIP "
            f"{pacote}: {e}\n"
        )
        return
    # Refresh final: propaga o estado definitivo do fonte para os destinos.
    if destinos_refresh:
        try:
            replicacao.refrescar_destinos(Path(pacote), destinos_refresh, log)
        except Exception as e:
            sys.stderr.write(
                f"AVISO: não foi possível refrescar os destinos de "
                f"replicação: {e}\n"
            )


def _executar_etapas(
    entrada: Path,
    pacote: Path,
    area_trabalho: Path,
    quarentena: Path,
    id_objeto: str,
    log: logging.Logger,
    destinos_json: Path | None,
    estado: dict,
) -> int:
    """Corpo das etapas E1–E9 (extraído para que `executar` possa envolvê-lo
    em try/finally e garantir _finalizar_sessao()).

    O dicionário `estado` é o canal de comunicação com o bloco finally de
    `executar`: ao final do E9, registramos em estado["destinos_refresh"] os
    destinos que devem ser re-sincronizados depois que o fonte for selado em
    definitivo.
    """

    if not Path(entrada).exists() or not any(Path(entrada).iterdir()):
        log.error("Diretório de entrada vazio ou inexistente: %s", entrada)
        return comum.RET_FALHA

    # ----- E1 — Recepção e preparação ------------------------------------- #
    diag = etapa_e1(Path(entrada), area_trabalho, id_objeto, log)
    copia = diag["copia"]
    raiz_material = diag["raiz_material"]

    # ----- E2 — Validação de admissão (1º ponto de decisão) --------------- #
    log.info("=== E2 — Validação de admissão (MS1) ===")
    ret = admissao.validar_admissao(copia, quarentena, pacote, id_objeto, log)
    if ret != comum.RET_SUCESSO:
        log.error("FLUXO INTERROMPIDO em E2: material rejeitado na admissão.")
        return comum.RET_FALHA

    # ----- E3 (apoio) — Metadados descritivos ----------------------------- #
    dublin_core = None
    if diag["planilha"]:
        log.info("=== E3 (apoio) — Coleta de metadados descritivos ===")
        dc_saida = comum.caminho_metadata(pacote) / "dublincore.xml"
        if metadados_descritivos.gerar_dublin_core(
            diag["planilha"], dc_saida, log
        ) == comum.RET_SUCESSO:
            dublin_core = dc_saida

    # ----- E3 — Constituição/validação do SIP (2º ponto de decisão) ------- #
    log.info("=== E3 — Constituição ou validação do SIP (MS6) ===")
    if diag["eh_bag"]:
        # Material já é um BagIt: copia para a raiz do pacote e valida.
        if raiz_material != pacote:
            for item in raiz_material.iterdir():
                alvo = pacote / item.name
                if item.is_dir():
                    shutil.copytree(item, alvo, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, alvo)
        ret = empacotamento.validar_sip(pacote, id_objeto, log)
    else:
        ret = empacotamento.constituir_sip(
            raiz_material, pacote, id_objeto, dublin_core=dublin_core, logger=log
        )
    if ret != comum.RET_SUCESSO:
        log.error("FLUXO INTERROMPIDO em E3: falha na constituição/validação "
                  "do SIP. O material original permanece na entrada.")
        return comum.RET_FALHA

    # ----- E4 — Identificação de formato ---------------------------------- #
    log.info("=== E4 — Identificação de formato (MS2) ===")
    if identificacao.identificar_formato(
        pacote, pacote, id_objeto, logger=log
    ) == comum.RET_FALHA:
        log.warning("E4 não pôde ser executada; prosseguindo com alerta.")

    # ----- E5 — Validação de conformidade de formato (MS3, essencial) ----- #
    log.info("=== E5 — Validação de conformidade de formato (MS3) ===")
    conformidade.validar_conformidade(pacote, pacote, id_objeto, logger=log)

    # ----- E6 — Extração de metadados técnicos das matrizes --------------- #
    log.info("=== E6 — Extração de metadados técnicos das matrizes (MS4) ===")
    matrizes = pacote / comum.DIR_DADOS / comum.DIR_ORIGINAIS
    caracterizacao.extrair_metadados(
        matrizes, pacote, id_objeto, rotulo="originais", logger=log
    )

    # ----- E7 — Geração de derivadas + reextração de metadados ------------ #
    log.info("=== E7 — Geração de representações derivadas (MS9) ===")
    derivadas.gerar_derivadas(pacote, pacote, id_objeto, logger=log)
    derivadas_dir = pacote / comum.DIR_DADOS / comum.DIR_DERIVADAS
    if derivadas_dir.exists() and any(derivadas_dir.iterdir()):
        log.info("=== E7 — Reextração de metadados das derivadas (MS4) ===")
        caracterizacao.extrair_metadados(
            derivadas_dir, pacote, id_objeto, rotulo="derivadas", logger=log
        )

    # ----- E8 — Empacotamento do AIP -------------------------------------- #
    log.info("=== E8 — Empacotamento do AIP (MS6) ===")
    if empacotamento.estruturar_aip(
        pacote, id_objeto, log
    ) != comum.RET_SUCESSO:
        log.error("FLUXO INTERROMPIDO em E8: falha na estruturação do AIP.")
        return comum.RET_FALHA

    # ----- E9 — Armazenamento e replicação (3º ponto de decisão) ---------- #
    log.info("=== E9 — Armazenamento e replicação (MS8) ===")
    if destinos_json and Path(destinos_json).exists():
        destinos = replicacao.carregar_destinos(Path(destinos_json))
        ret = replicacao.replicar(pacote, pacote, id_objeto, destinos, log)
        if ret == comum.RET_ALERTA:
            log.warning("E9 concluída com falha em ao menos um destino "
                        "(alerta relativo ao destino).")
        # Marca os destinos para o refresh final (em _finalizar_sessao), após
        # o fonte ser selado em definitivo — assim as cópias recebem o log
        # completo, os logs de replicação e os eventos PREMIS de replication.
        estado["destinos_refresh"] = destinos
    else:
        log.warning("E9 — nenhum arquivo de destinos informado; replicação "
                    "não executada. Configure --destinos para ativar a regra 3-2-1.")

    log.info("Fluxo de ingestão concluído. AIP em: %s", pacote)
    return comum.RET_SUCESSO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Orquestrador do workflow de preservação digital (E1–E9)."
    )
    parser.add_argument("--entrada", required=True,
                        help="Diretório de entrada com matrizes/pacotes.")
    parser.add_argument("--saida", required=True,
                        help="Diretório de saída onde o AIP será criado.")
    parser.add_argument("--nome", default=None,
                        help="Nome da sessão (recebe sufixo único).")
    parser.add_argument("--destinos", default=None,
                        help="JSON com os destinos de replicação (E9).")
    args = parser.parse_args(argv)

    return executar(
        Path(args.entrada), Path(args.saida), nome=args.nome,
        destinos_json=Path(args.destinos) if args.destinos else None,
    )


if __name__ == "__main__":
    sys.exit(main())
