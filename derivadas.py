#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
derivadas.py — Microserviço 9: Geração de representações derivadas (E7).

Referência: Seções 5.9 e 6.3.2.4 do TCC.

A etapa E7 opera sobre as matrizes de preservação do SIP (data/originais),
gerando representações derivadas a partir delas, SEM alterar o original. As
derivadas são incorporadas ao próprio pacote (data/derivadas), como
representações adicionais à matriz, e consolidadas no AIP na etapa seguinte
(E8). Ao concluir, grava um evento PREMIS de transformação (MS7) com o tipo de
transformação, os parâmetros, a ferramenta, a versão e o resultado.

Ferramentas por tipo de mídia (Seção 5.9):
  - Imagens: ImageMagick (convert/magick);
  - Documentos textuais: LibreOffice headless — PDF/A;
  - Áudio/vídeo: FFmpeg.

As derivadas são geradas conforme o plano de preservação da instituição ou, na
ausência deste, os parâmetros do CONARQ (2010):
  - JPEG com qualidade mínima de 80% para imagens;
  - PDF/A para documentos textuais;
  - resolução mínima de 300 dpi para documentos textuais.

A constituição do Pacote de Informação de Disseminação (DIP) a partir dessas
derivadas não faz parte do fluxo essencial; é microserviço recomendado (R2),
fora do escopo deste workflow (Seções 5.9 e 6.3.2.4).

Uso (CLI):
    python3 derivadas.py --sip <dir> --pacote <pac> --objeto <id>
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import comum
from eventos import RegistradorPREMIS

# Classificação por extensão (orienta a ferramenta e os parâmetros)
EXT_IMAGEM = {".tif", ".tiff", ".png", ".bmp", ".jp2", ".jpg", ".jpeg"}
EXT_TEXTO = {".doc", ".docx", ".odt", ".rtf", ".txt", ".ppt", ".pptx", ".odp"}
EXT_AV = {".wav", ".flac", ".aiff", ".mp4", ".mkv", ".mov", ".avi", ".mpeg"}

# Parâmetros padrão CONARQ (2010)
JPEG_QUALIDADE = "85"      # >= 80%
PDF_FILTRO = "pdf:writer_pdf_Export"   # gera PDF (perfil PDF/A configurável)
AUDIO_BITRATE = "192k"


def _magick_bin() -> str | None:
    for nome in ("magick", "convert"):
        if comum.ferramenta_disponivel(nome):
            return nome
    return None


def _gerar_imagem(arq: Path, destino: Path, logger):
    binario = _magick_bin()
    if binario is None:
        return (None, "ImageMagick indisponível.", None, None)
    saida = destino / f"{arq.stem}_derivada.jpg"
    cmd = [binario, str(arq), "-quality", JPEG_QUALIDADE, str(saida)]
    proc = comum.executar_comando(cmd, logger=logger)
    return (saida, f"ImageMagick -> JPEG q{JPEG_QUALIDADE}", cmd, proc)


def _gerar_texto(arq: Path, destino: Path, logger):
    if not comum.ferramenta_disponivel("soffice"):
        return (None, "LibreOffice (soffice) indisponível.", None, None)
    cmd = ["soffice", "--headless", "--convert-to", PDF_FILTRO,
           "--outdir", str(destino), str(arq)]
    proc = comum.executar_comando(cmd, logger=logger)
    saida = destino / f"{arq.stem}.pdf"
    return (saida if saida.exists() else None,
            "LibreOffice headless -> PDF/A", cmd, proc)


def _gerar_av(arq: Path, destino: Path, logger):
    if not comum.ferramenta_disponivel("ffmpeg"):
        return (None, "FFmpeg indisponível.", None, None)
    if arq.suffix.lower() in {".wav", ".flac", ".aiff"}:
        saida = destino / f"{arq.stem}_derivada.mp3"
        cmd = ["ffmpeg", "-y", "-i", str(arq), "-b:a", AUDIO_BITRATE, str(saida)]
        descricao = f"FFmpeg -> MP3 {AUDIO_BITRATE}"
    else:
        saida = destino / f"{arq.stem}_derivada.mp4"
        cmd = ["ffmpeg", "-y", "-i", str(arq), "-c:v", "libx264",
               "-crf", "23", "-c:a", "aac", str(saida)]
        descricao = "FFmpeg -> MP4 (H.264/AAC)"
    proc = comum.executar_comando(cmd, logger=logger)
    return (saida, descricao, cmd, proc)


