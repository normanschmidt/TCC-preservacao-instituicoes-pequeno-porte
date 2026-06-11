#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
empacotamento.py — Microserviço 6: Empacotamento do objeto digital (E3 e E8).

Referência: Seções 5.6, 6.3.1.3 (E3) e 6.3.2.4 (E8) do TCC.

Este microserviço estrutura o objeto digital e seus metadados em um pacote BagIt
(RFC 8493), usando a implementação de referência BagIt-Python (API nativa, sem
subprocess). O algoritmo de checksum é o SHA-256, padrão do BagIt (Seção 5.5).

Atua em dois modos, conforme a etapa:

  Modo SIP (E3):
    - 'constituir': empacota um diretório bruto de matrizes em um pacote BagIt,
      calculando os checksums e gerando os manifestos iniciais (linha de base de
      fixidez). Se uma planilha de metadados descritivos acompanha o material,
      seu Dublin Core XML é incluído antes do cálculo dos manifestos.
    - 'validar': valida um pacote BagIt recebido já nesse formato, comparando os
      checksums dos arquivos presentes com os manifestos do pacote e detectando
      arquivos ausentes, não declarados ou divergentes. Uma falha de validação
      interrompe o fluxo (segundo ponto de decisão, Seção 6.4).

  Modo AIP (E8):
    - reúne matrizes, derivadas, Dublin Core XML, relatórios de identificação,
      pareceres de conformidade e metadados técnicos em um pacote BagIt
      estruturado, recalcula os checksums e atualiza os manifestos, consolidando
      a linha de base de fixidez.

Estrutura do pacote (Seção 7.1): data/originais, data/derivadas (payload BagIt),
mais metadata/ e logs/ como diretórios de tag (cobertos pelo tagmanifest).

Uso (CLI):
    python3 empacotamento.py sip-constituir --origem <dir> --pacote <dir> ...
    python3 empacotamento.py sip-validar   --pacote <bag>
    python3 empacotamento.py aip           --pacote <dir> ...
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

import bagit  # BagIt-Python (implementação de referência, RFC 8493)

import comum
from eventos import RegistradorPREMIS

AGENTE = "BagIt-Python"
AGENTE_VERSAO = getattr(bagit, "VERSION", "1.8")


def _bag_info_base(id_objeto: str, fonte: str) -> dict:
    """Metadados administrativos mínimos do bag-info.txt."""
    return {
        "Source-Organization": "Instituição de memória de pequeno porte",
        "External-Identifier": id_objeto,
        "Internal-Sender-Description": fonte,
        "Bagging-Date": comum.agora_iso(),
    }


def _mover_para_payload(origem: Path, pacote: Path) -> None:
    """Move as matrizes recebidas para data/originais antes de criar o bag.

    O make_bag transforma todo o conteúdo do diretório em payload (data/);
    por isso, organizamos previamente as matrizes em 'originais/'.
    """
    destino = Path(pacote) / comum.DIR_ORIGINAIS
    destino.mkdir(parents=True, exist_ok=True)
    for item in Path(origem).iterdir():
        # Planilhas de metadados não entram como matriz de preservação.
        if item.suffix.lower() in (".csv", ".ods"):
            continue
        alvo = destino / item.name
        if item.is_dir():
            shutil.copytree(item, alvo, dirs_exist_ok=True)
        else:
            shutil.copy2(item, alvo)


def _anexar_tags(pacote: Path, raiz_pacote_externo: Path) -> None:
    """Garante a presença dos diretórios de tag metadata/ e logs/ no bag."""
    comum.caminho_metadata(pacote)
    comum.caminho_logs(pacote)


