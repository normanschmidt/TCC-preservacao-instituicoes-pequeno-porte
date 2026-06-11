#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
caracterizacao.py — Microserviço 4: Extração de metadados técnicos (E6 e E7).

Referência: Seções 5.4, 6.3.2.3 (E6) e 6.3.2.4 (E7) do TCC.

Este microserviço extrai automaticamente as propriedades técnicas dos arquivos
(formato, versão, dimensões, resolução, espaço de cor, codec, taxa de amostragem
etc.). O XML produzido pela ferramenta de caracterização é embutido na seção
<premis:objectCharacteristicsExtension> do <premis:object> correspondente em
metadata/premis.xml, em conformidade com o esquema PREMIS v3 (Seção 5.7). Como
a extração deve ocorrer também após transformações, o mesmo microserviço é
reexecutado sobre as representações derivadas geradas em E7 (Seção 6.3.2.3).

Ferramentas (Seção 5.4):
  - FITS (padrão): agregador (DROID, ExifTool, MediaInfo, JHOVE, Tika) que
    consolida tudo em um único XML; executado por subprocess (fits.sh / fits).
  - ExifTool + MediaInfo (alternativa leve, sem Java): integração por
    subprocess.

Ao concluir, grava o evento PREMIS correspondente (MS7).

Uso (CLI):
    python3 caracterizacao.py --alvo <dir> --pacote <pac> --objeto <id> \\
        [--ferramenta fits|exif] [--rotulo originais|derivadas]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from lxml import etree

import comum
from eventos import RegistradorPREMIS


def _nome_fits() -> str | None:
    """Resolve o executável do FITS (fits.sh ou fits) no PATH."""
    for nome in ("fits.sh", "fits"):
        if comum.ferramenta_disponivel(nome):
            return nome
    return None


def _executar_fits(
    arq: Path, logger, dir_log_fits: Path | None = None, timeout: int = 120,
) -> etree._Element | None:
    """Executa o FITS sobre um arquivo e retorna o XML parseado.

    O FITS grava sua saída em um arquivo (parâmetro -o). Usamos um arquivo
    temporário, parseamos o XML e o devolvemos como elemento lxml para que o
    chamador o embuta no PREMIS. O arquivo temporário é descartado em seguida.

    O parâmetro 'dir_log_fits', quando informado, define o diretório de
    trabalho do subprocesso — o Log4j interno do FITS escreve em './fits.log'
    relativo ao cwd, então direcionamos esse cwd para logs/ do AIP. Assim
    todas as invocações do FITS acumulam suas mensagens em fits.log dentro
    do pacote, coberto pelo tagmanifest, em vez de poluírem o diretório a
    partir do qual o orquestrador foi chamado.

    O parâmetro 'timeout' (em segundos) limita a duração de cada invocação
    do FITS; se ele exceder esse tempo, a execução é abortada e o arquivo é
    pulado com aviso, sem travar o lote inteiro. O FITS é lento por natureza
    — sobe uma JVM por arquivo —, então o limite deve ser generoso.
    """
    binario = _nome_fits()
    if binario is None:
        return None
    with tempfile.NamedTemporaryFile(
        suffix=".fits.xml", delete=False
    ) as tmp:
        tmp_path = Path(tmp.name)
    try:
        comum.executar_comando(
            [binario, "-i", str(arq), "-o", str(tmp_path)],
            logger=logger, timeout=timeout, cwd=dir_log_fits,
        )
        return etree.parse(str(tmp_path)).getroot()
    except subprocess.TimeoutExpired:
        logger.warning("FITS excedeu o timeout de %ds em %s — arquivo pulado.",
                       timeout, arq.name)
        return None
    except Exception as e:
        logger.warning("FITS falhou em %s: %s", arq.name, e)
        return None
    finally:
        tmp_path.unlink(missing_ok=True)


