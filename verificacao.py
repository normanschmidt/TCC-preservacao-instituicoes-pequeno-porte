#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verificacao.py — Microserviço 5: Verificação periódica de integridade (E10).

Referência: Seções 5.5 e 6.3.4.1 do TCC.

A etapa E10 opera de forma independente do fluxo de ingestão, sendo acionada pelo
agendador cron do Linux em um ciclo definido pela política de preservação da
instituição. Para cada AIP armazenado em um diretório especificado, gera novos
checksums e os compara com os valores constantes nos manifestos do pacote BagIt.
Qualquer divergência gera notificação por mensagens de saída, alertando para a
necessidade de restauração a partir de uma das cópias de replicação.

É gerado um evento PREMIS de verificação de integridade para cada AIP verificado.

Observação sobre a fixidez: o checksum SHA-256 de cada arquivo é declarado em
dois locais do AIP, propositalmente redundantes — no `manifest-sha256.txt` do
BagIt (fixidez estrutural do pacote) e em `<premis:fixity>` de cada
`<premis:object>` no `metadata/premis.xml` (fixidez do objeto preservado,
conforme a Seção 5.7). Esta etapa verifica o manifesto do BagIt, que é a
referência operacional usada pela implementação de referência BagIt-Python; a
fixidez registrada em PREMIS permanece como prova canônica por objeto, exigida
pelo padrão.

Ferramentas (Seção 5.5): a verificação por arquivo usa o módulo hashlib / o BagIt
(SHA-256); para árvores de diretórios inteiras, o utilitário hashdeep pode ser
empregado como reforço (geração/comparação de manifestos).

Agendamento (exemplo de crontab, verificação semanal aos domingos às 3h):
    0 3 * * 0 /usr/bin/python3 /caminho/verificacao.py --armazenamento /aips

Uso (CLI):
    python3 verificacao.py --armazenamento <dir_com_aips>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import bagit

import comum
from eventos import RegistradorPREMIS

AGENTE = "BagIt-Python/hashlib (SHA-256)"


def _eh_bag(caminho: Path) -> bool:
    return (caminho / "bagit.txt").exists()


def verificar_aip(aip: Path, logger) -> tuple[bool, str]:
    """Verifica a integridade de um único AIP, comparando com seus manifestos.

    A verificação recai sobre o payload (data/) — as matrizes e derivadas
    preservadas —, recalculando o checksum SHA-256 de cada arquivo e comparando-o
    com o valor declarado no manifesto do pacote (manifest-sha256.txt). Detecta
    divergência de checksum, arquivo declarado e ausente, e arquivo presente no
    payload mas não declarado.

    A fixidez incide sobre o payload, e não sobre os arquivos de tag
    administrativos (logs/ e metadata/), que crescem legitimamente ao
    longo da vida custodial do objeto — cada nova verificação acrescenta um
    evento ao próprio PREMIS. Verificar o payload evita alarmes falsos
    decorrentes dessa mutação administrativa esperada.
    """
    try:
        bag = bagit.Bag(str(aip))
    except Exception as e:
        return (False, f"Erro ao abrir o pacote: {e}")

    problemas: list[str] = []
    entradas = bag.payload_entries()   # {caminho_relativo: {algoritmo: checksum}}

    for relativo, checksums in entradas.items():
        esperado = checksums.get(comum.ALGORITMO_CHECKSUM)
        if esperado is None and checksums:
            esperado = next(iter(checksums.values()))
        arquivo = Path(aip) / relativo
        if not arquivo.exists():
            problemas.append(f"declarado e ausente: {relativo}")
            continue
        atual = comum.calcular_sha256(arquivo)
        if esperado is None or atual.lower() != esperado.lower():
            problemas.append(f"checksum divergente: {relativo}")

    # Arquivos presentes no payload mas não declarados no manifesto.
    declarados = {Path(r).as_posix() for r in entradas}
    raiz_payload = Path(aip) / comum.DIR_DADOS
    if raiz_payload.exists():
        for arquivo in raiz_payload.rglob("*"):
            if arquivo.is_file():
                relativo = arquivo.relative_to(aip).as_posix()
                if relativo not in declarados:
                    problemas.append(f"presente e não declarado: {relativo}")

    if problemas:
        return (False, "DIVERGÊNCIA DE INTEGRIDADE (payload): "
                       + "; ".join(problemas))
    return (True, "Íntegro: checksums do payload conferem com o manifesto "
                  "SHA-256.")


