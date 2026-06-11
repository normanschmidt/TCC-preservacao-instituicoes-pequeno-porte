#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
admissao.py — Microserviço 1: Validação de admissão (E2).

Referência: Seções 5.1 e 6.3.1.2 do TCC.

A etapa E2 executa o antivírus ClamAV (clamscan) sobre todos os arquivos do
material recebido. O resultado governa o primeiro ponto de decisão do fluxo
(Seção 6.4):

  - se forem detectados arquivos infectados, o material é movido para um
    diretório de quarentena, um evento de rejeição PREMIS é registrado e o fluxo
    é interrompido para aquele objeto (retorno != 0);
  - se a varredura não encontrar ameaças, o evento de sucesso é registrado e o
    fluxo prossegue (retorno 0).

A ferramenta clamscan fornece códigos de retorno padronizados (CISCO SYSTEMS,
2024):
    0 — nenhum vírus encontrado;
    1 — vírus(es) encontrado(s);
    2 — erro durante a varredura.

Uso (CLI):
    python3 admissao.py --trabalho <copia_trabalho> --quarentena <dir> \\
        --pacote <pacote> --objeto <id>
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import comum
from eventos import RegistradorPREMIS

AGENTE = "ClamAV"


def _versao_clamav() -> str:
    """Obtém a versão do clamscan para registro no evento PREMIS."""
    try:
        proc = subprocess.run(["clamscan", "--version"],
                              capture_output=True, text=True)
        # Ex.: "ClamAV 1.0.5/27200/..." -> "1.0.5"
        bruto = proc.stdout.strip()
        if "ClamAV" in bruto:
            return bruto.split()[1].split("/")[0]
        return bruto
    except Exception:
        return "desconhecida"


def validar_admissao(
    dir_trabalho: Path,
    dir_quarentena: Path,
    raiz_pacote: Path,
    id_objeto: str,
    logger=None,
) -> int:
    """Executa a varredura antivírus e decide a admissibilidade do material.

    Retorna comum.RET_SUCESSO se o material está apto a prosseguir, ou
    comum.RET_FALHA se foi rejeitado (infectado) ou se a varredura falhou.
    """
    log = logger or comum.obter_logger("admissao")
    premis = RegistradorPREMIS(raiz_pacote, id_objeto, logger=log)
    versao = _versao_clamav()

    if not comum.ferramenta_disponivel("clamscan"):
        msg = "clamscan (ClamAV) não está instalado no sistema."
        log.error(msg)
        premis.registrar_evento(
            tipo="virus check", resultado="failure", detalhe=msg,
            agente_software=AGENTE, agente_versao=versao,
        )
        return comum.RET_FALHA

    # clamscan: varredura recursiva, listando apenas infectados.
    cmd = ["clamscan", "-r", "--infected", "--no-summary", str(dir_trabalho)]
    log.info("Iniciando varredura antivírus em %s", dir_trabalho)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    relatorio = proc.stdout.strip()
    log.debug("Saída do clamscan:\n%s", relatorio or "(vazia)")

    # Preserva a saída bruta da varredura como evidência forense em logs/.
    try:
        log_arq = comum.salvar_saida_processo(
            raiz_pacote, "admissao", proc, sufixo=".log", cmd=cmd,
        )
        log.info("Saída do ClamAV preservada em %s", log_arq)
    except Exception as e:
        log.warning("Não foi possível salvar o log do ClamAV: %s", e)

    if proc.returncode == 0:
        log.info("Varredura concluída: nenhuma ameaça detectada.")
        premis.registrar_evento(
            tipo="virus check", resultado="success",
            detalhe="Nenhuma ameaça detectada na varredura antivírus.",
            agente_software=AGENTE, agente_versao=versao,
        )
        return comum.RET_SUCESSO

    if proc.returncode == 1:
        log.warning("Arquivos infectados detectados. Material rejeitado.")
        Path(dir_quarentena).mkdir(parents=True, exist_ok=True)
        destino = Path(dir_quarentena) / id_objeto
        try:
            shutil.move(str(dir_trabalho), str(destino))
            log.warning("Material movido para quarentena: %s", destino)
        except Exception as e:
            log.error("Falha ao mover para quarentena: %s", e)
        premis.registrar_evento(
            tipo="virus check", resultado="failure",
            detalhe=f"Material rejeitado por contaminação. Itens:\n{relatorio}",
            agente_software=AGENTE, agente_versao=versao,
        )
        return comum.RET_FALHA

    # returncode == 2 ou outro: erro de varredura
    msg = f"Erro durante a varredura antivírus (código {proc.returncode})."
    log.error(msg)
    premis.registrar_evento(
        tipo="virus check", resultado="failure",
        detalhe=f"{msg} stderr: {proc.stderr.strip()}",
        agente_software=AGENTE, agente_versao=versao,
    )
    return comum.RET_FALHA


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MS1 — Validação de admissão por varredura antivírus (E2)."
    )
    parser.add_argument("--trabalho", required=True,
                        help="Cópia de trabalho a ser verificada (saída de E1).")
    parser.add_argument("--quarentena", required=True,
                        help="Diretório de quarentena para material rejeitado.")
    parser.add_argument("--pacote", required=True,
                        help="Raiz do pacote para registro do evento PREMIS.")
    parser.add_argument("--objeto", required=True,
                        help="Identificador do objeto.")
    args = parser.parse_args(argv)

    return validar_admissao(
        Path(args.trabalho), Path(args.quarentena),
        Path(args.pacote), args.objeto,
    )


if __name__ == "__main__":
    sys.exit(main())