def _make_bag_preservando_tags(pacote: Path, bag_info: dict) -> bagit.Bag:
    """Cria o bag sem absorver os diretórios de tag (metadata/ e logs/).

    O make_bag transforma todo o conteúdo do diretório em payload (data/). Como
    a estrutura da Seção 7.1 mantém 'metadata/' e 'logs/' como diretórios de tag
    (irmãos de 'data/'), eles são afastados temporariamente antes do make_bag e
    restaurados em seguida, ficando então cobertos pelo tagmanifest no save().

    Em Linux, mover o diretório 'logs/' não interrompe o log em andamento, pois o
    arquivo aberto é referenciado por inode, não por caminho.
    """
    pacote = Path(pacote)
    afastados: dict[str, Path] = {}
    for nome_tag in (comum.DIR_METADATA, comum.DIR_LOGS):
        origem = pacote / nome_tag
        if origem.exists():
            temp = pacote.parent / f".{pacote.name}_{nome_tag}_tag"
            if temp.exists():
                shutil.rmtree(temp)
            shutil.move(str(origem), str(temp))
            afastados[nome_tag] = temp

    bag = bagit.make_bag(
        str(pacote), bag_info=bag_info, checksums=[comum.ALGORITMO_CHECKSUM]
    )

    # Restaura os diretórios de tag como irmãos de data/.
    for nome_tag, temp in afastados.items():
        shutil.move(str(temp), str(pacote / nome_tag))
    return bag


def _popular_objetos_premis(
    pacote: Path, premis: RegistradorPREMIS, subdir: str, logger
) -> int:
    """Cria, no PREMIS, um <premis:object xsi:type="premis:file"> para cada
    arquivo em pacote/data/<subdir>/, registrando sua fixidez (SHA-256) e
    tamanho em <premis:objectCharacteristics>.

    Esta é a base canônica de fixidez por objeto no padrão PREMIS — redundante,
    propositalmente, com o manifest-sha256.txt do BagIt: o manifest é a fixidez
    estrutural do pacote, e o <premis:fixity> é a fixidez do objeto preservado
    (Seção 5.5 e 5.7).
    """
    base = Path(pacote) / comum.DIR_DADOS / subdir
    if not base.exists():
        return 0
    n = 0
    for arq in comum.listar_arquivos(base):
        rel = arq.relative_to(pacote).as_posix()
        premis.obter_ou_criar_objeto_arquivo(rel)
        premis.adicionar_fixidez(rel, comum.calcular_sha256(arq))
        premis.adicionar_tamanho(rel, arq.stat().st_size)
        n += 1
    logger.info("PREMIS: %d objeto(s) por arquivo registrado(s) em data/%s/.",
                n, subdir)
    return n


def _vincular_derivadas_a_matrizes(
    pacote: Path, premis: RegistradorPREMIS, logger
) -> int:
    """Para cada derivada em data/derivadas, identifica a matriz correspondente
    em data/originais (pela convenção de nomes do derivadas.py) e adiciona um
    <premis:relationship> tipo "derivation".

    Heurística: o stem da derivada (com o sufixo opcional "_derivada" removido)
    é casado com o stem de algum arquivo em data/originais/. Quando não há
    correspondência, o vínculo é omitido e um aviso é registrado em log; a
    instituição pode adicionar o relacionamento manualmente, se desejar.
    """
    derivadas_dir = Path(pacote) / comum.DIR_DADOS / comum.DIR_DERIVADAS
    originais_dir = Path(pacote) / comum.DIR_DADOS / comum.DIR_ORIGINAIS
    if not derivadas_dir.exists() or not originais_dir.exists():
        return 0
    matrizes_por_stem: dict[str, Path] = {}
    for m in comum.listar_arquivos(originais_dir):
        matrizes_por_stem.setdefault(m.stem, m)
    n = 0
    for d in comum.listar_arquivos(derivadas_dir):
        stem = d.stem
        if stem.endswith("_derivada"):
            stem = stem[: -len("_derivada")]
        matriz = matrizes_por_stem.get(stem)
        if matriz is None:
            logger.warning(
                "Não foi possível mapear derivada %s para uma matriz; "
                "vínculo PREMIS não criado.", d.name,
            )
            continue
        rel_d = d.relative_to(pacote).as_posix()
        rel_m = matriz.relative_to(pacote).as_posix()
        premis.registrar_relacionamento(rel_d, rel_m,
                                        tipo="derivation", subtipo="has source")
        n += 1
    logger.info("PREMIS: %d relacionamento(s) derivada→matriz registrado(s).", n)
    return n


# --------------------------------------------------------------------------- #
# E3 — Constituição do SIP a partir de diretório bruto
# --------------------------------------------------------------------------- #

