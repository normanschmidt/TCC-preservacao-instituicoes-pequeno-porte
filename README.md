# Workflow de microserviços de preservação digital — Scripts (Seção 7)

Implementação em Python 3 (Linux) dos microserviços essenciais e do fluxo de
trabalho propostos no TCC, correspondente ao Seção 7 e ao Quadro 7. Cada
script automatiza um microserviço (Seção 5) dentro de uma etapa do workflow
(Seção 6), e o orquestrador encadeia as etapas E1 a E9 tomando decisões com base
no código de retorno de cada microserviço (Seção 6.4).

## Mapa dos scripts (Quadro 7)

| Script                      | Etapa(s)    | Microserviço / função                                  |
| --------------------------- | ----------- | ------------------------------------------------------ |
| `orquestrador.py`           | E1–E9       | Controle do fluxo, encadeamento, logs e decisões       |
| `admissao.py`               | E2          | MS1 — validação de admissão (ClamAV)                   |
| `empacotamento.py`          | E3, E8      | MS6 — empacotamento BagIt (SIP e AIP)                  |
| `identificacao.py`          | E4          | MS2 — identificação de formato (DROID/Siegfried)       |
| `conformidade.py`           | E5          | MS3 — validação de conformidade (JHOVE/veraPDF)        |
| `caracterizacao.py`         | E6, E7      | MS4 — extração de metadados técnicos (FITS/ExifTool)   |
| `derivadas.py`              | E7          | MS9 — geração de derivadas (ImageMagick/LibreOffice/FFmpeg) |
| `eventos.py`                | E2–E10      | MS7 — registro de eventos PREMIS (lxml)                |
| `replicacao.py`             | E9          | MS8 — armazenamento com replicação (rsync/rclone)      |
| `verificacao.py`            | E10         | MS5 — verificação periódica de integridade             |

## Outros arquivos auxiliares 

| Arquivo                     | Etapa(s)    | Microserviço / função                                  |
| --------------------------- | ----------- | ------------------------------------------------------ |
| `comum.py`                  | —           | Módulo auxiliar (logs, subprocess, SHA-256, convenções) |
| `validar_premis.py`         | — (apoio)   | Valida e inspeciona o `metadata/premis.xml` (PREMIS v3) |
| `premis.xsd`                | — (apoio)   | PREMIS Preservation Metadata XML Schema v3.0           |
| `metadados_descritivos.py`  | (inativo)   | Planilha CSV/ODS → Dublin Core XML                     |

## Convenções de diretório (Seção 7.1)

    <nome>-<uuid>/
    │ bagit.txt
    │ manifest-sha256.txt
    │ tagmanifest-sha256.txt
    │ bag-info.txt
    └─ data/
         ├── originais/   (matrizes de preservação)
         └── derivadas/   (representações derivadas da matriz)
    └─ metadata/
         └── premis.xml   (PREMIS v3: object/event/agent/rights, fixidez,
                           formato, conformidade e XML do FITS embutidos por
                           arquivo em <objectCharacteristicsExtension>)
    └─ logs/              (logs de execução do orquestrador e saídas brutas
                           das ferramentas externas — evidência forense)

## Instalação

Resumo; o passo a passo completo (Ubuntu 26.04 LTS, na ordem correta) está em
`INSTALACAO.md`.

```bash
# 1) Bibliotecas Python — em ambiente virtual (Ubuntu 23.04+ exige isso por
#    causa do PEP 668; ver "externally-managed-environment"):
sudo apt install -y python3-venv python3-full
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
#    (sem sudo e sem venv, ver INSTALACAO.md Parte 4.1:
#     pip install --user --break-system-packages -r requirements.txt)

# 2) Utilitários externos — lote do apt (parte das ferramentas):
sudo apt install -y clamav clamav-freshclam libimage-exiftool-perl mediainfo \
                 imagemagick libreoffice ffmpeg rsync rclone hashdeep \
                 libxml2-utils openjdk-21-jre fuse3
# DROID/Siegfried, JHOVE/veraPDF e FITS têm instaladores próprios — ver
# INSTALACAO.md (Parte 3). Configuração do rclone p/ nuvem — INSTALACAO.md 2.2.
```

## Uso

### Fluxo de ingestão (E1–E9)

```bash
python3 orquestrador.py \
    --entrada /caminho/entrada \
    --saida   /caminho/saida \
    --nome    lote_2026_03 \
    --destinos destinos.exemplo.json
```

