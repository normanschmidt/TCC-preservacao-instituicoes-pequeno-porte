#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
comum.py — Módulo auxiliar de utilidades compartilhadas pelos scripts do workflow.

Este módulo NÃO corresponde a uma etapa do workflow (Seção 6) nem a um microserviço
(Seção 5). Ele reúne funções de apoio usadas por todos os scripts descritos no
Quadro 7 — configuração de log, execução de utilitários externos por subprocess,
cálculo de checksums SHA-256 e definição das convenções de diretório estabelecidas
na Seção 7.1 — evitando a repetição dessas rotinas em cada microserviço.

Convenções de diretório (Seção 7.1):

    <nome>-<uuid>/
    │ bagit.txt
    │ manifest-sha256.txt
    │ tagmanifest-sha256.txt
    │ bag-info.txt
    └─ data/
         ├── originais/    (matrizes digitais de preservação)
         └── derivadas/    (representações derivadas)
    └─ metadata/           (PREMIS, Dublin Core e demais metadados)
    │    ├── premis.xml    (entidades object/event/agent/rights)
    │    └── dublincore.xml
    └─ logs/               (log do orquestrador + saídas brutas das
                            ferramentas externas — evidência forense)

Observação sobre o checksum: embora o diagrama da Seção 7.1 mostre, a título de
exemplo, "manifest-md5.txt", o texto da Seção 5.5 estabelece o SHA-256 como
algoritmo padrão do BagIt (RFC 8493). Adota-se aqui o SHA-256.
"""

import hashlib
import logging
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constantes de convenção (Seção 7.1)
# --------------------------------------------------------------------------- #

ALGORITMO_CHECKSUM = "sha256"   # padrão do BagIt (RFC 8493), conforme Seção 5.5

DIR_DADOS = "data"
DIR_ORIGINAIS = "originais"     # data/originais  -> matrizes de preservação
DIR_DERIVADAS = "derivadas"     # data/derivadas  -> representações derivadas
DIR_METADATA = "metadata"       # PREMIS, Dublin Core e demais metadados
DIR_LOGS = "logs"               # logs de execução

ARQUIVO_PREMIS = "premis.xml"             # metadata/premis.xml (todas as entidades)
ARQUIVO_DUBLIN_CORE = "dublincore.xml"    # metadata/dublincore.xml

# Códigos de retorno padronizados, usados pelo orquestrador para tomar decisões
# (Seção 6.4 — pontos de decisão em falhas).
RET_SUCESSO = 0
RET_FALHA = 1
RET_ALERTA = 2     # condição não fatal (ex.: arquivo não reconhecido / não conforme)


# --------------------------------------------------------------------------- #
# Identificação de sessão / objeto
# --------------------------------------------------------------------------- #

def gerar_identificador(nome: str | None = None) -> str:
    """Gera um identificador universalmente único para a sessão/objeto.

    Conforme a Seção 7.1, quando um nome é informado, agrega-se a ele um sufixo
    único separado por hífen (<nome>-<uuid>); quando não, o identificador é
    apenas o sufixo universal gerado.
    """
    sufixo = uuid.uuid4().hex[:12]
    nome = (nome or "").strip().replace(" ", "-")
    return f"{nome}-{sufixo}" if nome else sufixo


def agora_iso() -> str:
    """Retorna o instante atual em ISO 8601 com fuso (para eventos PREMIS)."""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

def configurar_logging(dir_logs: Path, nome_sessao: str) -> logging.Logger:
    """Configura o logging para arquivo (em logs/) e para a saída padrão.

    Cada execução grava um arquivo de log com carimbo de tempo no subdiretório
    'logs', registrando dados cronológicos e a descrição de ocorrências
    (Seção 7.1).
    """
    dir_logs = Path(dir_logs)
    dir_logs.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d-%H%M%S")
    arquivo_log = dir_logs / f"workflow_{nome_sessao}_{carimbo}.log"

    logger = logging.getLogger(nome_sessao)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(arquivo_log, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formato)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formato)
    logger.addHandler(ch)

    logger.info("Log da sessão iniciado em %s", arquivo_log)
    return logger


def obter_logger(nome: str = "workflow") -> logging.Logger:
    """Devolve um logger já configurado ou um logger mínimo de fallback."""
    logger = logging.getLogger(nome)
    if not logger.handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)-8s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    return logger


# --------------------------------------------------------------------------- #
# Execução de ferramentas externas (subprocess)
# --------------------------------------------------------------------------- #

def ferramenta_disponivel(nome: str) -> bool:
    """Verifica se um utilitário externo está disponível no PATH do sistema."""
    return shutil.which(nome) is not None


def executar_comando(
    cmd: list[str],
    logger: logging.Logger | None = None,
    timeout: int | None = None,
    aceitar_codigos: tuple[int, ...] = (0,),
    cwd: Path | str | None = None,
) -> subprocess.CompletedProcess:
    """Executa um comando externo de forma controlada.

    Diversos microserviços (ClamAV, DROID, JHOVE, FITS, ImageMagick, FFmpeg,
    rsync, rclone) integram-se por meio do módulo subprocess da biblioteca
    padrão (Seção 5). Esta função centraliza essa chamada, registrando a linha
    de comando e o resultado no log e tratando a ausência da ferramenta.

    O parâmetro 'cwd' define o diretório de trabalho do processo filho. É
    útil para ferramentas que produzem arquivos colaterais relativos ao seu
    diretório de execução — caso típico do FITS, cujo Log4j interno escreve
    em ./fits.log; redirecionando o cwd para logs/ do AIP, esse arquivo passa
    a ser preservado dentro do pacote, coberto pelo tagmanifest.

    Levanta FileNotFoundError se a ferramenta não estiver instalada e
    RuntimeError se o código de retorno não estiver entre os aceitos.
    """
    log = logger or obter_logger()
    log.debug("Executando: %s", " ".join(str(c) for c in cmd))
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except FileNotFoundError:
        log.error("Ferramenta não encontrada no sistema: %s", cmd[0])
        raise

    if proc.returncode not in aceitar_codigos:
        log.error(
            "Comando '%s' retornou código %d. stderr: %s",
            cmd[0], proc.returncode, proc.stderr.strip(),
        )
        raise RuntimeError(
            f"Falha ao executar {cmd[0]} (código {proc.returncode})"
        )
    return proc


# --------------------------------------------------------------------------- #
# Checksums / integridade (apoio à Seção 5.5)
# --------------------------------------------------------------------------- #

def calcular_sha256(caminho: Path, bloco: int = 1 << 20) -> str:
    """Calcula o checksum SHA-256 de um arquivo usando o módulo hashlib.

    Conforme a Seção 5.5, o cálculo por hashlib dispensa chamada externa e é
    nativo do Python.
    """
    h = hashlib.sha256()
    with open(caminho, "rb") as f:
        for parte in iter(lambda: f.read(bloco), b""):
            h.update(parte)
    return h.hexdigest()


def listar_arquivos(diretorio: Path) -> list[Path]:
    """Lista recursivamente todos os arquivos comuns sob um diretório."""
    return [p for p in Path(diretorio).rglob("*") if p.is_file()]


# --------------------------------------------------------------------------- #
# Estrutura de diretórios do pacote (Seção 7.1)
# --------------------------------------------------------------------------- #

def caminho_metadata(raiz_pacote: Path) -> Path:
    """Retorna (criando, se preciso) o subdiretório 'metadata/' do pacote."""
    p = Path(raiz_pacote) / DIR_METADATA
    p.mkdir(parents=True, exist_ok=True)
    return p


def arquivo_premis(raiz_pacote: Path) -> Path:
    """Retorna o caminho do arquivo único 'metadata/premis.xml' do pacote.

    O PREMIS é mantido em um único documento XML usando o esquema container do
    PREMIS v3, que reúne as entidades object, event, agent e rights em um só
    arquivo. Diferentemente de implementações que dividem cada entidade em
    arquivos separados, esta opção é a esperada por sistemas como o
    Archivematica em pacotes BagIt (ver Seção 7.1).
    """
    return caminho_metadata(raiz_pacote) / ARQUIVO_PREMIS


def arquivo_dublin_core(raiz_pacote: Path) -> Path:
    """Retorna o caminho do arquivo 'metadata/dublincore.xml' do pacote."""
    return caminho_metadata(raiz_pacote) / ARQUIVO_DUBLIN_CORE


def caminho_logs(raiz_pacote: Path) -> Path:
    """Retorna (criando, se preciso) o subdiretório 'logs' do pacote."""
    p = Path(raiz_pacote) / DIR_LOGS
    p.mkdir(parents=True, exist_ok=True)
    return p


# --------------------------------------------------------------------------- #
# Logs brutos das ferramentas externas (Seção 5.7 — evidência forense)
# --------------------------------------------------------------------------- #

def carimbo_tempo() -> str:
    """Carimbo de tempo curto para nomear arquivos de log (YYYYmmdd-HHMMSS)."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def salvar_log_microservico(
    raiz_pacote: Path,
    microservico: str,
    conteudo: str,
    sufixo: str = ".log",
    sub: str | None = None,
    carimbo: str | None = None,
) -> Path:
    """Grava um arquivo de log bruto de microsserviço em logs/.

    Convenções de nome (Seção 7.1):
      - sem sub: logs/<microservico>_<carimbo><sufixo>
      - com sub: logs/<microservico>_<carimbo>/<sub><sufixo>

    Esses logs preservam a saída original (stdout/stderr, XML, CSV, JSON) das
    ferramentas externas — evidência forense complementar aos eventos PREMIS,
    em conformidade com a prática estabelecida por sistemas de preservação
    como o Archivematica, que mantém logs por etapa dentro do AIP.
    """
    if carimbo is None:
        carimbo = carimbo_tempo()
    base = caminho_logs(raiz_pacote)
    if sub:
        dir_ms = base / f"{microservico}_{carimbo}"
        dir_ms.mkdir(parents=True, exist_ok=True)
        arq = dir_ms / f"{sub}{sufixo}"
    else:
        arq = base / f"{microservico}_{carimbo}{sufixo}"
    arq.write_text(conteudo, encoding="utf-8")
    return arq


def salvar_saida_processo(
    raiz_pacote: Path,
    microservico: str,
    proc,
    sufixo: str = ".log",
    sub: str | None = None,
    carimbo: str | None = None,
    cmd: list | None = None,
) -> Path:
    """Grava o stdout, stderr e código de retorno de um CompletedProcess
    (resultado de subprocess.run) como arquivo de log em logs/.

    Útil para preservar a saída original das ferramentas externas (ClamAV,
    DROID, JHOVE, FFmpeg, rsync, etc.) sem mudar a lógica dos microsserviços.
    """
    partes: list[str] = []
    if cmd:
        partes.append("$ " + " ".join(str(c) for c in cmd))
        partes.append("")
    if getattr(proc, "stdout", None):
        partes.append("--- stdout ---")
        partes.append(proc.stdout)
    if getattr(proc, "stderr", None):
        partes.append("--- stderr ---")
        partes.append(proc.stderr)
    partes.append(f"--- exit code: {proc.returncode} ---")
    return salvar_log_microservico(
        raiz_pacote, microservico, "\n".join(partes),
        sufixo=sufixo, sub=sub, carimbo=carimbo,
    )
