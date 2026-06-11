# Guia de instalação do workflow de preservação (Ubuntu 26.04 LTS)

Guia único de instalação de todas as ferramentas externas usadas pelos
microsserviços do workflow, complementar ao `README.md` (que descreve o uso). As
instruções assumem um usuário comum com acesso a `sudo`, em ambiente de testes
Ubuntu 26.04 LTS. O **Apêndice A** traz as variantes sem privilégios (sem `sudo`).

A sequência abaixo está na ordem mais adequada para instalar tudo: primeiro a
dependência compartilhada (Java, exigido por DROID, JHOVE, veraPDF e FITS), depois
as ferramentas empacotadas (`apt`) em lote único, em seguida as ferramentas com
instalador próprio (download) e, por fim, as atualizações de base/assinaturas e a
verificação final.

| Ferramenta | Etapa / MS | Comando | Método |
| --- | --- | --- | --- |
| (Java 21) | dependência | `java` | apt (`openjdk-21-jre`) |
| ClamAV | E2 / MS1 | `clamscan` | apt + base de assinaturas |
| ExifTool | E6,E7 / MS4 (alt.) | `exiftool` | apt |
| MediaInfo | E6,E7 / MS4 (alt.) | `mediainfo` | apt |
| ImageMagick | E7 / MS9 | `convert` / `magick` | apt |
| LibreOffice | E7 / MS9 | `soffice` | apt |
| FFmpeg | E7 / MS9 | `ffmpeg` | apt |
| rsync | E9 / MS8 | `rsync` | apt |
| rclone | E9 / MS8 | `rclone` | apt (ou instalador oficial) |
| hashdeep | E10 / MS5 | `hashdeep` | apt (reforço opcional) |
| DROID | E4 / MS2 | `droid` | ZIP (TNA) — exige Java |
| Siegfried | E4 / MS2 (alt.) | `sf` | repositório apt |
| JHOVE | E5 / MS3 | `jhove` | instalador Java (IzPack) |
| veraPDF | E5 / MS3 (alt.) | `verapdf` | instalador Java (IzPack) |
| FITS | E6,E7 / MS4 | `fits` | ZIP (Harvard) — exige Java |

> Em rede com proxy, exporte `http_proxy`/`https_proxy` antes dos comandos que
> baixam assinaturas, bases de dados ou instaladores.

---

## Parte 1 — Preparação e dependência de Java

```bash
sudo apt update
```

DROID, JHOVE, veraPDF e FITS são aplicações Java. O DROID 6.8.1 exige **Java 21**
(versões até a 6.8.0 funcionam com Java 8 a 17); o Java 21 atende a todas as
quatro. Instale-o uma única vez:

```bash
sudo apt install -y openjdk-21-jre
java -version          # deve indicar "openjdk version 21"
```

> Para um ambiente apenas de linha de comando, `openjdk-21-jre-headless` é
> suficiente.

---

## Parte 2 — Ferramentas empacotadas (apt), em lote

As ferramentas a seguir vêm dos repositórios do Ubuntu e podem ser instaladas de
uma só vez:

```bash
sudo apt install -y clamav clamav-freshclam \
  libimage-exiftool-perl mediainfo imagemagick libreoffice ffmpeg \
  rsync rclone hashdeep libxml2-utils
```

A maior parte fica pronta após a instalação; verifique com:

```bash
exiftool -ver
mediainfo --version
convert -version 2>/dev/null || magick -version   # IM6 usa 'convert'; IM7 usa 'magick'
soffice --headless --version
ffmpeg -version | head -1
rsync --version | head -1
hashdeep -V
xmllint --version 2>&1 | head -1                  # libxml2-utils
```

Notas por ferramenta:

- **ImageMagick (E7):** conforme a versão empacotada, o comando é `convert`
  (ImageMagick 6) ou `magick` (ImageMagick 7); o `derivadas.py` tenta `magick` e
  depois `convert`, então qualquer um serve.
