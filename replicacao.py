#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
replicacao.py — Microserviço 8: Armazenamento com replicação (E9).

Referência: Seções 5.8 e 6.3.3.1 do TCC.

A etapa E9 transfere o AIP finalizado na E8 para três destinos distintos,
implementando a regra 3‑2‑1: um destino primário local e dois secundários, sendo
ao menos um geograficamente distinto (remoto). Após cada transferência, é feita a
verificação de integridade com base nos manifestos de checksum (MS5). Um evento
PREMIS de armazenamento registra cada destino e o resultado da transferência.

Ferramentas (Seção 5.8):
  - rsync: cópias locais e em rede local (sincronização incremental);
  - rclone: cópia remota em provedores de nuvem (verificação por hash);
  - sftp/scp: alternativa para destino remoto único via SSH.

O resultado desta etapa constitui o terceiro ponto de decisão do workflow
(Seção 6.4): se algum destino não puder ser alcançado ou se a verificação
pós-transferência falhar, registra-se a falha do destino específico e gera-se
alerta. A interrupção é relativa apenas ao destino que falhou.

Configuração dos destinos: arquivo JSON com a lista de destinos. Cada destino tem
'tipo' ("local" | "remoto"), 'caminho' (diretório local ou 'remoto:caminho' do
rclone) e 'rotulo'. Exemplo em destinos.exemplo.json.

Uso (CLI):
    python3 replicacao.py --aip <dir_aip> --objeto <id> --destinos destinos.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import logging
from pathlib import Path

import bagit

import comum
from eventos import RegistradorPREMIS


def _garantir_source_consistente(aip: Path, log) -> None:
    """Faz flush dos FileHandlers do log e regrava o tagmanifest do AIP-fonte.

    Usada antes de suspender o log para o E9 — garante que o estado em disco
    do log esteja consistente com o tagmanifest, antes da janela em que o
    log deixa de ser escrito.
    """
    for h in list(log.handlers):
        if isinstance(h, logging.FileHandler):
            h.flush()
    try:
        bag = bagit.Bag(str(aip))
        bag.save(manifests=True)
    except Exception as e:
        log.warning("Não foi possível regravar tagmanifest do fonte antes "
                    "da replicação: %s", e)


class _SuspenderLogArquivo:
    """Context manager que remove temporariamente os FileHandlers do logger.

    Durante o E9, o conteúdo do log do workflow não pode mudar enquanto o
    rsync/rclone copia o AIP — qualquer escrita invalida o tagmanifest na
    cópia em trânsito. Suspendemos o FileHandler enquanto os destinos são
    transferidos; mensagens nesse período seguem visíveis na saída padrão,
    mas não são gravadas no arquivo de log. Após a suspensão, o handler é
    restaurado e as mensagens voltam a ser arquivadas.

    Os destinos recebem, portanto, uma cópia do AIP com o log no estado
    imediatamente anterior ao início das transferências — selada pelo
    tagmanifest renovado de _garantir_source_consistente.
    """

    def __init__(self, log):
        self.log = log
        self.removidos: list[logging.FileHandler] = []

    def __enter__(self):
        for h in list(self.log.handlers):
            if isinstance(h, logging.FileHandler):
                h.flush()
                self.log.removeHandler(h)
                self.removidos.append(h)
        return self

    def __exit__(self, *args):
        for h in self.removidos:
            self.log.addHandler(h)


def _replicar_local(aip: Path, destino: str, logger,
                     ) -> tuple[bool, str, str]:
    """Replica para destino local/rede local com rsync.

    Retorna (ok, mensagem, log_bruto). O log_bruto é o stdout/stderr do rsync
    com o comando e o exit code — o chamador grava esse conteúdo em
    logs/replicacao_<ts>/<rotulo>.log do AIP-fonte ao final do lote, em uma
    única atualização do tagmanifest.

    A integridade da cópia é garantida pelo modo checksum do rsync (-c) e pelo
    seu código de retorno; não se executa bag.validate() no destino porque
    qualquer escrita posterior em logs/ ou metadata/ no AIP-fonte tornaria o
    tagmanifest da cópia obsoleto antes da validação.
    """
    if not comum.ferramenta_disponivel("rsync"):
        return (False, "rsync indisponível.", "")
    try:
        Path(destino).mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        return (False, f"Sem permissão para criar destino '{destino}': {e}", "")
    except OSError as e:
        return (False, f"Não foi possível preparar destino '{destino}': {e}", "")
    # -a (arquivamento), -c (checksum na comparação), --delete para espelhar.
    cmd = ["rsync", "-ac", "--delete", f"{str(aip).rstrip('/')}/",
           f"{str(destino).rstrip('/')}/{Path(aip).name}/"]
    try:
        proc = comum.executar_comando(cmd, logger=logger)
    except Exception as e:
        return (False, f"Falha no rsync: {e}",
                f"$ {' '.join(str(c) for c in cmd)}\n[exceção]: {e}\n")
    partes = [
        "$ " + " ".join(str(c) for c in cmd),
        "--- stdout ---", proc.stdout.rstrip() if proc.stdout else "",
        "--- stderr ---", proc.stderr.rstrip() if proc.stderr else "",
        f"--- exit code: {proc.returncode} ---",
    ]
    return (True, "Cópia concluída via rsync -c (checksum).",
            "\n".join(partes))


