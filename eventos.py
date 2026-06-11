#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
eventos.py — Microserviço 7: Registro de eventos de preservação (PREMIS).

Etapas atendidas: E2, E3, E4, E5, E6, E7, E8, E9, E10 (função transversal).
Referência: Seções 5.7 e 6.3 do TCC.

Conforme a Seção 5.7, este microserviço documenta toda ação realizada sobre o
objeto digital, vinculando cada ação ao agente que a executou, à data de
ocorrência e ao resultado. A solução adotada é a geração de arquivos XML em
conformidade com o esquema PREMIS por meio de scripts Python com a biblioteca
lxml, sem sistema de gestão dedicado (Quadro 5.7).

Cada evento registra (Seção 5.7):
  - tipo de evento (ingestão, verificação de integridade, identificação de
    formato, validação de conformidade, extração de metadados, transformação,
    armazenamento, rejeição, etc.);
  - identificador do objeto relacionado;
  - data e hora de ocorrência;
  - agente responsável (ferramenta de software com versão e responsável
    institucional);
  - resultado alcançado (sucesso ou falha, com detalhe do erro quando aplicável).

O arquivo PREMIS é gravado em metadata/premis.xml e incorporado ao pacote BagIt
como metadado do objeto, integrando a rastreabilidade ao próprio pacote (Seção
5.7). É adotado o esquema container do PREMIS v3, que reúne todas as entidades
(object, event, agent, rights) em um único documento — opção mais simples e
mais interoperável do que dividir cada entidade em arquivos separados (ver
Seção 7.1).

A entidade <premis:rights> é criada com um placeholder vazio: o workflow não
gera direitos automaticamente, pois a determinação de direitos (licença,
copyright, restrições) é decisão curatorial e cabe à instituição preencher
o elemento quando aplicável.

Uso como biblioteca (forma usual, chamada pelos demais scripts):

    from eventos import RegistradorPREMIS
    premis = RegistradorPREMIS(raiz_pacote, id_objeto="meu_objeto")
    premis.registrar_evento(
        tipo="virus check",
        resultado="success",
        detalhe="Nenhuma ameaça detectada (ClamAV 1.0.5).",
        agente_software="ClamAV", agente_versao="1.0.5",
    )

Uso como CLI (acrescenta um evento avulso a um pacote):

    python3 eventos.py --pacote <pac> --objeto <id> --tipo "fixity check" \\
        --resultado success --detalhe "..." --agente-software hashdeep
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lxml import etree

import comum

# Namespace e localização do esquema PREMIS v3
PREMIS_NS = "http://www.loc.gov/premis/v3"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
PREMIS_XSD = (
    "http://www.loc.gov/premis/v3 "
    "http://www.loc.gov/standards/premis/v3/premis.xsd"
)
NSMAP = {"premis": PREMIS_NS, "xsi": XSI_NS}

# Responsável institucional padrão (substituível por política da instituição)
AGENTE_INSTITUCIONAL_PADRAO = "Instituição de memória (responsável de preservação)"


def _q(tag: str) -> str:
    """Qualifica um nome de elemento com o namespace PREMIS."""
    return f"{{{PREMIS_NS}}}{tag}"