- **LibreOffice (E7):** comando `soffice`, executado em modo `--headless`. Para
  um servidor enxuto, bastam `libreoffice-core libreoffice-writer` (acrescente
  `-calc`/`-impress` conforme o acervo). Em execuções automatizadas, pode-se
  isolar o perfil de usuário com `-env:UserInstallation=file:///tmp/lo_profile`.
- **hashdeep (E10):** reforço opcional — o `verificacao.py` usa o módulo
  `hashlib` do Python por padrão. O pacote instala apenas o executável `hashdeep`
  (use `hashdeep -c sha256 -r <dir>` para hashing recursivo SHA-256).
- **libxml2-utils (xmllint):** usado pela ferramenta auxiliar `validar_premis.py`
  e em validações avulsas do `metadata/premis.xml` contra o esquema PREMIS v3.
  Para validar offline, baixe uma vez o XSD oficial e aponte-o:
  ```bash
  wget https://www.loc.gov/standards/premis/v3/premis.xsd
  xmllint --schema premis.xsd --noout <aip>/metadata/premis.xml
  # ou, com resumo legível do conteúdo:
  python3 validar_premis.py <aip> --xsd premis.xsd
  ```

Duas ferramentas têm passos adicionais (itens 2.1 e 2.2).

### 2.1 ClamAV — base de assinaturas (E2, MS1)

O `clamscan` **só funciona após a base de assinaturas ser baixada**. O serviço
`clamav-freshclam` faz isso automaticamente; para garantir a primeira atualização
imediatamente:

```bash
sudo systemctl stop clamav-freshclam
sudo freshclam
sudo systemctl start clamav-freshclam

clamscan --version          # confirma versão e data da base
echo teste > /tmp/limpo.txt && clamscan /tmp/limpo.txt   # deve terminar "OK"
```

> Sem a base, o `clamscan` retorna erro (código 2) e o `admissao.py`
> corretamente não admite o material — a varredura antivírus é condição de
> admissão (E2).

### 2.2 rclone — configuração dos destinos remotos (E9, MS8)

O `rclone` do `apt` pode estar em uma versão mais antiga; para a mais recente, use
o instalador oficial:

```bash
curl https://rclone.org/install.sh | sudo bash
```

Antes do uso, configure os destinos remotos (nuvem). Os nomes criados devem
corresponder aos do `destinos.exemplo.json`:

```bash
rclone config       # cria os "remotes" (ex.: gdrive:workflowtcc/aips)
rclone version
```

**Configuração de um remoto Google Drive** (interativo). No menu do `rclone
config`, responda:

```
n) New remote
name> gdrive                      # nome livre; deve casar com o destinos.json
Storage> drive                    # "Google Drive" (pode digitar "drive")
client_id> [Enter]                # em branco (ver nota sobre client_id próprio)
client_secret> [Enter]
scope> 1                          # 1 = acesso total ao Drive
service_account_file> [Enter]
Edit advanced config? n
Use auto config? y                # y em desktop com navegador; n se for servidor
Configure this as a Shared Drive (Team Drive)? n   # n para Drive pessoal
y) Yes this is OK
q) Quit config
```

Com `Use auto config? y`, o `rclone` abre o navegador para o login Google e
captura o token automaticamente. **Em servidor headless** (sem navegador),
responda `n`: o `rclone` exibirá uma URL e pedirá um token; gere-o em outra
máquina que tenha navegador e `rclone`, com `rclone authorize "drive"`, e cole
o token de volta no servidor.

Teste o remoto:

```bash
rclone lsd gdrive:                                   # lista pastas
rclone mkdir gdrive:workflowtcc/aips
echo teste > /tmp/t.txt && rclone copy /tmp/t.txt gdrive:workflowtcc/aips/
rclone check /tmp/t.txt gdrive:workflowtcc/aips/     # verificação por hash
```