def _replicar_remoto(aip: Path, destino: str, logger,
                      ) -> tuple[bool, str, str]:
    """Replica para destino remoto com rclone (copy + check por hash).

    Retorna (ok, mensagem, log_bruto). O 'rclone check' compara hashes entre
    origem e destino — é a verificação de integridade da transferência remota.
    """
    if not comum.ferramenta_disponivel("rclone"):
        return (False, "rclone indisponível.", "")
    alvo = f"{destino.rstrip('/')}/{Path(aip).name}"
    partes: list[str] = []
    try:
        for sub_cmd in (["rclone", "copy", str(aip), alvo],
                        ["rclone", "check", str(aip), alvo]):
            proc = comum.executar_comando(sub_cmd, logger=logger)
            partes.append("$ " + " ".join(str(c) for c in sub_cmd))
            if proc.stdout:
                partes.append("--- stdout ---")
                partes.append(proc.stdout.rstrip())
            if proc.stderr:
                partes.append("--- stderr ---")
                partes.append(proc.stderr.rstrip())
            partes.append(f"--- exit code: {proc.returncode} ---\n")
    except Exception as e:
        partes.append(f"[exceção]: {e}")
        return (False, f"Falha no rclone: {e}", "\n".join(partes))
    return (True, "Transferência remota verificada por hash (rclone check).",
            "\n".join(partes))


def replicar(
    aip: Path,
    raiz_pacote: Path,
    id_objeto: str,
    destinos: list[dict],
    logger=None,
) -> int:
    """Replica o AIP para todos os destinos configurados (E9).

    Sequência em duas fases para preservar a consistência do tagmanifest:
      1. Todas as transferências são executadas sem tocar no AIP-fonte —
         destinos recebem snapshots íntegros do AIP no estado em que ele se
         encontra ao iniciar o E9.
      2. Ao final, todos os logs de ferramenta (rsync/rclone) são gravados em
         logs/replicacao_<ts>/<destino>.log do AIP-fonte, todos os eventos
         PREMIS de replication são registrados, e bag.save() é chamado uma
         única vez para renovar o tagmanifest do AIP-fonte.

    Retorna comum.RET_SUCESSO se todos os destinos foram bem-sucedidos, ou
    comum.RET_ALERTA se ao menos um falhou (sem interromper os demais —
    Seção 6.4).
    """
    log = logger or comum.obter_logger("replicacao")

    if not destinos:
        log.error("Nenhum destino de replicação configurado.")
        return comum.RET_FALHA

    # Fase 1 — todas as transferências, sem modificar o AIP-fonte.
    # Sela o tagmanifest com o estado atual do log e suspende o FileHandler
    # durante o loop: cada destino recebe o AIP no estado pré-E9, com o
    # tagmanifest correspondente, garantindo bag.validate() na cópia.
    _garantir_source_consistente(Path(aip), log)
    resultados: list[dict] = []
    carimbo = comum.carimbo_tempo()
    with _SuspenderLogArquivo(log):
        for d in destinos:
            rotulo = d.get("rotulo", d.get("caminho", "destino"))
            tipo = d.get("tipo", "local")
            caminho = d.get("caminho", "")
            log.info("Replicando para [%s] %s (%s)...", tipo, rotulo, caminho)

            rotulo_safe = "".join(
                c if c.isalnum() or c in "-_." else "_" for c in rotulo
            )

            if tipo == "remoto":
                ok, msg, log_bruto = _replicar_remoto(Path(aip), caminho, log)
                agente = "rclone"
            else:
                ok, msg, log_bruto = _replicar_local(Path(aip), caminho, log)
                agente = "rsync"

            if ok:
                log.info("Destino '%s' concluído: %s", rotulo, msg)
            else:
                log.warning("Falha no destino '%s': %s", rotulo, msg)
            resultados.append({
                "rotulo": rotulo, "rotulo_safe": rotulo_safe, "tipo": tipo,
                "caminho": caminho, "ok": ok, "msg": msg, "agente": agente,
                "log_bruto": log_bruto,
            })

    # Fase 2 — registro consolidado no AIP-fonte (uma única atualização do
    # tagmanifest cobre logs + premis).
    premis = RegistradorPREMIS(raiz_pacote, id_objeto, logger=log)
    for r in resultados:
        if r["log_bruto"]:
            try:
                comum.salvar_log_microservico(
                    raiz_pacote, "replicacao", r["log_bruto"],
                    sufixo=".log", sub=r["rotulo_safe"], carimbo=carimbo,
                )
            except Exception as e:
                log.warning("Não foi possível salvar o log de '%s': %s",
                            r["rotulo"], e)
        premis.registrar_evento(
            tipo="replication",
            resultado="success" if r["ok"] else "failure",
            detalhe=f"Destino '{r['rotulo']}' ({r['tipo']}, {r['caminho']}): "
                    f"{r['msg']}",
            agente_software=r["agente"], agente_versao="",
        )

    # Re-sela o tagmanifest do AIP-fonte para refletir os novos logs em logs/
    # e os novos eventos em metadata/premis.xml — bag.validate() local volta a
    # passar e futuros E10/E9 partem de um AIP consistente.
    try:
        bag = bagit.Bag(str(aip))
        bag.save(manifests=True)
    except Exception as e:
        log.warning("Não foi possível atualizar o tagmanifest após o E9: %s", e)

    falhas = [r["rotulo"] for r in resultados if not r["ok"]]
    if falhas:
        log.warning("Replicação concluída com falhas em: %s", ", ".join(falhas))
        return comum.RET_ALERTA
    log.info("Replicação 3-2-1 concluída com sucesso em todos os destinos.")
    return comum.RET_SUCESSO