def verificar_armazenamento(armazenamento: Path, logger=None) -> int:
    """Verifica todos os AIPs sob o diretório de armazenamento (E10).

    Retorna comum.RET_SUCESSO se todos os AIPs estão íntegros, ou
    comum.RET_ALERTA se houver qualquer divergência (gera notificação).
    """
    log = logger or comum.obter_logger("verificacao")
    armazenamento = Path(armazenamento)
    if not armazenamento.exists():
        log.error("Diretório de armazenamento inexistente: %s", armazenamento)
        return comum.RET_FALHA

    aips = [p for p in sorted(armazenamento.iterdir())
            if p.is_dir() and _eh_bag(p)]
    if not aips:
        log.warning("Nenhum AIP (pacote BagIt) encontrado em %s.", armazenamento)
        return comum.RET_SUCESSO

    log.info("Iniciando verificação periódica de %d AIP(s).", len(aips))
    divergencias = []
    linhas_relatorio = ["aip;status;mensagem"]

    for aip in aips:
        ok, msg = verificar_aip(aip, log)
        status = "integro" if ok else "divergente"
        linhas_relatorio.append(f"{aip.name};{status};{msg}")

        # Registra o evento PREMIS dentro do próprio AIP verificado.
        premis = RegistradorPREMIS(aip, aip.name, logger=log)
        premis.registrar_evento(
            tipo="fixity check",
            resultado="success" if ok else "failure",
            detalhe=msg, agente_software="BagIt-Python", agente_versao="",
        )

        # Preserva uma linha de log da verificação no próprio AIP. Cada execução
        # de E10 gera um arquivo dentro do logs/ do AIP, formando um histórico
        # cronológico das verificações periódicas (Seção 5.7).
        try:
            comum.salvar_log_microservico(
                aip, "verificacao",
                f"{comum.agora_iso()}\t{status}\t{msg}\n",
                sufixo=".log",
            )
        except Exception as e:
            log.warning("Não foi possível salvar o log de verificação em "
                        "%s: %s", aip.name, e)

        # Como adicionamos um evento PREMIS e um arquivo de log no diretório
        # de tags do AIP, o tagmanifest precisa ser renovado para refletir o
        # novo estado — caso contrário, futuros bag.validate() falhariam por
        # divergência de fixidez no metadata/premis.xml e no logs/.
        try:
            bag = bagit.Bag(str(aip))
            bag.save(manifests=True)
        except Exception as e:
            log.warning("Não foi possível atualizar o tagmanifest do AIP %s "
                        "após E10: %s", aip.name, e)

        if ok:
            log.info("AIP '%s': %s", aip.name, msg)
        else:
            divergencias.append(aip.name)
            log.error("ALERTA — AIP '%s': %s", aip.name, msg)

    # Relatório consolidado de integridade.
    relatorio = armazenamento / "relatorio_integridade.csv"
    relatorio.write_text("\n".join(linhas_relatorio), encoding="utf-8")
    log.info("Relatório de integridade gravado: %s", relatorio)

    if divergencias:
        log.error("VERIFICAÇÃO CONCLUÍDA COM DIVERGÊNCIAS em: %s. "
                  "Recomenda-se restauração a partir das cópias de replicação.",
                  ", ".join(divergencias))
        return comum.RET_ALERTA

    log.info("Verificação concluída: todos os AIPs estão íntegros.")
    return comum.RET_SUCESSO


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MS5 — Verificação periódica de integridade dos AIPs (E10)."
    )
    parser.add_argument("--armazenamento", required=True,
                        help="Diretório que contém os AIPs armazenados.")
    args = parser.parse_args(argv)

    log = comum.configurar_logging(
        comum.caminho_logs(Path(args.armazenamento)), "verificacao"
    )
    return verificar_armazenamento(Path(args.armazenamento), logger=log)


if __name__ == "__main__":
    sys.exit(main())
