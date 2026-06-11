#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validar_premis.py — Valida e inspeciona o metadata/premis.xml de um pacote.

Ferramenta de apoio (não é uma etapa do workflow). Faz duas coisas:

  1. VALIDAÇÃO
     - boa-formação XML (o documento é um XML válido?);
     - validação contra o esquema oficial PREMIS v3 (XSD), quando um XSD local
       é informado por --xsd ou quando há acesso à internet para baixá-lo do
       site da Library of Congress.

  2. INSPEÇÃO
     - resumo legível do conteúdo: objetos por tipo (representação/arquivo),
       características técnicas por arquivo (fixidez, tamanho, formato/PUID,
       conformidade wellFormed/valid, presença do XML de caracterização),
       relacionamentos, eventos (por tipo e resultado), agentes e direitos.

Uso:
    # aponta diretamente para o arquivo:
    python3 validar_premis.py caminho/do/AIP/metadata/premis.xml

    # ou aponta para a raiz do AIP (procura metadata/premis.xml):
    python3 validar_premis.py caminho/do/AIP

    # com XSD local (validação offline, mais robusta):
    python3 validar_premis.py caminho/do/AIP --xsd premis.xsd

O XSD oficial do PREMIS v3 pode ser baixado uma vez de:
    https://www.loc.gov/standards/premis/v3/premis.xsd
(ele importa http://www.w3.org/2001/03/xml.xsd; com acesso à internet, o
lxml resolve essa importação automaticamente durante a validação.)
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path

from lxml import etree

PREMIS_NS = "http://www.loc.gov/premis/v3"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
NS = {"p": PREMIS_NS}
XSD_URL = "https://www.loc.gov/standards/premis/v3/premis.xsd"


def _q(tag: str) -> str:
    return f"{{{PREMIS_NS}}}{tag}"


def _resolver_arquivo(caminho: Path) -> Path:
    """Aceita o premis.xml diretamente ou a raiz do AIP (procura metadata/)."""
    caminho = Path(caminho)
    if caminho.is_dir():
        candidato = caminho / "metadata" / "premis.xml"
        if candidato.exists():
            return candidato
        raise FileNotFoundError(
            f"Não encontrei metadata/premis.xml em {caminho}"
        )
    return caminho


# --------------------------------------------------------------------------- #
# Validação
# --------------------------------------------------------------------------- #

def validar(arvore: etree._ElementTree, xsd: Path | None) -> bool:
    """Valida contra o XSD do PREMIS v3. Retorna True se válido (ou se a
    validação por esquema não pôde ser realizada, apenas avisando)."""
    fonte_xsd: Path | None = xsd
    temporario: Path | None = None

    if fonte_xsd is None:
        # Tenta baixar o XSD oficial para um arquivo temporário.
        try:
            print(f"  Baixando XSD do PREMIS v3 de {XSD_URL} ...")
            with tempfile.NamedTemporaryFile(
                suffix=".xsd", delete=False
            ) as tmp:
                temporario = Path(tmp.name)
            urllib.request.urlretrieve(XSD_URL, temporario)
            fonte_xsd = temporario
        except Exception as e:
            print(f"  [aviso] Não foi possível baixar o XSD ({e}).")
            print("  [aviso] Pulei a validação por esquema. Informe um XSD "
                  "local com --xsd para validar offline.")
            return True

    try:
        esquema = etree.XMLSchema(etree.parse(str(fonte_xsd)))
    except Exception as e:
        print(f"  [aviso] Não foi possível carregar o XSD ({e}).")
        return True
    finally:
        if temporario is not None:
            temporario.unlink(missing_ok=True)

    if esquema.validate(arvore):
        print("  ✓ Válido contra o esquema PREMIS v3.")
        return True
    print("  ✗ INVÁLIDO contra o esquema PREMIS v3. Erros:")
    for erro in esquema.error_log:
        print(f"    linha {erro.line}: {erro.message}")
    return False


# --------------------------------------------------------------------------- #
# Inspeção
# --------------------------------------------------------------------------- #

def _texto(el, caminho_xpath: str) -> str:
    achado = el.findtext(caminho_xpath, namespaces=NS)
    return achado if achado is not None else ""


