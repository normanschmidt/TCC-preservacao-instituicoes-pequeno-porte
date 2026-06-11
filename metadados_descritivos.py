#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
metadados_descritivos.py — Apoio à etapa E3: coleta de metadados descritivos.

Referência: Seção 5.1.1 do TCC.

No cenário de instituições de pequeno porte, a coleta de metadados descritivos é
feita por uma planilha em formato aberto (CSV ou ODS). Este script lê essa
planilha e gera um arquivo Dublin Core XML, que será incorporado ao pacote BagIt
pelo microserviço de empacotamento (E3) antes do cálculo dos manifestos.

Padrão de metadados: Dublin Core (DUBLIN CORE METADATA INITIATIVE, 2020). Os
elementos considerados essenciais para acervos de objetos digitalizados são:
    título (dc:title), criador (dc:creator), data (dc:date),
    descrição (dc:description), tipo (dc:type), idioma (dc:language) e
    identificador (dc:identifier).

A planilha deve ter uma linha por item digitalizado e colunas cujos nomes
correspondam (em português ou inglês) aos elementos Dublin Core. Uma coluna
"arquivo" pode ser usada para associar cada registro ao seu arquivo de matriz.

Leitura: CSV pelo módulo csv da biblioteca padrão; ODS pela biblioteca odfpy.
Geração do XML: biblioteca lxml.

Uso (CLI):
    python3 metadados_descritivos.py --planilha meta.csv --saida dublincore.xml
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from lxml import etree

import comum

DC_NS = "http://purl.org/dc/elements/1.1/"
NSMAP = {"dc": DC_NS}

# Elementos Dublin Core essenciais e sinônimos de coluna aceitos (pt/en)
MAPA_COLUNAS = {
    "title":       ["title", "titulo", "título", "dc:title"],
    "creator":     ["creator", "criador", "autor", "dc:creator"],
    "date":        ["date", "data", "dc:date"],
    "description": ["description", "descricao", "descrição", "dc:description"],
    "type":        ["type", "tipo", "dc:type"],
    "language":    ["language", "idioma", "lingua", "língua", "dc:language"],
    "identifier":  ["identifier", "identificador", "id", "dc:identifier"],
}
COLUNA_ARQUIVO = ["arquivo", "file", "filename", "nome_arquivo"]


def _normalizar(cabecalho: list[str]) -> dict[str, str]:
    """Mapeia os nomes de coluna da planilha para os elementos Dublin Core."""
    mapeado: dict[str, str] = {}
    cab_lower = {c.strip().lower(): c for c in cabecalho}
    for elemento, sinonimos in MAPA_COLUNAS.items():
        for s in sinonimos:
            if s in cab_lower:
                mapeado[elemento] = cab_lower[s]
                break
    for s in COLUNA_ARQUIVO:
        if s in cab_lower:
            mapeado["__arquivo__"] = cab_lower[s]
            break
    return mapeado


def _ler_csv(caminho: Path) -> tuple[list[str], list[dict]]:
    with open(caminho, newline="", encoding="utf-8-sig") as f:
        amostra = f.read(2048)
        f.seek(0)
        try:
            dialeto = csv.Sniffer().sniff(amostra, delimiters=",;\t")
        except csv.Error:
            dialeto = csv.excel
        leitor = csv.DictReader(f, dialect=dialeto)
        linhas = list(leitor)
        return (leitor.fieldnames or [], linhas)


def _ler_ods(caminho: Path) -> tuple[list[str], list[dict]]:
    """Lê a primeira planilha de um arquivo ODS usando odfpy."""
    try:
        from odf.opendocument import load
        from odf.table import Table, TableRow, TableCell
        from odf.text import P
    except ImportError as e:
        raise RuntimeError(
            "Leitura de ODS requer a biblioteca 'odfpy' "
            "(pip install odfpy)."
        ) from e

    doc = load(str(caminho))
    tabela = doc.spreadsheet.getElementsByType(Table)[0]
    linhas_brutas = tabela.getElementsByType(TableRow)

    def texto_celula(celula) -> str:
        partes = [str(p) for p in celula.getElementsByType(P)]
        return " ".join(partes).strip()

    def expandir(linha) -> list[str]:
        valores: list[str] = []
        for celula in linha.getElementsByType(TableCell):
            repeticao = int(
                celula.getAttribute("numbercolumnsrepeated") or 1
            )
            valores.extend([texto_celula(celula)] * repeticao)
        return valores

    if not linhas_brutas:
        return ([], [])
    cabecalho = expandir(linhas_brutas[0])
    registros: list[dict] = []
    for linha in linhas_brutas[1:]:
        valores = expandir(linha)
        if not any(valores):
            continue
        registro = {cabecalho[i]: (valores[i] if i < len(valores) else "")
                    for i in range(len(cabecalho))}
        registros.append(registro)
    return (cabecalho, registros)


def gerar_dublin_core(planilha: Path, saida: Path, logger=None) -> int:
    """Lê a planilha CSV/ODS e gera o arquivo Dublin Core XML.

    Cada linha vira um elemento <record> contendo os elementos Dublin Core
    preenchidos. Retorna comum.RET_SUCESSO em caso de êxito.
    """
    log = logger or comum.obter_logger("metadados_descritivos")
    planilha = Path(planilha)
    if not planilha.exists():
        log.error("Planilha não encontrada: %s", planilha)
        return comum.RET_FALHA

    sufixo = planilha.suffix.lower()
    if sufixo == ".csv":
        cabecalho, linhas = _ler_csv(planilha)
    elif sufixo == ".ods":
        cabecalho, linhas = _ler_ods(planilha)
    else:
        log.error("Formato de planilha não suportado: %s", sufixo)
        return comum.RET_FALHA

    mapa = _normalizar(cabecalho)
    if not mapa:
        log.warning("Nenhuma coluna Dublin Core reconhecida em %s.", planilha)

    raiz = etree.Element("metadados_descritivos", nsmap=NSMAP)
    for linha in linhas:
        registro = etree.SubElement(raiz, "record")
        coluna_arq = mapa.get("__arquivo__")
        if coluna_arq and linha.get(coluna_arq):
            registro.set("arquivo", str(linha[coluna_arq]).strip())
        for elemento in MAPA_COLUNAS:
            coluna = mapa.get(elemento)
            valor = (linha.get(coluna) or "").strip() if coluna else ""
            if valor:
                el = etree.SubElement(registro, f"{{{DC_NS}}}{elemento}")
                el.text = valor

    Path(saida).parent.mkdir(parents=True, exist_ok=True)
    etree.ElementTree(raiz).write(
        str(saida), pretty_print=True, xml_declaration=True, encoding="UTF-8"
    )
    log.info("Dublin Core XML gerado com %d registro(s): %s",
             len(linhas), saida)
    return comum.RET_SUCESSO


def localizar_planilha(diretorio: Path) -> Path | None:
    """Procura, na área de trabalho, uma planilha de metadados (CSV ou ODS)."""
    for ext in ("*.csv", "*.ods"):
        candidatos = sorted(Path(diretorio).rglob(ext))
        if candidatos:
            return candidatos[0]
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Apoio a E3 — gera Dublin Core XML a partir de planilha."
    )
    parser.add_argument("--planilha", required=True, help="Planilha CSV ou ODS.")
    parser.add_argument("--saida", required=True, help="Arquivo XML de saída.")
    args = parser.parse_args(argv)
    return gerar_dublin_core(Path(args.planilha), Path(args.saida))


if __name__ == "__main__":
    sys.exit(main())