> **client_id próprio (recomendado para uso institucional):** deixar `client_id`
> em branco usa uma credencial OAuth compartilhada globalmente, sujeita a limites
> de taxa do Google. Para uso em produção, crie um `client_id`/`client_secret`
> próprio no Google Cloud Console (habilite a Google Drive API → Credentials →
> OAuth client ID, tipo Desktop app) e informe-os no `rclone config`. Guia:
> <https://rclone.org/drive/#making-your-own-client-id>.

**Verificação periódica de AIPs remotos (E10).** O `verificacao.py` lê os bytes
de cada arquivo para recalcular o SHA-256, e isso exige um sistema de arquivos
local — ele **não** percorre diretamente uma especificação de remoto do `rclone`
(`gdrive:...`). Para verificar AIPs que estão na nuvem, monte o remoto como um
sistema de arquivos local com o `rclone mount` (via FUSE) e aponte o
`verificacao.py` para o ponto de montagem:

```bash
sudo apt install -y fuse3
mkdir -p ~/gdrive-aips
rclone mount gdrive:workflowtcc/aips ~/gdrive-aips --vfs-cache-mode full --daemon

python3 verificacao.py --armazenamento ~/gdrive-aips

fusermount -u ~/gdrive-aips                          # desmonta ao terminar
```

Observe que `bag.validate()` lê todos os bytes de todos os arquivos; sobre um
remoto montado, isso baixa o AIP a cada ciclo. Em acervos grandes, a alternativa
mais econômica é manter uma cópia local verificada e confirmar o remoto contra
ela por hash (`rclone check copia_local gdrive:.../<aip>`), reaproveitando o
MD5 que o Drive já armazena, sem baixar tudo.

O `rsync` (cópias locais e em rede local) já foi instalado no lote e não exige
configuração.

---

## Parte 3 — Ferramentas com instalador próprio (download)

Estas não têm pacote `apt` oficial. As três aplicações Java (JHOVE, veraPDF, FITS)
reutilizam o `openjdk-21-jre` da Parte 1.

### 3.1 DROID (identificação de formato — padrão, E4/MS2)

Baixe o pacote multiplataforma `droid-binary-<versão>-bin.zip` (o que **não** traz
Java embutido):

- Releases: <https://github.com/digital-preservation/droid/releases>
- TNA: <https://www.nationalarchives.gov.uk/information-management/manage-information/preserving-digital-records/droid/>

```bash
# Exemplo com a 6.8.1 — confirme o nome/URL do arquivo atual na página de releases
curl -L -o /tmp/droid.zip \
  "https://github.com/digital-preservation/droid/releases/download/droid-6.8.1/droid-binary-6.8.1-bin.zip"
sudo mkdir -p /opt/droid
sudo unzip -q /tmp/droid.zip -d /opt/droid
sudo chmod +x /opt/droid/droid.sh

# Lançador 'droid' no PATH (o identificacao.py chama o comando 'droid')
sudo tee /usr/local/bin/droid >/dev/null <<'EOF'
#!/bin/sh
exec /opt/droid/droid.sh "$@"
EOF
sudo chmod +x /usr/local/bin/droid
```

Na primeira execução em linha de comando, o DROID baixa automaticamente as
assinaturas PRONOM (binária e de contêiner) e grava sua configuração em
`~/.droid6` (requer internet). Teste reproduzindo o que o workflow faz:

```bash
mkdir -p /tmp/amostra && printf 'teste\n' > /tmp/amostra/a.txt
droid -R -a /tmp/amostra -p /tmp/perfil.droid
droid -p /tmp/perfil.droid -E /tmp/saida.csv
column -s, -t /tmp/saida.csv     # exibe o relatório em colunas
```

No relatório, o diretório aparece como uma linha à parte (`TYPE = Folder`, com a
coluna `PUID` vazia — normal); a linha do arquivo `a.txt` (`TYPE = File`) deve
trazer `METHOD = Extension` e `PUID = x-fmt/111` (`Plain Text File`), pois texto
puro é identificado por extensão. A primeira coluna do CSV (`ID`) é apenas o
identificador interno sequencial do DROID, não o PUID. O `identificacao.py`
processa somente as linhas `TYPE = File`, ignorando as de diretório.