- `--entrada`: diretório com as matrizes (diretório bruto, ZIP/TAR ou pacote BagIt).
- `--saida`: onde o pacote AIP será criado (subpasta com o identificador único).
- `--nome` (opcional): nome da sessão; recebe um sufixo universalmente único.
- `--destinos` (opcional): JSON com os destinos de replicação 3‑2‑1 (E9).

### Verificação periódica de integridade (E10, via cron)

```bash
python3 verificacao.py --armazenamento /srv/preservacao/aips_primario
```

Exemplo de agendamento semanal (crontab, domingos às 3h). Com venv, aponte para
o Python do ambiente; sem venv, use `/usr/bin/python3` e o `crontab -e` do
usuário dono dos pacotes:

```cron
0 3 * * 0 /opt/workflow/.venv/bin/python /opt/workflow/verificacao.py --armazenamento /srv/preservacao/aips_primario
```

Para verificar AIPs que estão em um remoto na nuvem (ex.: Google Drive via
`rclone`), monte-o antes como sistema de arquivos local — o `verificacao.py` lê
os bytes para recalcular o SHA-256 e não percorre um `gdrive:...` diretamente
(ver INSTALACAO.md 2.2):

```bash
rclone mount gdrive:workflowtcc/aips ~/gdrive-aips --vfs-cache-mode full --daemon
python3 verificacao.py --armazenamento ~/gdrive-aips
fusermount -u ~/gdrive-aips
```

### Execução isolada de um microserviço

Cada script também é executável de forma independente (testes isolados,
Seção 7), p. ex.:

```bash
python3 admissao.py   --trabalho ./copia --quarentena ./quarentena --pacote ./pac --objeto obj1
python3 identificacao.py --sip ./pac --pacote ./pac --objeto obj1 --ferramenta siegfried
python3 eventos.py    --pacote ./pac --objeto obj1 --tipo "fixity check" --resultado success
```

## Pontos de decisão (Seção 6.4)

1. **E2** (admissão): material infectado → movido para quarentena transitória
   (em `_trabalho/`, fora do AIP) + evento PREMIS de rejeição → fluxo
   interrompido (não se preserva conteúdo malicioso).
2. **E3** (SIP): falha de validação/constituição → evento PREMIS de falha → fluxo
   interrompido; material permanece na entrada.
3. **E9** (replicação): destino inalcançável, sem permissão de escrita ou falha
   de transferência → evento PREMIS de falha do destino + alerta (relativo ao
   destino, sem interromper os demais). A integridade de cada cópia é garantida
   pelo modo checksum do `rsync` (`-c`) e pelo `rclone check`; ao encerramento,
   o fonte finalizado é re-sincronizado para os destinos, deixando as cópias
   idênticas e completas (ver Seção 7.2/7.10 do TCC).

## Códigos de retorno

Padronizados em `comum.py` para que o orquestrador decida o avanço do fluxo:
`0` sucesso · `1` falha (interrompe) · `2` alerta (não interrompe).

## Observações

- Os scripts degradam graciosamente quando uma ferramenta tem alternativa
  (ex.: DROID → Siegfried; FITS → ExifTool+MediaInfo) e registram o ocorrido.
- Os metadados de preservação convergem para `metadata/premis.xml` (esquema
  container do PREMIS v3): para cada arquivo do payload existe um
  `<premis:object xsi:type="premis:file">` com `objectIdentifier`, `fixity`
  (SHA-256), `size`, `format` (PUID PRONOM), `significantProperties`
  (wellFormed, valid) e `objectCharacteristicsExtension` (XML do FITS
  embutido). Derivadas trazem `<premis:relationship>` apontando para sua
  matriz. Todos os eventos de preservação acumulam-se no mesmo arquivo,
  integrando a rastreabilidade ao objeto (Seções 5.5 e 5.7).
- A saída bruta de cada ferramenta externa é preservada em `logs/`, como
  evidência forense complementar aos eventos PREMIS — em conformidade com a
  prática de sistemas como o Archivematica. Em particular: `admissao_*.log`
  (ClamAV), `identificacao_*.csv|.json` (DROID/Siegfried), `conformidade_*/`
  (relatórios XML do JHOVE/veraPDF por arquivo), `caracterizacao_*.log`
  (lista de invocações — o XML do FITS já está em PREMIS), `derivadas_*.log`
  (ImageMagick/LibreOffice/FFmpeg), `replicacao_*/` (rsync/rclone por
  destino) e `verificacao_*.log` (histórico de verificações periódicas).