def inspecionar(raiz: etree._Element) -> None:
    objetos = raiz.findall(_q("object"))
    eventos = raiz.findall(_q("event"))
    agentes = raiz.findall(_q("agent"))
    direitos = raiz.findall(_q("rights"))

    reps = [o for o in objetos
            if o.get(f"{{{XSI_NS}}}type", "").endswith("representation")]
    arquivos = [o for o in objetos
                if o.get(f"{{{XSI_NS}}}type", "").endswith("file")]

    print("\n=== Objetos ===")
    print(f"  total: {len(objetos)}  "
          f"(representação: {len(reps)}, arquivo: {len(arquivos)})")
    for rep in reps:
        ident = _texto(rep, "p:objectIdentifier/p:objectIdentifierValue")
        print(f"  • [representação] {ident}")

    print("\n=== Arquivos (premis:file) ===")
    for obj in arquivos:
        ident = _texto(obj, "p:objectIdentifier/p:objectIdentifierValue")
        chars = obj.find(_q("objectCharacteristics"))
        flags = []
        if chars is not None:
            if chars.find(_q("fixity")) is not None:
                algo = _texto(chars, "p:fixity/p:messageDigestAlgorithm")
                flags.append(f"fixity={algo}")
            tam = chars.findtext(_q("size"))
            if tam:
                flags.append(f"size={tam}B")
            puid = _texto(chars, "p:format/p:formatRegistry/p:formatRegistryKey")
            nome_fmt = _texto(chars, "p:format/p:formatDesignation/p:formatName")
            if puid or nome_fmt:
                flags.append(f"format={nome_fmt or '?'}"
                             + (f" [{puid}]" if puid else ""))
            wf = obj.findtext(
                "p:objectCharacteristics/p:significantProperties"
                "[p:significantPropertiesType='wellFormed']/"
                "p:significantPropertiesValue", namespaces=NS)
            vl = obj.findtext(
                "p:objectCharacteristics/p:significantProperties"
                "[p:significantPropertiesType='valid']/"
                "p:significantPropertiesValue", namespaces=NS)
            if wf is not None or vl is not None:
                flags.append(f"wellFormed={wf or '?'}, valid={vl or '?'}")
            ext = chars.find(_q("objectCharacteristicsExtension"))
            if ext is not None and len(ext):
                tag_filha = etree.QName(ext[0]).localname
                flags.append(f"caracterizacao=<{tag_filha}>")
        rels = obj.findall(_q("relationship"))
        for r in rels:
            sub = r.findtext(_q("relationshipSubType"))
            alvo = r.findtext(
                "p:relatedObjectIdentifier/p:relatedObjectIdentifierValue",
                namespaces=NS)
            flags.append(f"{sub or 'rel'}→{alvo}")
        print(f"  • {ident}")
        print(f"      {' | '.join(flags) if flags else '(sem características)'}")

    print("\n=== Eventos ===")
    print(f"  total: {len(eventos)}")
    por_tipo = Counter()
    for ev in eventos:
        tipo = ev.findtext(_q("eventType")) or "?"
        resultado = _texto(
            ev, "p:eventOutcomeInformation/p:eventOutcome") or "?"
        por_tipo[(tipo, resultado)] += 1
    for (tipo, resultado), n in sorted(por_tipo.items()):
        print(f"  • {tipo} [{resultado}]: {n}")

    print("\n=== Agentes ===")
    print(f"  total: {len(agentes)}")
    for ag in agentes:
        nome = ag.findtext(_q("agentName")) or "?"
        tipo = ag.findtext(_q("agentType")) or "?"
        print(f"  • {nome} ({tipo})")

    print("\n=== Direitos ===")
    print(f"  total: {len(direitos)}"
          + ("  (placeholder — preencher pela instituição)"
             if direitos else ""))
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Valida e inspeciona um metadata/premis.xml."
    )
    parser.add_argument("alvo",
                        help="Caminho do premis.xml ou da raiz do AIP.")
    parser.add_argument("--xsd", default=None,
                        help="XSD local do PREMIS v3 (validação offline).")
    parser.add_argument("--so-inspecao", action="store_true",
                        help="Pula a validação por esquema.")
    args = parser.parse_args(argv)

    try:
        arquivo = _resolver_arquivo(Path(args.alvo))
    except FileNotFoundError as e:
        print(f"Erro: {e}", file=sys.stderr)
        return 1

    print(f"Arquivo: {arquivo}")
    print("\n=== Validação ===")
    # 1) Boa-formação
    try:
        arvore = etree.parse(str(arquivo))
        print("  ✓ XML bem-formado.")
    except etree.XMLSyntaxError as e:
        print(f"  ✗ XML MAL-FORMADO: {e}")
        return 1

    # 2) Esquema PREMIS v3
    valido = True
    if not args.so_inspecao:
        xsd = Path(args.xsd) if args.xsd else None
        valido = validar(arvore, xsd)

    # 3) Inspeção do conteúdo
    inspecionar(arvore.getroot())

    return 0 if valido else 2


if __name__ == "__main__":
    sys.exit(main())