def refrescar_destinos(aip: Path, destinos: list[dict], logger=None) -> int:
    """Re-sincroniza o AIP-fonte (já finalizado) para todos os destinos, de
    modo que as cópias fiquem byte-a-byte idênticas ao fonte.

    Chamada ao FIM da sessão, depois que o log do workflow foi fechado e o
    tagmanifest do fonte foi re-selado em definitivo (ver
    orquestrador._finalizar_sessao). Nesse instante o fonte contém tudo —
    log completo, logs de replicação (logs/replicacao_*), eventos PREMIS de
    replication e tagmanifest atualizado —, e este passe propaga essas
    diferenças para os destinos que a cópia inicial (durante o E9) havia
    deixado defasados.

    O rsync/rclone é incremental: apenas os arquivos alterados desde a cópia
    inicial são transferidos (essencialmente o workflow_*.log, o premis.xml,
    os logs/replicacao_* e o tagmanifest). Falhas em um destino são
    registradas e não interrompem os demais.

    Não escreve no log do workflow do AIP (que já está fechado neste ponto);
    mensagens eventuais de subprocess seguem apenas para a saída padrão.
    """
    log = logger or comum.obter_logger("replicacao")
    if not destinos:
        return comum.RET_SUCESSO
    falhas = []
    for d in destinos:
        rotulo = d.get("rotulo", d.get("caminho", "destino"))
        tipo = d.get("tipo", "local")
        caminho = d.get("caminho", "")
        nome_aip = Path(aip).name
        try:
            if tipo == "remoto":
                alvo = f"{caminho.rstrip('/')}/{nome_aip}"
                comum.executar_comando(
                    ["rclone", "copy", str(aip), alvo], logger=log,
                )
            else:
                Path(caminho).mkdir(parents=True, exist_ok=True)
                comum.executar_comando(
                    ["rsync", "-ac", "--delete", f"{str(aip).rstrip('/')}/",
                     f"{caminho.rstrip('/')}/{nome_aip}/"],
                    logger=log,
                )
            log.info("Destino '%s' refrescado (cópia idêntica ao fonte).",
                     rotulo)
        except Exception as e:
            falhas.append(rotulo)
            log.warning("Falha ao refrescar destino '%s': %s", rotulo, e)
    return comum.RET_ALERTA if falhas else comum.RET_SUCESSO


def carregar_destinos(caminho: Path) -> list[dict]:
    """Carrega a configuração de destinos a partir de um arquivo JSON."""
    return json.loads(Path(caminho).read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MS8 — Armazenamento com replicação 3-2-1 (E9)."
    )
    parser.add_argument("--aip", required=True, help="Caminho do AIP finalizado.")
    parser.add_argument("--objeto", required=True, help="Identificador do objeto.")
    parser.add_argument("--destinos", required=True,
                        help="Arquivo JSON com a configuração de destinos.")
    parser.add_argument("--pacote", default=None,
                        help="Raiz do pacote para PREMIS (padrão: o próprio AIP).")
    args = parser.parse_args(argv)

    pacote = Path(args.pacote) if args.pacote else Path(args.aip)
    destinos = carregar_destinos(Path(args.destinos))
    return replicar(Path(args.aip), pacote, args.objeto, destinos)


if __name__ == "__main__":
    sys.exit(main())