### 3.2 Siegfried (identificação de formato — alternativa, E4/MS2)

O Siegfried (`sf`) é um binário em Go, instalado pelo repositório apt da
comunidade:

```bash
sudo mkdir -p /etc/apt/keyrings
curl -sL "http://keyserver.ubuntu.com/pks/lookup?op=get&search=0x20F802FE798E6857" \
  | gpg --dearmor | sudo tee /etc/apt/keyrings/siegfried-archive-keyring.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/siegfried-archive-keyring.gpg] https://www.itforarchivists.com/ buster main" \
  | sudo tee /etc/apt/sources.list.d/siegfried.list >/dev/null
sudo apt update && sudo apt install -y siegfried

sf -update         # baixa/atualiza as assinaturas PRONOM
sf -version
sf -json /tmp/amostra | head
```

> O componente `buster main` é uma suíte estática do repositório e funciona em
> qualquer versão do Ubuntu, independentemente do codinome (o do 26.04 é
> *resolute*).

### 3.3 JHOVE (conformidade — padrão, E5/MS3)

Aplicação Java com instalador IzPack. O instalador é distribuído pelo site da Open
Preservation Foundation — **não está no GitHub releases**. O GitHub hospeda apenas
o código-fonte.

- Download (sempre-atual): <http://software.openpreservation.org/rel/jhove-latest.jar>
- Versões anteriores / arquivo: <https://software.openpreservation.org/releases/jhove>
- Produto / documentação: <https://jhove.openpreservation.org>

> **Importante:** `jhove-latest.jar` é o instalador IzPack, não um JAR
> executável diretamente. Sem a flag `-console`, ele tenta abrir uma janela
> gráfica e falha em servidores sem display.

**Por que instalar em `/opt` e não em `~/jhove`?**

O instalador escreve o caminho de instalação diretamente em `JHOVE_HOME` dentro
do script lançador (`jhove`). Se instalado em `~/jhove`, esse caminho aponta para
o diretório pessoal de quem instalou e outros usuários não conseguem usar o JHOVE
(o config seria buscado em `~/jhove/conf/jhove.conf` de cada um, onde não existe).
Instalando em `/opt/jhove`, o `JHOVE_HOME=/opt/jhove` fica fixo no script e
qualquer usuário que execute `jhove` encontra automaticamente o config e os módulos.

**Instalação em `/opt/jhove` (todos os usuários — recomendado):**

```bash
curl -L -o /tmp/jhove-latest.jar \
  "http://software.openpreservation.org/rel/jhove-latest.jar"

# Cria /opt/jhove e cede temporariamente a escrita ao usuário atual
sudo mkdir -p /opt/jhove
sudo chown "$USER" /opt/jhove

# Modo texto (sem display); quando o instalador perguntar o caminho,
# substitua o padrão ~/jhove por: /opt/jhove
java -jar /tmp/jhove-latest.jar -console

# Restaura a posse ao root e garante leitura/execução para todos
sudo chown -R root:root /opt/jhove
sudo chmod -R 755 /opt/jhove

# Lançador acessível a todos os usuários (wrapper por caminho absoluto, em vez de
# symlink, para evitar ambiguidade na resolução do diretório-base do JHOVE)
sudo tee /usr/local/bin/jhove >/dev/null <<'EOF'
#!/bin/sh
exec /opt/jhove/jhove "$@"
EOF
sudo chmod +x /usr/local/bin/jhove
jhove -v
```

> **Instalação por usuário** (só quem instalou usa): aceite o caminho padrão
> `~/jhove` durante a instalação. Nesse caso, o lançador em `/usr/local/bin/jhove`
> aponta para o diretório pessoal de quem instalou e outros usuários não conseguem
> utilizá-lo.
>
> **Instalação não assistida** (automação): rode uma vez com `-console`, salve o
> `auto-install.xml` gerado ao final e reutilize-o em outras máquinas com
> `java -jar /tmp/jhove-latest.jar auto-install.xml`.
>
> **Alternativa — virtual framebuffer** (se `-console` não funcionar):
> `sudo apt install -y xvfb && xvfb-run java -jar /tmp/jhove-latest.jar`