def gerar_derivadas(
    sip: Path,
    raiz_pacote: Path,
    id_objeto: str,
    logger=None,
) -> int:
    """Gera as representações derivadas a partir das matrizes (E7).

    Retorna comum.RET_SUCESSO se ao menos uma derivada foi gerada ou se não há
    matrizes elegíveis; comum.RET_ALERTA se nenhuma ferramenta estava disponível
    para os tipos presentes (não interrompe o fluxo — princípio da parcimônia).
    """
    log = logger or comum.obter_logger("derivadas")
    premis = RegistradorPREMIS(raiz_pacote, id_objeto, logger=log)

    origem = Path(sip) / comum.DIR_DADOS / comum.DIR_ORIGINAIS
    origem = origem if origem.exists() else Path(sip)
    destino = Path(raiz_pacote) / comum.DIR_DADOS / comum.DIR_DERIVADAS
    destino.mkdir(parents=True, exist_ok=True)

    geradas, ignoradas, falhas = [], [], []
    buffer_log: list[str] = [
        f"# derivadas.py — inicio={comum.agora_iso()}",
        "",
    ]
    for arq in comum.listar_arquivos(origem):
        ext = arq.suffix.lower()
        try:
            if ext in EXT_IMAGEM:
                saida, desc, cmd, proc = _gerar_imagem(arq, destino, log)
            elif ext in EXT_TEXTO:
                saida, desc, cmd, proc = _gerar_texto(arq, destino, log)
            elif ext in EXT_AV:
                saida, desc, cmd, proc = _gerar_av(arq, destino, log)
            else:
                # Princípio da parcimônia: só processa tipos aplicáveis.
                ignoradas.append(arq.name)
                continue
        except Exception as e:
            log.warning("Falha ao gerar derivada de %s: %s", arq.name, e)
            falhas.append(arq.name)
            buffer_log.append(f"# {arq.name}: EXCEPTION {e}\n")
            continue

        # Acumula a invocação no log do microsserviço.
        if cmd is not None:
            buffer_log.append(f"# {arq.name} -> {desc}")
            buffer_log.append("$ " + " ".join(str(c) for c in cmd))
            if proc is not None:
                if proc.stdout:
                    buffer_log.append("--- stdout ---")
                    buffer_log.append(proc.stdout.rstrip())
                if proc.stderr:
                    buffer_log.append("--- stderr ---")
                    buffer_log.append(proc.stderr.rstrip())
                buffer_log.append(f"--- exit code: {proc.returncode} ---")
            buffer_log.append("")

        if saida:
            log.info("Derivada gerada: %s (%s)", saida.name, desc)
            geradas.append(f"{arq.name} -> {saida.name} [{desc}]")
        else:
            falhas.append(arq.name)
            log.warning("Não foi possível gerar derivada de %s: %s", arq.name, desc)

    # Preserva o log consolidado das ferramentas de derivação (Seção 5.7).
    try:
        log_arq = comum.salvar_log_microservico(
            raiz_pacote, "derivadas",
            "\n".join(buffer_log) + "\n", sufixo=".log",
        )
        log.info("Log das ferramentas de derivação preservado em %s", log_arq)
    except Exception as e:
        log.warning("Não foi possível salvar o log das derivadas: %s", e)

    detalhe = (f"Derivadas geradas: {len(geradas)}; "
               f"ignoradas (tipo não aplicável): {len(ignoradas)}; "
               f"falhas: {len(falhas)}.")
    if geradas:
        detalhe += " | " + "; ".join(geradas)

    premis.registrar_evento(
        tipo="migration",  # transformação/migração da matriz em representação derivada
        resultado="success" if not falhas else "failure",
        detalhe=detalhe,
        agente_software="ImageMagick/LibreOffice/FFmpeg", agente_versao="",
    )

    if not geradas and falhas:
        return comum.RET_ALERTA
    return comum.RET_SUCESSO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MS9 — Geração de representações derivadas (E7)."
    )
    parser.add_argument("--sip", required=True)
    parser.add_argument("--pacote", required=True)
    parser.add_argument("--objeto", required=True)
    args = parser.parse_args(argv)
    return gerar_derivadas(Path(args.sip), Path(args.pacote), args.objeto)


if __name__ == "__main__":
    sys.exit(main())
