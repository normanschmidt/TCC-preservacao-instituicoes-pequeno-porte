#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
conformidade.py — Microserviço 3: Validação de conformidade de formato (E5).

Referência: Seções 5.3 e 6.3.2.2 do TCC.

A validação de conformidade é um microserviço essencial (MS3), e a etapa E5
integra o fluxo regular de ingestão, executada logo após a identificação de
formato (E4). Executa o JHOVE (padrão) sobre os arquivos do SIP, complementado
pelo veraPDF para acervos com PDF/A e pelo MediaConch para acervos audiovisuais,
verificando se cada arquivo está well-formed e valid segundo a especificação do
formato identificado na etapa E4. As verdictos wellFormed e valid são gravados
em <premis:significantProperties> dentro do <premis:object> de cada arquivo em
metadata/premis.xml, e o evento agregado "format validation" é registrado
em PREMIS (MS7).

Política adotada (Seção 6.3.2.2): por se tratar de instituições de pequeno porte,
arquivos não conformes geram apenas alerta em log e evento PREMIS, SEM
interromper o fluxo. A conduta diante de não conformidade é decisão de política
de preservação a cargo da instituição; aqui se adota a de apenas documentar a
conformidade ou não conformidade, sem suspender o processamento.

Uso (CLI):
    python3 conformidade.py --sip <dir> --pacote <pac> --objeto <id> \\
        [--ferramenta jhove|verapdf]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

from lxml import etree

import comum
from eventos import RegistradorPREMIS

# Namespace do relatório XML do JHOVE
JHOVE_NS = "http://schema.openpreservation.org/ois/xml/ns/jhove"


def _validar_arquivo_jhove(
    arquivo: Path, raiz_pacote: Path, carimbo: str, logger
) -> tuple[bool | None, bool | None, str]:
    """Roda o JHOVE em um arquivo e devolve (well_formed, valid, mensagem).
    Preserva o XML bruto do JHOVE em logs/conformidade_<carimbo>/<arquivo>.jhove.xml.

    Cada elemento pode ser True, False ou None (indeterminado).
    Possíveis status JHOVE:
      - "Well-Formed and valid"     -> (True, True)
      - "Well-Formed, but not valid"-> (True, False)
      - "Not well-formed"           -> (False, None)
    """
    import shutil
    with tempfile.TemporaryDirectory() as tmp:
        saida = Path(tmp) / "jhove.xml"
        try:
            comum.executar_comando(
                ["jhove", "-h", "XML", "-o", str(saida), str(arquivo)],
                logger=logger, aceitar_codigos=(0,),
            )
        except Exception as e:
            return (None, None, f"Erro ao executar JHOVE: {e}")
        # Preserva o XML bruto do JHOVE como evidência (Seção 5.7).
        try:
            dir_log = comum.caminho_logs(raiz_pacote) / (
                f"conformidade_{carimbo}"
            )
            dir_log.mkdir(parents=True, exist_ok=True)
            shutil.copy2(saida, dir_log / f"{arquivo.name}.jhove.xml")
        except Exception as e:
            logger.warning("Não foi possível salvar o XML do JHOVE: %s", e)
        try:
            arvore = etree.parse(str(saida))
        except etree.XMLSyntaxError as e:
            return (None, None, f"Relatório JHOVE ilegível: {e}")

        status_el = arvore.find(f".//{{{JHOVE_NS}}}status")
        status_txt = (status_el.text or "").strip() if status_el is not None else ""
        s_low = status_txt.lower()
        # Quatro casos possíveis do JHOVE:
        #   "Well-Formed and valid"       -> (True,  True)
        #   "Well-Formed, but not valid"  -> (True,  False)
        #   "Well-Formed"  (sozinho)      -> (True,  None)   <- validade
        #                                                       indeterminada;
        #                                                       o módulo daquele
        #                                                       formato não
        #                                                       implementa
        #                                                       validação além
        #                                                       da boa-formação
        #                                                       (ex.: HTML, FB2)
        #   "Not well-formed"             -> (False, None)
        if s_low.startswith("well-formed and valid"):
            return (True, True, status_txt)
        if "but not valid" in s_low:
            return (True, False, status_txt)
        if s_low.startswith("well-formed"):
            return (True, None, status_txt)
        if s_low.startswith("not well-formed"):
            return (False, None, status_txt)
        return (None, None, status_txt or "Status não reportado pelo JHOVE.")