### 3.4 veraPDF (conformidade — alternativa PDF/A, E5/MS3)

Também usa instalador IzPack, com a mesma dependência de caminho fixo no lançador.
Instale em `/opt/verapdf` para que qualquer usuário possa executá-lo.

```bash
curl -L -o /tmp/verapdf-installer.zip \
  "https://software.verapdf.org/rel/verapdf-installer.zip"
mkdir -p /tmp/verapdf-inst && unzip -q /tmp/verapdf-installer.zip -d /tmp/verapdf-inst
cd /tmp/verapdf-inst/verapdf-*
VERAPDF_JAR=$(ls verapdf-izpack-installer-*.jar)

# Cria /opt/verapdf e cede temporariamente a escrita ao usuário atual
sudo mkdir -p /opt/verapdf
sudo chown "$USER" /opt/verapdf

# Modo texto (sem display); quando perguntar o caminho, informe: /opt/verapdf
java -jar "$VERAPDF_JAR" -console

# Restaura a posse ao root e garante leitura/execução para todos
sudo chown -R root:root /opt/verapdf
sudo chmod -R 755 /opt/verapdf

sudo tee /usr/local/bin/verapdf >/dev/null <<'EOF'
#!/bin/sh
exec /opt/verapdf/verapdf "$@"
EOF
sudo chmod +x /usr/local/bin/verapdf
verapdf --version
```

> **Instalação não assistida (auto-install.xml):** o veraPDF documenta
> explicitamente esse modo — rode uma vez com `-console`, salve o
> `auto-install.xml` gerado ao final e reutilize-o em outras máquinas com
> `java -jar "$VERAPDF_JAR" auto-install.xml`.
>
> **MediaConch (opcional):** para acervos predominantemente audiovisuais, o
> MediaConch é o validador indicado (Seção 5.3); pacotes `.deb` em
> <https://mediaarea.net/MediaConch/download>. Não é necessário no fluxo padrão.

### 3.5 FITS (caracterização — padrão, E6/E7/MS4)

Distribuído como ZIP e executado pelo `fits.sh`:

- Downloads: <https://projects.iq.harvard.edu/fits/downloads>
- Releases: <https://github.com/harvard-lts/fits/releases>

```bash
# Exemplo com a 1.6.0 — confirme a versão/URL atual na página de downloads
curl -L -o /tmp/fits.zip \
  "https://github.com/harvard-lts/fits/releases/download/1.6.0/fits-1.6.0.zip"
sudo mkdir -p /opt/fits
sudo unzip -q /tmp/fits.zip -d /opt/fits
sudo chmod +x /opt/fits/fits.sh
# Wrapper por caminho absoluto (em vez de symlink): garante que o fits.sh
# resolva o FITS_HOME para /opt/fits, independentemente de quem o executa
sudo tee /usr/local/bin/fits >/dev/null <<'EOF'
#!/bin/sh
exec /opt/fits/fits.sh "$@"
EOF
sudo chmod +x /usr/local/bin/fits
fits -v
```

> **Atenção (Ubuntu 26.04):** o FITS embute uma cópia do MediaInfo compilada para
> o Ubuntu 22.04 (Jammy). Se a caracterização de áudio/vídeo falhar, remova a
> cópia embutida para que o FITS use o MediaInfo do sistema (instalado na Parte 2):
>
> ```bash
> sudo rm -f /opt/fits/tools/mediainfo/linux/libmediainfo.so.0 \
>            /opt/fits/tools/mediainfo/linux/libzen.so.0
> ```