def constituir_sip(
    origem: Path,
    pacote: Path,
    id_objeto: str,
    dublin_core: Path | None = None,
    logger=None,
) -> int:
    """Empacota um diretório bruto de matrizes em um SIP BagIt (E3)."""
    log = logger or comum.obter_logger("empacotamento")
    premis = None
    try:
        Path(pacote).mkdir(parents=True, exist_ok=True)
        _mover_para_payload(origem, pacote)

        # Inclui o Dublin Core XML como metadado descritivo do pacote (diretório
        # de tag 'metadata/', coberto pelo tagmanifest), e não como matriz de
        # preservação no payload.
        if dublin_core and Path(dublin_core).exists():
            destino_dc = comum.caminho_metadata(pacote) / "dublincore.xml"
            if Path(dublin_core).resolve() != destino_dc.resolve():
                shutil.copy2(dublin_core, destino_dc)
            log.info("Dublin Core XML incorporado ao SIP (metadata/).")

        # Cria o bag preservando os diretórios de tag (metadata/ e logs/), que
        # não devem ser absorvidos pelo payload (data/).
        bag = _make_bag_preservando_tags(
            pacote, _bag_info_base(id_objeto, "SIP constituído de diretório bruto")
        )

        # O registrador PREMIS é instanciado após o make_bag, de modo que o
        # diretório metadata/ seja criado como tag (irmão de data/) e
        # fique coberto pelo tagmanifest no save subsequente.
        premis = RegistradorPREMIS(pacote, id_objeto, logger=log)

        # Linha de base de fixidez por objeto preservado (PREMIS):
        # cada matriz recebe seu próprio <premis:object> com fixity e size.
        _popular_objetos_premis(pacote, premis, comum.DIR_ORIGINAIS, log)
        premis.persistir()

        premis.registrar_evento(
            tipo="creation", resultado="success",
            detalhe="SIP constituído a partir de diretório bruto; "
                    "linha de base de fixidez (SHA-256) estabelecida em "
                    "<premis:fixity> de cada matriz.",
            agente_software=AGENTE, agente_versao=AGENTE_VERSAO,
        )

        # Garante os diretórios de tag e recalcula manifestos + tagmanifest,
        # incorporando o Dublin Core e o PREMIS recém-gravados.
        _anexar_tags(pacote, pacote)
        bag.save(manifests=True)

        log.info("SIP BagIt constituído com manifestos %s em %s.",
                 comum.ALGORITMO_CHECKSUM, pacote)
        return comum.RET_SUCESSO
    except Exception as e:
        log.error("Falha ao constituir o SIP: %s", e)
        if premis is None:
            premis = RegistradorPREMIS(pacote, id_objeto, logger=log)
        premis.registrar_evento(
            tipo="creation", resultado="failure",
            detalhe=f"Falha na constituição do SIP: {e}",
            agente_software=AGENTE, agente_versao=AGENTE_VERSAO,
        )
        return comum.RET_FALHA


# --------------------------------------------------------------------------- #
# E3 — Validação de SIP já empacotado em BagIt
# --------------------------------------------------------------------------- #

def validar_sip(pacote: Path, id_objeto: str, logger=None) -> int:
    """Valida um pacote BagIt recebido pronto (E3).

    Uma falha de validação (divergência de checksum, arquivo ausente ou
    estrutura inválida) interrompe o fluxo — segundo ponto de decisão (6.4).
    """
    log = logger or comum.obter_logger("empacotamento")
    premis = RegistradorPREMIS(pacote, id_objeto, logger=log)
    try:
        bag = bagit.Bag(str(pacote))
        bag.validate()
        log.info("SIP BagIt validado com sucesso: %s", pacote)
        # Linha de base de fixidez por objeto preservado (PREMIS): mesmo
        # quando o SIP é recebido pronto, registra-se em <premis:object> cada
        # matriz com sua fixidez e tamanho, para que o AIP final tenha PREMIS
        # canônico independentemente da origem.
        _popular_objetos_premis(pacote, premis, comum.DIR_ORIGINAIS, log)
        premis.persistir()
        premis.registrar_evento(
            tipo="fixity check", resultado="success",
            detalhe="Pacote BagIt recebido validado contra seus manifestos.",
            agente_software=AGENTE, agente_versao=AGENTE_VERSAO,
        )
        return comum.RET_SUCESSO
    except bagit.BagValidationError as e:
        log.error("Validação do SIP falhou: %s", e)
        premis.registrar_evento(
            tipo="fixity check", resultado="failure",
            detalhe=f"Falha de validação do pacote BagIt recebido: {e}",
            agente_software=AGENTE, agente_versao=AGENTE_VERSAO,
        )
        return comum.RET_FALHA
    except Exception as e:
        log.error("Erro ao validar o SIP: %s", e)
        premis.registrar_evento(
            tipo="fixity check", resultado="failure",
            detalhe=f"Erro ao abrir/validar o pacote: {e}",
            agente_software=AGENTE, agente_versao=AGENTE_VERSAO,
        )
        return comum.RET_FALHA