def _executar_exif(arq: Path, logger) -> etree._Element:
    """Extrai metadados com ExifTool (e MediaInfo para A/V), retornando um
    único elemento XML que agrega as saídas."""
    extensoes_av = {".wav", ".mp3", ".mp4", ".mkv", ".mov", ".avi", ".flac"}
    raiz = etree.Element("metadados_tecnicos", arquivo=arq.name)
    try:
        proc = subprocess.run(
            ["exiftool", "-X", str(arq)], capture_output=True, text=True
        )
        if proc.returncode == 0 and proc.stdout.strip():
            try:
                exif_el = etree.fromstring(proc.stdout.encode("utf-8"))
                raiz.append(exif_el)
            except etree.XMLSyntaxError:
                etree.SubElement(raiz, "exiftool_bruto").text = proc.stdout
    except FileNotFoundError:
        logger.warning("ExifTool não instalado.")

    if arq.suffix.lower() in extensoes_av:
        try:
            proc = subprocess.run(
                ["mediainfo", "--Output=JSON", str(arq)],
                capture_output=True, text=True,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                mi = etree.SubElement(raiz, "mediainfo")
                mi.text = json.dumps(json.loads(proc.stdout),
                                     ensure_ascii=False)
        except FileNotFoundError:
            logger.warning("MediaInfo não instalado.")
    return raiz


def extrair_metadados(
    alvo: Path,
    raiz_pacote: Path,
    id_objeto: str,
    ferramenta: str = "fits",
    rotulo: str = "originais",
    logger=None,
) -> int:
    """Extrai metadados técnicos dos arquivos em 'alvo' e os embute no PREMIS.

    Para cada arquivo, o XML produzido pela ferramenta é colocado em
    <premis:objectCharacteristicsExtension> do respectivo <premis:object> em
    metadata/premis.xml. O rótulo (originais/derivadas) é apenas informativo —
    a separação entre matrizes (E6) e derivadas (E7) é refletida no caminho
    relativo de cada arquivo dentro do pacote (data/originais/... ou
    data/derivadas/...).
    """
    log = logger or comum.obter_logger("caracterizacao")
    premis = RegistradorPREMIS(raiz_pacote, id_objeto, logger=log)

    arquivos = comum.listar_arquivos(alvo)
    if not arquivos:
        log.warning("Nenhum arquivo a caracterizar em %s.", alvo)
        return comum.RET_SUCESSO

    # Seleção de ferramenta com degradação graciosa.
    if ferramenta == "fits" and _nome_fits() is None:
        if comum.ferramenta_disponivel("exiftool"):
            log.warning("FITS indisponível; usando ExifTool/MediaInfo.")
            ferramenta = "exif"
        else:
            msg = "Nenhuma ferramenta de caracterização (FITS/ExifTool) disponível."
            log.error(msg)
            premis.registrar_evento(
                tipo="metadata extraction", resultado="failure", detalhe=msg,
                agente_software="FITS", agente_versao="indisponível",
            )
            return comum.RET_FALHA

    agente = "FITS" if ferramenta == "fits" else "ExifTool+MediaInfo"
    processados = 0
    total = len(arquivos)
    invocacoes: list[str] = [
        f"# caracterizacao.py — {rotulo} | ferramenta={agente} | "
        f"inicio={comum.agora_iso()} | total_arquivos={total}",
        "# Formato: <timestamp>\t<arquivo>\t<resultado>",
    ]
    carimbo = comum.carimbo_tempo()
    # Pré-cria o subdiretório do microsserviço em logs/. Ele será o cwd das
    # invocações do FITS — o Log4j interno do FITS escreve ./fits.log relativo
    # ao cwd, e dessa forma o arquivo passa a ser preservado dentro do AIP
    # (logs/caracterizacao_<carimbo>/fits.log), coberto pelo tagmanifest, em
    # vez de poluir o diretório onde o orquestrador foi chamado.
    dir_log_ms = comum.caminho_logs(raiz_pacote) / f"caracterizacao_{carimbo}"
    dir_log_ms.mkdir(parents=True, exist_ok=True)
    try:
        for i, arq in enumerate(arquivos, start=1):
            rel_path = arq.relative_to(raiz_pacote).as_posix()
            log.info("[%d/%d] Caracterizando %s (%s)...",
                     i, total, rel_path, agente)
            inicio = comum.agora_iso()
            if ferramenta == "fits":
                xml_elem = _executar_fits(arq, log, dir_log_fits=dir_log_ms)
            else:
                xml_elem = _executar_exif(arq, log)
            if xml_elem is None:
                invocacoes.append(f"{inicio}\t{rel_path}\tFALHA")
                continue
            premis.registrar_caracterizacao(rel_path, xml_elem)
            invocacoes.append(f"{inicio}\t{rel_path}\tOK")
            processados += 1
        premis.persistir()
        # Lista de invocações fica junto ao fits.log nativo dentro do mesmo
        # subdiretório de caracterização — o XML detalhado de cada execução
        # já está em <objectCharacteristicsExtension> do PREMIS; esta lista
        # documenta apenas a ordem e o resultado.
        try:
            log_arq = comum.salvar_log_microservico(
                raiz_pacote, "caracterizacao",
                "\n".join(invocacoes) + "\n", sufixo=".log",
                sub="invocacoes", carimbo=carimbo,
            )
            log.info("Log de execução da caracterização preservado em %s",
                     log_arq)
        except Exception as e:
            log.warning("Não foi possível salvar o log da caracterização: %s", e)
    except Exception as e:
        log.error("Falha na extração de metadados técnicos: %s", e)
        premis.registrar_evento(
            tipo="metadata extraction", resultado="failure",
            detalhe=f"Erro de caracterização: {e}",
            agente_software=agente, agente_versao="",
        )
        return comum.RET_FALHA

    log.info("Metadados técnicos extraídos de %d arquivo(s) [%s] e embutidos "
             "no PREMIS.", processados, rotulo)
    premis.registrar_evento(
        tipo="metadata extraction", resultado="success",
        detalhe=f"Metadados técnicos extraídos de {processados} arquivo(s) "
                f"({rotulo}) e embutidos em <objectCharacteristicsExtension>.",
        agente_software=agente, agente_versao="",
    )
    return comum.RET_SUCESSO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MS4 — Extração de metadados técnicos (E6/E7)."
    )
    parser.add_argument("--alvo", required=True,
                        help="Diretório com os arquivos a caracterizar.")
    parser.add_argument("--pacote", required=True)
    parser.add_argument("--objeto", required=True)
    parser.add_argument("--ferramenta", default="fits", choices=["fits", "exif"])
    parser.add_argument("--rotulo", default="originais",
                        choices=["originais", "derivadas"])
    args = parser.parse_args(argv)
    return extrair_metadados(Path(args.alvo), Path(args.pacote), args.objeto,
                            ferramenta=args.ferramenta, rotulo=args.rotulo)


if __name__ == "__main__":
    sys.exit(main())