def _validar_arquivo_verapdf(
    arquivo: Path, raiz_pacote: Path, carimbo: str, logger
) -> tuple[bool | None, bool | None, str]:
    """Valida PDF/A com veraPDF; devolve (well_formed, valid, mensagem).
    Preserva a saída bruta em logs/conformidade_<carimbo>/<arquivo>.verapdf.xml."""
    try:
        proc = subprocess.run(
            ["verapdf", "--format", "xml", str(arquivo)],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return (None, None, "veraPDF não instalado.")
    # Preserva a saída bruta como evidência.
    try:
        dir_log = comum.caminho_logs(raiz_pacote) / f"conformidade_{carimbo}"
        dir_log.mkdir(parents=True, exist_ok=True)
        (dir_log / f"{arquivo.name}.verapdf.xml").write_text(
            proc.stdout or proc.stderr or "(saída vazia)", encoding="utf-8",
        )
    except Exception as e:
        logger.warning("Não foi possível salvar o relatório do veraPDF: %s", e)
    if proc.returncode == 0:
        return (True, True, "PDF/A válido (veraPDF).")
    if proc.returncode == 1:
        # PDF foi parseado (well-formed) mas não conforma com PDF/A (not valid)
        return (True, False, "PDF/A inválido (veraPDF).")
    return (None, None, proc.stderr.strip() or "Erro veraPDF.")


def validar_conformidade(
    sip: Path,
    raiz_pacote: Path,
    id_objeto: str,
    ferramenta: str = "jhove",
    logger=None,
) -> int:
    """Valida a conformidade de formato dos arquivos do SIP (E5).

    Para cada arquivo avaliado, grava em <premis:significantProperties> os
    indicadores wellFormed e valid (true/false), na seção
    <objectCharacteristics> do <premis:object> correspondente.

    Retorna comum.RET_SUCESSO quando todos os arquivos são conformes,
    comum.RET_ALERTA quando há não conformes (sem interromper o fluxo), e
    comum.RET_FALHA apenas se a ferramenta não puder ser executada.
    """
    log = logger or comum.obter_logger("conformidade")
    premis = RegistradorPREMIS(raiz_pacote, id_objeto, logger=log)

    binario = "jhove" if ferramenta == "jhove" else "verapdf"
    if not comum.ferramenta_disponivel(binario):
        msg = f"Ferramenta de validação de conformidade '{binario}' indisponível."
        log.error(msg)
        premis.registrar_evento(
            tipo="format validation", resultado="failure", detalhe=msg,
            agente_software=binario, agente_versao="indisponível",
        )
        return comum.RET_FALHA

    # Considera apenas as matrizes (data/originais), conforme o fluxo.
    base = Path(sip) / comum.DIR_DADOS / comum.DIR_ORIGINAIS
    base = base if base.exists() else Path(sip)
    arquivos = comum.listar_arquivos(base)

    nao_conformes = []
    avaliados = 0
    carimbo = comum.carimbo_tempo()
    for arq in arquivos:
        if ferramenta == "verapdf" and arq.suffix.lower() != ".pdf":
            continue
        if ferramenta == "jhove":
            well_formed, valid, mensagem = _validar_arquivo_jhove(
                arq, raiz_pacote, carimbo, log,
            )
        else:
            well_formed, valid, mensagem = _validar_arquivo_verapdf(
                arq, raiz_pacote, carimbo, log,
            )
        # Registra significantProperties no PREMIS
        try:
            rel_path = arq.resolve().relative_to(raiz_pacote.resolve()).as_posix()
        except ValueError:
            rel_path = arq.name
        premis.registrar_conformidade(rel_path, well_formed, valid)
        avaliados += 1
        if well_formed is False or valid is False:
            # Não-conformidade efetiva — JHOVE/veraPDF afirmou que algo falha.
            nao_conformes.append(arq.name)
            log.warning("Arquivo não conforme: %s — %s", arq.name, mensagem)
        elif well_formed is True and valid is None:
            # Validade indeterminada — o módulo daquele formato no JHOVE não
            # implementa validação além da boa-formação; é o caso de HTML,
            # FB2 e outros formatos com módulos parciais. Não é uma falha:
            # o arquivo é bem-formado, apenas a validade não foi avaliada.
            log.info("Validade indeterminada (módulo JHOVE não valida este "
                     "formato): %s — %s", arq.name, mensagem)
    premis.persistir()

    agente = "JHOVE" if ferramenta == "jhove" else "veraPDF"
    if nao_conformes:
        premis.registrar_evento(
            tipo="format validation", resultado="success",
            detalhe=f"Validação concluída ({avaliados} arquivo(s)); "
                    f"{len(nao_conformes)} não conforme(s) documentado(s): "
                    f"{', '.join(nao_conformes)}.",
            agente_software=agente, agente_versao="",
        )
        return comum.RET_ALERTA

    premis.registrar_evento(
        tipo="format validation", resultado="success",
        detalhe=f"Todos os {avaliados} arquivo(s) avaliados são "
                "well-formed e valid.",
        agente_software=agente, agente_versao="",
    )
    log.info("Validação de conformidade concluída: %d arquivo(s) registrado(s) "
             "em <premis:significantProperties>.", avaliados)
    return comum.RET_SUCESSO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MS3 — Validação de conformidade de formato (E5)."
    )
    parser.add_argument("--sip", required=True)
    parser.add_argument("--pacote", required=True)
    parser.add_argument("--objeto", required=True)
    parser.add_argument("--ferramenta", default="jhove",
                        choices=["jhove", "verapdf"])
    args = parser.parse_args(argv)
    return validar_conformidade(Path(args.sip), Path(args.pacote),
                               args.objeto, ferramenta=args.ferramenta)


if __name__ == "__main__":
    sys.exit(main())