class RegistradorPREMIS:
    """Acumula e persiste eventos PREMIS de um objeto em metadata/premis.xml.

    O documento usa o esquema container do PREMIS v3, com as entidades object,
    event, agent e rights reunidas em um único arquivo XML. Se o arquivo já
    existir (o objeto já passou por etapas anteriores), ele é carregado e os
    novos eventos são anexados, preservando o histórico custodial ininterrupto
    exigido pelo InterPARES (Seções 2.3.7 e 4.1.7).
    """

    def __init__(self, raiz_pacote: Path, id_objeto: str,
                 logger=None) -> None:
        self.raiz_pacote = Path(raiz_pacote)
        self.id_objeto = id_objeto
        self.logger = logger or comum.obter_logger("eventos")
        self.arquivo = comum.arquivo_premis(self.raiz_pacote)
        self._contador_evento = 0
        self.raiz = self._carregar_ou_criar()

    # ----- montagem do documento ------------------------------------------ #

    def _carregar_ou_criar(self) -> etree._Element:
        if self.arquivo.exists():
            try:
                arvore = etree.parse(str(self.arquivo))
                raiz = arvore.getroot()
                self._contador_evento = len(raiz.findall(_q("event")))
                self.logger.debug("PREMIS existente carregado (%d eventos).",
                                  self._contador_evento)
                return raiz
            except etree.XMLSyntaxError:
                self.logger.warning("PREMIS existente inválido; recriando.")

        raiz = etree.Element(_q("premis"), nsmap=NSMAP, version="3.0")
        raiz.set(f"{{{XSI_NS}}}schemaLocation", PREMIS_XSD)
        self._adicionar_objeto_representacao(raiz)
        self._adicionar_rights_placeholder(raiz)
        return raiz

    def _adicionar_objeto_representacao(self, raiz: etree._Element) -> None:
        """Cria a entidade <object xsi:type="premis:representation"> que
        representa o pacote como um todo (a "representação" no sentido PREMIS).
        Cada arquivo do pacote será representado por seu próprio <object> do
        tipo premis:file, vinculado a esta representação."""
        obj = etree.SubElement(raiz, _q("object"))
        obj.set(f"{{{XSI_NS}}}type", "premis:representation")
        ident = etree.SubElement(obj, _q("objectIdentifier"))
        etree.SubElement(ident, _q("objectIdentifierType")).text = "local"
        etree.SubElement(ident, _q("objectIdentifierValue")).text = self.id_objeto

    def _adicionar_rights_placeholder(self, raiz: etree._Element) -> None:
        """Cria a entidade <rights> como placeholder.

        O workflow não gera direitos automaticamente; este elemento existe para
        sinalizar o ponto em que a instituição pode incorporar um ou mais
        <premis:rightsStatement> conforme sua política de direitos.
        """
        rights = etree.SubElement(raiz, _q("rights"))
        rights.append(etree.Comment(
            " Placeholder: a ser preenchido pela instituição com um ou mais "
            "<premis:rightsStatement> conforme sua política de direitos "
            "(licença, copyright, statute, donor, policy ou other). "
        ))

    # ----- registro de eventos -------------------------------------------- #

    def registrar_evento(
        self,
        tipo: str,
        resultado: str,
        detalhe: str = "",
        agente_software: str = "",
        agente_versao: str = "",
        agente_institucional: str = AGENTE_INSTITUCIONAL_PADRAO,
        data_hora: str | None = None,
    ) -> None:
        """Acrescenta um <event> ao documento PREMIS e o persiste em disco.

        Parâmetros principais:
          tipo       — eventType (ex.: "virus check", "format identification").
          resultado  — eventOutcome ("success" ou "failure").
          detalhe    — eventOutcomeDetailNote (mensagem do utilitário, erro etc.).
          agente_*   — ferramenta de software, versão e responsável institucional.
        """
        self._contador_evento += 1
        data_hora = data_hora or comum.agora_iso()

        evento = etree.SubElement(self.raiz, _q("event"))

        ev_id = etree.SubElement(evento, _q("eventIdentifier"))
        etree.SubElement(ev_id, _q("eventIdentifierType")).text = "local"
        etree.SubElement(ev_id, _q("eventIdentifierValue")).text = (
            f"{self.id_objeto}-evt-{self._contador_evento:04d}"
        )

        etree.SubElement(evento, _q("eventType")).text = tipo
        etree.SubElement(evento, _q("eventDateTime")).text = data_hora

        outcome = etree.SubElement(evento, _q("eventOutcomeInformation"))
        etree.SubElement(outcome, _q("eventOutcome")).text = resultado
        if detalhe:
            det = etree.SubElement(outcome, _q("eventOutcomeDetail"))
            etree.SubElement(det, _q("eventOutcomeDetailNote")).text = detalhe

        # Agente de software (ferramenta + versão)
        if agente_software:
            nome_agente = agente_software
            if agente_versao:
                nome_agente = f"{agente_software} {agente_versao}"
            self._vincular_agente(evento, "software", nome_agente)

        # Agente institucional (responsável humano/organizacional)
        if agente_institucional:
            self._vincular_agente(evento, "organization", agente_institucional)

        # Vínculo com o objeto relacionado ao evento
        link_obj = etree.SubElement(evento, _q("linkingObjectIdentifier"))
        etree.SubElement(link_obj, _q("linkingObjectIdentifierType")).text = "local"
        etree.SubElement(link_obj, _q("linkingObjectIdentifierValue")).text = (
            self.id_objeto
        )

        self._persistir()
        self.logger.info("Evento PREMIS registrado: %s (%s).", tipo, resultado)

    def _vincular_agente(self, evento, papel: str, valor: str) -> None:
        link = etree.SubElement(evento, _q("linkingAgentIdentifier"))
        etree.SubElement(link, _q("linkingAgentIdentifierType")).text = "local"
        etree.SubElement(link, _q("linkingAgentIdentifierValue")).text = valor
        etree.SubElement(link, _q("linkingAgentRole")).text = papel
        self._garantir_agente(valor, papel)

    def _garantir_agente(self, valor: str, papel: str) -> None:
        """Cria a entidade <agent> uma única vez por valor de identificador."""
        for ag in self.raiz.findall(_q("agent")):
            v = ag.find(f"{_q('agentIdentifier')}/{_q('agentIdentifierValue')}")
            if v is not None and v.text == valor:
                return
        agente = etree.SubElement(self.raiz, _q("agent"))
        ident = etree.SubElement(agente, _q("agentIdentifier"))
        etree.SubElement(ident, _q("agentIdentifierType")).text = "local"
        etree.SubElement(ident, _q("agentIdentifierValue")).text = valor
        etree.SubElement(agente, _q("agentName")).text = valor
        etree.SubElement(agente, _q("agentType")).text = papel

    # ----- gestão de objetos por arquivo (premis:file) -------------------- #

    def _encontrar_objeto_arquivo(self, rel_path: str) -> etree._Element | None:
        """Retorna o <premis:object xsi:type="premis:file"> de um arquivo pelo
        seu caminho relativo, ou None se ainda não tiver sido criado."""
        for obj in self.raiz.findall(_q("object")):
            tipo = obj.get(f"{{{XSI_NS}}}type", "")
            if not tipo.endswith("file"):
                continue
            val = obj.find(f"{_q('objectIdentifier')}/{_q('objectIdentifierValue')}")
            if val is not None and val.text == rel_path:
                return obj
        return None

    def obter_ou_criar_objeto_arquivo(
        self, rel_path: str, original_name: str | None = None,
    ) -> etree._Element:
        """Cria (se ainda não existir) um <premis:object xsi:type="premis:file">
        para um arquivo do pacote, identificado pelo seu caminho relativo à raiz
        do pacote (ex.: 'data/originais/doc01.tif').

        Cada arquivo preservado — matrizes e derivadas — é representado por um
        objeto PREMIS independente, com sua própria seção <objectCharacteristics>
        que reúne identidade, fixidez, formato (MS2), conformidade (MS3) e
        caracterização técnica (MS4).
        """
        obj = self._encontrar_objeto_arquivo(rel_path)
        if obj is not None:
            return obj
        obj = etree.SubElement(self.raiz, _q("object"))
        obj.set(f"{{{XSI_NS}}}type", "premis:file")
        ident = etree.SubElement(obj, _q("objectIdentifier"))
        etree.SubElement(ident, _q("objectIdentifierType")).text = "local"
        etree.SubElement(ident, _q("objectIdentifierValue")).text = rel_path
        chars = etree.SubElement(obj, _q("objectCharacteristics"))
        etree.SubElement(chars, _q("compositionLevel")).text = "0"
        etree.SubElement(obj, _q("originalName")).text = (
            original_name or Path(rel_path).name
        )
        return obj

    def _obter_caracteristicas(self, rel_path: str) -> etree._Element:
        """Retorna o <objectCharacteristics> do arquivo, criando-o se preciso."""
        obj = self.obter_ou_criar_objeto_arquivo(rel_path)
        chars = obj.find(_q("objectCharacteristics"))
        if chars is None:
            chars = etree.SubElement(obj, _q("objectCharacteristics"))
            etree.SubElement(chars, _q("compositionLevel")).text = "0"
        return chars

    def adicionar_fixidez(self, rel_path: str, sha256: str) -> None:
        """Acrescenta um <premis:fixity> com o checksum SHA-256 do arquivo."""
        chars = self._obter_caracteristicas(rel_path)
        for f in chars.findall(_q("fixity")):
            chars.remove(f)
        fixity = etree.SubElement(chars, _q("fixity"))
        etree.SubElement(fixity, _q("messageDigestAlgorithm")).text = "SHA-256"
        etree.SubElement(fixity, _q("messageDigest")).text = sha256

    def adicionar_tamanho(self, rel_path: str, tamanho_bytes: int) -> None:
        """Define o <premis:size> (em bytes) do arquivo."""
        chars = self._obter_caracteristicas(rel_path)
        size_elem = chars.find(_q("size"))
        if size_elem is None:
            size_elem = etree.SubElement(chars, _q("size"))
        size_elem.text = str(tamanho_bytes)

    def registrar_formato(
        self, rel_path: str, puid: str = "", nome: str = "", versao: str = "",
    ) -> None:
        """Adiciona o <premis:format> do arquivo, com registro PRONOM (MS2)."""
        chars = self._obter_caracteristicas(rel_path)
        for f in chars.findall(_q("format")):
            chars.remove(f)
        fmt = etree.SubElement(chars, _q("format"))
        desig = etree.SubElement(fmt, _q("formatDesignation"))
        etree.SubElement(desig, _q("formatName")).text = nome or "unknown"
        if versao:
            etree.SubElement(desig, _q("formatVersion")).text = versao
        if puid:
            reg = etree.SubElement(fmt, _q("formatRegistry"))
            etree.SubElement(reg, _q("formatRegistryName")).text = "PRONOM"
            etree.SubElement(reg, _q("formatRegistryKey")).text = puid

    def registrar_conformidade(
        self, rel_path: str, well_formed: bool | None, valid: bool | None,
    ) -> None:
        """Adiciona <premis:significantProperties> com wellFormed/valid (MS3)."""
        chars = self._obter_caracteristicas(rel_path)
        # idempotência: remove propriedades anteriores de mesmo tipo
        for sp in list(chars.findall(_q("significantProperties"))):
            t = sp.find(_q("significantPropertiesType"))
            if t is not None and t.text in ("wellFormed", "valid"):
                chars.remove(sp)
        for nome, val in (("wellFormed", well_formed), ("valid", valid)):
            if val is None:
                continue
            sp = etree.SubElement(chars, _q("significantProperties"))
            etree.SubElement(sp, _q("significantPropertiesType")).text = nome
            etree.SubElement(sp, _q("significantPropertiesValue")).text = (
                "true" if val else "false"
            )

    def registrar_caracterizacao(
        self, rel_path: str, xml_elemento: etree._Element,
        aplicacao: str = "", versao_aplicacao: str = "",
    ) -> None:
        """Embute o XML extraído pela caracterização (FITS, ExifTool, MediaInfo)
        em <premis:objectCharacteristicsExtension> do arquivo (MS4).

        Quando informados, aplicacao/versao_aplicacao são registrados em
        <premis:creatingApplication> da mesma seção objectCharacteristics —
        para PREMIS, "creatingApplication" é a ferramenta que produziu o objeto;
        aqui registramos a ferramenta de caracterização que extraiu os metadados
        técnicos como informação de proveniência da própria caracterização.
        """
        import copy as _copy
        chars = self._obter_caracteristicas(rel_path)
        if aplicacao:
            for ca in chars.findall(_q("creatingApplication")):
                chars.remove(ca)
            ca = etree.SubElement(chars, _q("creatingApplication"))
            etree.SubElement(ca, _q("creatingApplicationName")).text = aplicacao
            if versao_aplicacao:
                etree.SubElement(
                    ca, _q("creatingApplicationVersion")
                ).text = versao_aplicacao
        for ext in chars.findall(_q("objectCharacteristicsExtension")):
            chars.remove(ext)
        ext = etree.SubElement(chars, _q("objectCharacteristicsExtension"))
        ext.append(_copy.deepcopy(xml_elemento))

    def registrar_relacionamento(
        self, rel_path: str, rel_path_relacionado: str,
        tipo: str = "derivation", subtipo: str = "has source",
    ) -> None:
        """Vincula um objeto a outro via <premis:relationship> (ex.: uma derivada
        à sua matriz, com tipo='derivation' e subtipo='has source')."""
        obj = self.obter_ou_criar_objeto_arquivo(rel_path)
        # idempotência: não duplica vínculo igual já existente
        for r in obj.findall(_q("relationship")):
            t = r.find(_q("relationshipType"))
            s = r.find(_q("relationshipSubType"))
            v = r.find(f"{_q('relatedObjectIdentifier')}/"
                       f"{_q('relatedObjectIdentifierValue')}")
            if (t is not None and t.text == tipo
                    and s is not None and s.text == subtipo
                    and v is not None and v.text == rel_path_relacionado):
                return
        rel = etree.SubElement(obj, _q("relationship"))
        etree.SubElement(rel, _q("relationshipType")).text = tipo
        etree.SubElement(rel, _q("relationshipSubType")).text = subtipo
        ident = etree.SubElement(rel, _q("relatedObjectIdentifier"))
        etree.SubElement(ident, _q("relatedObjectIdentifierType")).text = "local"
        etree.SubElement(ident, _q("relatedObjectIdentifierValue")).text = (
            rel_path_relacionado
        )

    def persistir(self) -> None:
        """Força a gravação do documento PREMIS em disco.

        As operações por arquivo (adicionar_fixidez, registrar_formato, etc.)
        não persistem automaticamente — espera-se que o script chame este
        método uma única vez ao final do lote, evitando dezenas de gravações
        em arquivos com muitos componentes.
        """
        self._persistir()

    def _persistir(self) -> None:
        arvore = etree.ElementTree(self.raiz)
        arvore.write(
            str(self.arquivo),
            pretty_print=True,
            xml_declaration=True,
            encoding="UTF-8",
        )

    @property
    def caminho(self) -> Path:
        return self.arquivo


# --------------------------------------------------------------------------- #
# Interface de linha de comando
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Registra um evento PREMIS avulso em um pacote (MS7)."
    )
    parser.add_argument("--pacote", required=True, help="Raiz do pacote BagIt.")
    parser.add_argument("--objeto", required=True, help="Identificador do objeto.")
    parser.add_argument("--tipo", required=True, help="Tipo de evento (eventType).")
    parser.add_argument("--resultado", default="success",
                        choices=["success", "failure"], help="eventOutcome.")
    parser.add_argument("--detalhe", default="", help="Detalhe do resultado.")
    parser.add_argument("--agente-software", default="", help="Ferramenta.")
    parser.add_argument("--agente-versao", default="", help="Versão da ferramenta.")
    args = parser.parse_args(argv)

    premis = RegistradorPREMIS(Path(args.pacote), id_objeto=args.objeto)
    premis.registrar_evento(
        tipo=args.tipo,
        resultado=args.resultado,
        detalhe=args.detalhe,
        agente_software=args.agente_software,
        agente_versao=args.agente_versao,
    )
    print(f"Evento registrado em {premis.caminho}")
    return comum.RET_SUCESSO


if __name__ == "__main__":
    sys.exit(main())