O `caracterizacao.py` usa o FITS como padrão e, como alternativa, a combinação
**ExifTool + MediaInfo** (já instalada na Parte 2; `--ferramenta fits|exif`).

---

## Parte 4 — Dependências Python (em ambiente virtual)

O workflow depende de três bibliotecas Python (`bagit`, `lxml`, `odfpy`). A
partir do Ubuntu 23.04, o Python do sistema é "externally managed" (PEP 668):
um `pip install` direto falha com a mensagem `error: externally-managed-environment`.
A forma recomendada — pela própria mensagem do erro e pela documentação Debian/
Ubuntu — é usar um **ambiente virtual** (`venv`) por projeto.

```bash
sudo apt install -y python3-venv python3-full
cd /caminho/para/workflow_preservacao
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Em sessões subsequentes, ative o ambiente antes de rodar o workflow:

```bash
cd /caminho/para/workflow_preservacao
source .venv/bin/activate
python3 orquestrador.py --entrada ... --saida ... --nome ...
deactivate                         # ao terminar
```

Para o agendamento de E10 no `cron`, aponte para o Python do venv diretamente,
sem precisar de `source`:

```cron
0 3 * * 0 /caminho/para/workflow_preservacao/.venv/bin/python \
          /caminho/para/workflow_preservacao/verificacao.py \
          --armazenamento /aips
```

> **Por que não 100% apt?** O Ubuntu fornece `python3-lxml` e `python3-odf`
> (que é o `odfpy`), mas o `BagIt-Python` da Library of Congress **não tem
> pacote `.deb`** — segue só pelo PyPI. Logo, qualquer caminho que evite
> totalmente o `venv` cairia em `pip install --break-system-packages`,
> exatamente o que o PEP 668 desencoraja em sistemas multiusuário.

### 4.1 Alternativas ao venv (quando aplicável)

O `venv` é a recomendação para uso institucional, mas há dois atalhos para
ambientes específicos:

- **Instalação no espaço do usuário, sem `sudo`** — confina os pacotes ao
  `~/.local/` do usuário corrente, sem tocar no Python do sistema. O Python já
  inclui esse caminho na busca de módulos (PEP 370), então o workflow funciona
  sem ativar nada:
  ```bash
  pip install --user --break-system-packages -r requirements.txt
  ```
  Para reverter: `rm -rf ~/.local/lib/python3.*/site-packages/{bagit,lxml,odf,odfpy*}*`.
  No agendamento de E10, use o `crontab` **do usuário** (`crontab -e`), que
  herda o `$HOME` e enxerga o `~/.local/`; o cron do sistema (`/etc/cron.d`),
  que roda como `root`, não veria esses pacotes.

- **Instalação no sistema com `--break-system-packages`** — só faz sentido em
  contêiner descartável ou VM dedicada exclusivamente ao workflow. Em estação
  compartilhada ou servidor de produção, **evite**: pode conflitar com pacotes
  Python do `apt` em atualizações futuras do sistema.

> **pipx não serve aqui.** O `pipx` isola *aplicativos* CLI, expondo apenas
> executáveis; ele não disponibiliza bibliotecas para `import` por outros
> scripts. Como o workflow importa `bagit`, `lxml` e `odf` como módulos, o
> `pipx` não atende — use `venv` (recomendado) ou uma das alternativas acima.

---

## Parte 5 — Verificação final

Confirme que todos os comandos estão visíveis ao orquestrador:

```bash
command -v java clamscan droid sf jhove verapdf fits exiftool mediainfo \
           convert magick soffice ffmpeg rsync rclone hashdeep