# --------------------------------------------------------------------------- #
# E8 — Estruturação do AIP
# --------------------------------------------------------------------------- #

def estruturar_aip(pacote: Path, id_objeto: str, logger=None) -> int:
    """Consolida o pacote como AIP, recalculando checksums e manifestos (E8).

    Pressupõe que as etapas anteriores já depositaram, na estrutura do pacote,
    as matrizes (data/originais), as derivadas (data/derivadas) e os metadados
    (metadata/: identificação, conformidade, técnicos, Dublin Core e PREMIS).
    """
    log = logger or comum.obter_logger("empacotamento")
    premis = RegistradorPREMIS(pacote, id_objeto, logger=log)
    try:
        bag = bagit.Bag(str(pacote))
        # Atualiza metadados administrativos do AIP.
        bag.info["Internal-Sender-Description"] = "AIP consolidado"
        bag.info["Bag-Type"] = "AIP"

        # PREMIS: garante objetos para matrizes (idempotente — apenas reforça,
        # caso a etapa E3 tenha sido pulada/recebida); cria objetos para as
        # derivadas com fixidez e tamanho; e adiciona o relacionamento
        # derivation entre cada derivada e sua matriz de origem.
        _popular_objetos_premis(pacote, premis, comum.DIR_ORIGINAIS, log)
        _popular_objetos_premis(pacote, premis, comum.DIR_DERIVADAS, log)
        _vincular_derivadas_a_matrizes(pacote, premis, log)
        premis.persistir()

        # Recalcula payload e atualiza todos os manifestos (fixidez consolidada).
        _anexar_tags(pacote, pacote)
        bag.save(manifests=True)
        bag.validate()

        # Registra a consolidação e regrava os manifestos, de modo que o próprio
        # evento PREMIS de ingestão passe a integrar o tagmanifest do AIP final.
        premis.registrar_evento(
            tipo="ingestion", resultado="success",
            detalhe="AIP consolidado: matrizes, derivadas e metadados reunidos; "
                    "manifestos SHA-256 atualizados e validados.",
            agente_software=AGENTE, agente_versao=AGENTE_VERSAO,
        )
        bag.save(manifests=True)

        log.info("AIP estruturado e validado: %s", pacote)
        return comum.RET_SUCESSO
    except Exception as e:
        log.error("Falha ao estruturar o AIP: %s", e)
        premis.registrar_evento(
            tipo="ingestion", resultado="failure",
            detalhe=f"Falha na estruturação do AIP: {e}",
            agente_software=AGENTE, agente_versao=AGENTE_VERSAO,
        )
        return comum.RET_FALHA


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="MS6 — Empacotamento BagIt (SIP em E3, AIP em E8)."
    )
    sub = parser.add_subparsers(dest="modo", required=True)

    p1 = sub.add_parser("sip-constituir", help="Constitui SIP de diretório bruto.")
    p1.add_argument("--origem", required=True)
    p1.add_argument("--pacote", required=True)
    p1.add_argument("--objeto", required=True)
    p1.add_argument("--dublin-core", default=None)

    p2 = sub.add_parser("sip-validar", help="Valida SIP BagIt recebido.")
    p2.add_argument("--pacote", required=True)
    p2.add_argument("--objeto", required=True)

    p3 = sub.add_parser("aip", help="Estrutura/consolida o AIP.")
    p3.add_argument("--pacote", required=True)
    p3.add_argument("--objeto", required=True)

    args = parser.parse_args(argv)

    if args.modo == "sip-constituir":
        dc = Path(args.dublin_core) if args.dublin_core else None
        return constituir_sip(Path(args.origem), Path(args.pacote),
                              args.objeto, dublin_core=dc)
    if args.modo == "sip-validar":
        return validar_sip(Path(args.pacote), args.objeto)
    if args.modo == "aip":
        return estruturar_aip(Path(args.pacote), args.objeto)
    return comum.RET_FALHA


if __name__ == "__main__":
    sys.exit(main())