```

Cada microsserviço degrada graciosamente quando há alternativa configurada
(DROID → Siegfried; FITS → ExifTool+MediaInfo) e registra a ausência em log e em
evento PREMIS, sem interromper o fluxo — exceto a varredura antivírus (E2), que é
condição de admissão. Com isso, o ambiente está pronto para executar o
`orquestrador.py` conforme o `README.md`.

---

## Apêndice A — Instalação sem privilégios (sem sudo)

Para ambientes em que o `sudo` não esteja disponível, as ferramentas que não vêm
do `apt` podem ser instaladas inteiramente no espaço do usuário. Os instaladores
de JHOVE, veraPDF e FITS já instalam por padrão sob o diretório pessoal (`~/jhove`,
`~/verapdf`); basta expor os comandos via `~/.local/bin` em vez de `/usr/local/bin`.

Garanta que `~/.local/bin` esteja no `PATH`:

```bash
mkdir -p ~/opt ~/.local/bin
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.profile
source ~/.profile
```

**Java 21 (Eclipse Temurin), em espaço de usuário** — substitui o
`openjdk-21-jre` da Parte 1:

```bash
curl -L -o /tmp/temurin21.tar.gz \
  "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jdk/hotspot/normal/eclipse"
tar -xzf /tmp/temurin21.tar.gz -C ~/opt
mv ~/opt/jdk-21* ~/opt/jdk-21
echo 'export JAVA_HOME="$HOME/opt/jdk-21"' >> ~/.profile
echo 'export PATH="$JAVA_HOME/bin:$PATH"' >> ~/.profile
source ~/.profile
java -version
```

**DROID, em espaço de usuário** (lançador que fixa o Java do usuário):

```bash
mkdir -p ~/opt/droid && unzip -q /tmp/droid.zip -d ~/opt/droid
chmod +x ~/opt/droid/droid.sh
cat > ~/.local/bin/droid <<'EOF'
#!/bin/sh
export JAVA_HOME="${JAVA_HOME:-$HOME/opt/jdk-21}"
export PATH="$JAVA_HOME/bin:$PATH"
exec "$HOME/opt/droid/droid.sh" "$@"
EOF
chmod +x ~/.local/bin/droid
```

**Siegfried, em espaço de usuário** (binário pré-compilado; ajuste a versão ao
arquivo atual em <https://github.com/richardlehane/siegfried/releases>):

```bash
curl -L -o /tmp/siegfried.zip \
  "https://github.com/richardlehane/siegfried/releases/download/v1.11.1/siegfried_1_11_1_linux64.zip"
unzip -o /tmp/siegfried.zip -d ~/.local/bin sf roy
chmod +x ~/.local/bin/sf ~/.local/bin/roy
sf -update
```

**JHOVE / veraPDF / FITS, em espaço de usuário** — instale como na Parte 3
(aceitando os caminhos padrão `~/jhove`, `~/verapdf` e `~/opt/fits`), mas exponha
os lançadores em `~/.local/bin` por meio de wrappers (mesmo motivo da Parte 3:
evitar ambiguidade na resolução do diretório-base):

```bash
cat > ~/.local/bin/jhove   <<'EOF'
#!/bin/sh
exec "$HOME/jhove/jhove" "$@"
EOF
cat > ~/.local/bin/verapdf <<'EOF'
#!/bin/sh
exec "$HOME/verapdf/verapdf" "$@"
EOF
cat > ~/.local/bin/fits    <<'EOF'
#!/bin/sh
exec "$HOME/opt/fits/fits.sh" "$@"
EOF
chmod +x ~/.local/bin/jhove ~/.local/bin/verapdf ~/.local/bin/fits
```

As ferramentas do `apt` (ClamAV, ExifTool, MediaInfo, ImageMagick, LibreOffice,
FFmpeg, rsync, rclone, hashdeep) exigem privilégios para instalação no sistema;
sem `sudo`, dependem de um administrador tê-las instalado previamente, ou de
versões portáteis equivalentes.

**Dependências Python**: o `venv` (Parte 4) já é a abordagem padrão e funciona
sem `sudo` para os pacotes Python em si. Só a criação do venv exige o pacote
`python3-venv` instalado no sistema; se ele não estiver disponível, o Python
pode ser obtido em espaço de usuário a partir de <https://www.python.org> ou
via `uv` / `pyenv`, e o `venv` é criado normalmente em seguida.
