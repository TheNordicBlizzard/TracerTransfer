# TracerTransfer

Sistema de transferência segura, automática e ponta a ponta de arquivos de traçador (`.oma` e `.vca`) entre lojas/ópticas remotas e o laboratório de produção industrial (FreeForm / SIOU).

O sistema opera em modelo **Store-and-Forward** composto por 3 programas independentes:
1. **Cliente (`TracerCliente.exe`):** monitora a pasta nas lojas, enfileira e envia arquivos via HTTPS com confirmação criptográfica e retentativa persistente.
2. **Servidor (`TracerServidor` / Linux Daemon):** atua como fila intermediária segura (spool), autentica dispositivos, fornece códigos de ativação de uso único e expõe painel administrativo protegido.
3. **Entregador (`TracerEntregador.exe` / Serviço Windows):** serviço executado no computador receptor do laboratório que consome a fila do servidor, valida integridade (SHA-256) e grava atomicamente os arquivos na pasta industrial (`C:\FreeForm\Tracer`), prevenindo sobrescrita por conflito.

---

## Índice

- [Arquitetura e Fluxo de Dados](#arquitetura-e-fluxo-de-dados)
- [1. TracerCliente (Lojas / Origem)](#1-tracercliente-lojas--origem)
  - [Funcionalidades](#funcionalidades-do-cliente)
  - [Como Instalar e Configurar](#como-instalar-e-configurar-o-cliente)
  - [Operação no Dia a Dia](#opera%C3%A7%C3%A3o-do-cliente)
- [2. TracerServidor (Servidor Central / Nuvem)](#2-tracerservidor-servidor-central--nuvem)
  - [Funcionalidades](#funcionalidades-do-servidor)
  - [Como Instalar (Linux / Debian / Ubuntu)](#como-instalar-o-servidor-em-linux)
  - [Como Instalar (Windows Server / Standalone)](#como-instalar-o-servidor-em-windows)
  - [Acesso Administrativo e Painel Web](#acesso-administrativo-e-painel-web)
- [3. TracerEntregador (Laboratório / Receptor)](#3-tracerentregador-laborat%C3%B3rio--receptor)
  - [Funcionalidades](#funcionalidades-do-entregador)
  - [Como Instalar como Serviço Windows](#como-instalar-o-entregador-como-servi%C3%A7o-windows)
  - [Comandos de Gerenciamento do Serviço](#comandos-de-gerenciamento-do-servi%C3%A7o)
- [Atualizações Automáticas (Auto-Update)](#atualiza%C3%A7%C3%B5es-autom%C3%A1ticas-auto-update)
- [Tratamento de Conflitos e Recuperação](#tratamento-de-conflitos-e-recupera%C3%A7%C3%A3o)
- [Desenvolvimento e Compilação](#desenvolvimento-e-compila%C3%A7%C3%A3o)

---

## Arquitetura e Fluxo de Dados

```
[Loja / Óptica]                     [Servidor Central]                     [Laboratório]
Tracer / Computador                  Servidor HTTPS / Nuvem                Computador Receptor
       │                                     │                             (Rede Local / LAN)
       ▼                                     │                                     │
[Pasta Monitorada]                           │                                     │
(*.oma / *.vca)                              │                                     │
       │                                     │                                     │
       ▼ (Monitora e envia)                  │                                     │
TracerCliente.exe  ────── HTTPS POST ──────► │ (Spool em disco                     │
(Fila local SQLite)    /api/files             │  + SQLite)                          │
                                             │                                     │
                                             │ ◄── Polling HTTPS (Bearer Token) ── TracerEntregador.exe
                                             │     GET /api/delivery/next          (Serviço Windows)
                                             │                                             │
                                             │ ─── Download e Validação SHA-256 ────────► │
                                             │     GET /api/delivery/{id}/content          │ (Gravação atômica)
                                             │                                             ▼
                                             │ ◄── Confirmação de Entrega (ACK) ── [C:\FreeForm\Tracer]
                                             │     POST /api/delivery/{id}/ack
```

---

## 1. TracerCliente (Lojas / Origem)

O **TracerCliente** é o aplicativo que roda nas máquinas das filiais/lojas onde os traçadores geram os arquivos de armação.

### Funcionalidades do Cliente
- **Monitoramento contínuo:** vigia a pasta configurada sem travar o Windows Explorer, detectando novos arquivos `.oma` e `.vca`.
- **Varredura completa (v0.1.4+):** processa e envia todos os arquivos `.oma`/`.vca` da pasta, independentemente de terem sido criados antes ou depois da instalação do cliente.
- **Checagem de estabilidade:** aguarda o traçador concluir a escrita do arquivo antes de iniciar o envio, evitando envios corrompidos ou parciais.
- **Fila local persistente (SQLite):** se a internet cair ou o servidor estiver indisponível, os arquivos ficam enfileirados localmente (`state.db`) com política de retentativa exponencial.
- **Proteção de credenciais via DPAPI:** o token do dispositivo e configurações sensíveis são cifrados nativamente no Windows com `CryptProtectData` (vinculados ao perfil do usuário local).
- **Execução silenciosa na bandeja:** minimiza para a área de notificação do Windows (systray) e inicia automaticamente junto com o logon do usuário.
- **Auto-Update integrado:** verifica a cada 6 horas atualizações assinadas com Ed25519 no GitHub Releases e aplica silenciosamente sem intervenção manual.

### Como Instalar e Configurar o Cliente

#### Opção A: Via Instalador Automático (Recomendado)
1. Baixe o instalador oficial `TracerTransfer-Cliente-<versão>-windows-x64-pilot-unsigned.exe` da release.
2. Execute o instalador com duplo clique (não requer privilégios de Administrador global; instala por usuário em `%LocalAppData%\Programs\TracerTransfer`).
3. Conclua o assistente. O programa iniciará e abrirá a tela inicial de boas-vindas.

#### Opção B: Pacote Portátil (.zip)
1. Baixe e extraia `TracerTransfer-Cliente-<versão>-Pacote.zip` em uma pasta do usuário.
2. Execute `TracerCliente.exe`.

#### Ativação Inicial da Loja (Passo Obrigatório)
1. Solicite ao administrador do laboratório um **Código de Ativação** (gerado no painel web do servidor).
2. Na tela do `TracerCliente`:
   - Digite ou confirme a URL do servidor (ex: `https://tracer.exemplo.com.br`).
   - Digite o **Código de Ativação** de uso único fornecido (formato: letras e números, válido por tempo limitado).
   - Clique em **Ativar Dispositivo**.
3. O cliente autentica no servidor, gera um segredo criptográfico e armazena o token protegido via Windows DPAPI.
4. **Selecione a Pasta do Traçador:**
   - Clique em **Selecionar Pasta...** e aponte para onde o software do traçador salva os arquivos (ex: `C:\Tracer\Export` ou `C:\OMA`).
   - Salve a configuração.

### Operação do Cliente
- O ícone do TracerTransfer permanecerá na bandeja do sistema (ao lado do relógio do Windows).
- Um clique duplo no ícone abre a janela de status para ver os últimos envios ou alterar a pasta.
- Para fechar completamente o cliente, clique com o botão direito no ícone da bandeja e selecione **Sair**.

---

## 2. TracerServidor (Servidor Central / Nuvem)

O **TracerServidor** é o núcleo de orquestração do sistema. Ele roda tipicamente em um servidor Linux (VM Debian/Ubuntu) ou em servidor Windows.

### Funcionalidades do Servidor
- **Isolamento de porta e segurança:** o processo escuta exclusivamente em `127.0.0.1:8765` (loopback). A exposição externa ocorre por terminação TLS reversa (Nginx com HTTPS ou Cloudflare Tunnel), prevenindo ataques diretos.
- **Store-and-Forward (Spool):** armazena arquivos recebidos em `/var/lib/tracer-transfer/delivery_spool/` com hash SHA-256 e controle transacional no SQLite (`tracer.sqlite3`).
- **Autenticação centralizada de administradores:** senhas protegidas com algoritmo Argon2id, sessões via cookies assinados `HttpOnly`, `SameSite=Lax` e proteção estrita contra CSRF.
- **Gerenciamento de Dispositivos:** cadastro de remetentes (lojas) e receptores (laboratório), rotação de chaves e suspensão/revogação instantânea de acesso.
- **Emissão de Códigos de Ativação:** gera códigos únicos e efêmeros para ativação simplificada de novas lojas sem digitar senhas ou tokens manuais.
- **Trilha de auditoria:** registra todas as ações administrativas, ativações e falhas em log estruturado.

### Como Instalar o Servidor em Linux

#### 1. Pré-requisitos do Sistema
```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip curl
```

#### 2. Extração e Instalação do Pacote
Baixe o arquivo `TracerServidor-<versão>-linux.tar.gz` da release e execute:
```bash
# Cria o diretório da aplicação e do spool/banco de dados
sudo mkdir -p /opt/tracer-transfer /var/lib/tracer-transfer /etc/tracer-transfer
sudo chown -R root:root /opt/tracer-transfer

# Extrai o tarball
sudo tar -xzf TracerServidor-<versão>-linux.tar.gz -C /opt/tracer-transfer

# Cria o ambiente virtual Python
sudo python3 -m venv /opt/tracer-transfer/venv
sudo /opt/tracer-transfer/venv/bin/pip install --upgrade pip
sudo /opt/tracer-transfer/venv/bin/pip install -r /opt/tracer-transfer/requirements.txt
```

#### 3. Configuração do Ambiente (`tracer-transfer.env`)
Crie o arquivo `/etc/tracer-transfer/tracer-transfer.env` com permissão estrita `600`:
```bash
sudo bash -c 'cat <<EOF > /etc/tracer-transfer/tracer-transfer.env
TRACER_SERVER_HOST=127.0.0.1
TRACER_SERVER_PORT=8765
TRACER_DATA_DIR=/var/lib/tracer-transfer
TRACER_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
EOF'
sudo chmod 600 /etc/tracer-transfer/tracer-transfer.env
```

#### 4. Instalação e Inicialização do Serviço Systemd
```bash
# Instala a unit systemd
sudo cp /opt/tracer-transfer/deploy/linux/tracer-transfer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tracer-transfer
sudo systemctl start tracer-transfer

# Verifica o status
sudo systemctl status tracer-transfer
```

#### 5. Criação do Primeiro Administrador
Execute o utilitário seguro via terminal (a senha é informada interativamente sem eco):
```bash
sudo /opt/tracer-transfer/venv/bin/python /opt/tracer-transfer/deploy/linux/manage_admin.py --create-admin admin
```

#### 6. Publicação HTTPS Externa
Recomenda-se utilizar o **Cloudflare Tunnel (`cloudflared`)** ou **Nginx Reverse Proxy** apontando para `http://127.0.0.1:8765`.

### Como Instalar o Servidor em Windows
Caso deseje executar o servidor em máquina Windows:
1. Utilize o executável compilado `TracerServidor.exe`.
2. Execute via linha de comando especificando a pasta de dados:
   ```powershell
   .\TracerServidor.exe --data-dir "C:\ProgramData\TracerTransfer-Server" --port 8765
   ```

### Acesso Administrativo e Painel Web
1. Abra o navegador e acesse a URL interna ou externa configurada (ex: via túnel SSH `http://localhost:18765` ou `https://tracer.exemplo.com.br`).
2. Faça login com o usuário administrador criado.
3. No painel você pode:
   - Ver o status em tempo real de todas as lojas e do receptor do laboratório.
   - Gerar códigos de ativação para novos computadores de traçador.
   - Visualizar a fila de entregas, arquivos pendentes, concluídos ou em conflito.

---

## 3. TracerEntregador (Laboratório / Receptor)

O **TracerEntregador** é o serviço responsável por receber os arquivos do servidor e colocá-los na máquina de cálculo do laboratório.

### Funcionalidades do Entregador
- **Operação como Serviço Windows (WinSW):** roda de forma totalmente autônoma sob a conta `NT AUTHORITY\LocalService` ou conta de serviço local, sem exigir que um usuário esteja logado no Windows.
- **Download seguro e validação de hash:** baixa arquivos da fila `/api/delivery/next`, confirma integridade conferindo o SHA-256 recebido contra o arquivo baixado.
- **Gravação Atômica em Disco:** salva os arquivos primeiro com extensão temporária `.part` no mesmo volume físico e faz renomeação atômica (`ReplaceFile`/`MoveFileEx`), garantindo que o software FreeForm nunca leia um arquivo cortado pela metade.
- **Prevenção de Sobrescrita Destrutiva:** se um arquivo com o mesmo nome já existir na pasta de destino com conteúdo diferente, o serviço não sobrescreve; ele sinaliza `destination_conflict` ao servidor e preserva a produção.
- **Confirmação transacional (ACK):** só remove o arquivo do spool do servidor após a gravação atômica ter sido confirmada no disco local.

### Como Instalar o Entregador como Serviço Windows

#### 1. Preparação dos Arquivos
1. No computador receptor do laboratório, baixe o pacote `TracerTransfer-Entregador-<versão>-Pacote.zip`.
2. Extraia o conteúdo em uma pasta temporária (ex: `C:\Temp\TracerEntrega`).

#### 2. Geração do Token de Entrega no Servidor
1. No painel web do servidor, acesse a aba **Dispositivos**.
2. Cadastre o dispositivo com a função `delivery` (ex: `Receptor Lab FreeForm`) e obtenha o token de autenticação de entrega.

#### 3. Instalação com PowerShell (Como Administrador)
Abra o **PowerShell como Administrador** e execute o script `Install-Delivery.ps1` contido no pacote:

```powershell
Set-Location "C:\Temp\TracerEntrega\deploy\windows-delivery"

.\Install-Delivery.ps1 `
    -BaseDirectory "C:\ProgramData\TracerTransfer-Delivery" `
    -OutputDirectory "C:\FreeForm\Tracer" `
    -ServerUrl "https://tracer.exemplo.com.br" `
    -DeliveryToken "SEU_TOKEN_DE_ENTREGA_AQUI"
```

O script realiza automaticamente:
1. Copia dos binários para `C:\Program Files\TracerTransfer-Delivery`.
2. Criação da pasta de dados `C:\ProgramData\TracerTransfer-Delivery`.
3. Proteção do token de entrega com **DPAPI em nível de máquina** (`LocalMachine`), garantindo que o serviço rodando sob `LocalService` possa se autenticar sem salvar o token em texto puro.
4. Registro e início do serviço Windows com o nome **`TracerTransferDelivery`** via WinSW.

### Comandos de Gerenciamento do Serviço

No PowerShell do computador receptor:

```powershell
# Verificar status do serviço
Get-Service TracerTransferDelivery

# Reiniciar o serviço
Restart-Service TracerTransferDelivery

# Parar o serviço
Stop-Service TracerTransferDelivery

# Ver os logs de entrega em tempo real
Get-Content "C:\ProgramData\TracerTransfer-Delivery\logs\TracerTransferDelivery.out.log" -Tail 50 -Wait
```

Para desinstalar o serviço de entrega:
```powershell
.\Uninstall-Delivery.ps1 -BaseDirectory "C:\ProgramData\TracerTransfer-Delivery"
```

---

## Atualizações Automáticas (Auto-Update)

O TracerTransfer conta com um pipeline de atualização criptograficamente seguro:
- **Feeds por Componente:** `update-feed-client.json`, `update-feed-delivery.json` e `update-feed-server.json`.
- **Assinatura Ed25519:** cada release é assinada digitalmente com chave privada protegida por DPAPI. Os clientes e serviços contêm a chave pública embutida e validam a assinatura antes de aceitar qualquer arquivo de atualização.
- **Verificação Periódica:**
  - O **Cliente** checa na inicialização e a cada 6 horas.
  - O **Entregador** possui tarefa de atualização integrada via `Enable-AutoUpdate.ps1`.
  - O **Servidor** possui rotina atômica de auto-update que valida o tarball antes de substituir o runtime em Linux.

---

## Tratamento de Conflitos e Recuperação

Se uma loja enviar um arquivo (ex: `611892.oma`) e na pasta de destino (`C:\FreeForm\Tracer`) já existir um arquivo com esse mesmo nome mas com hash SHA-256 diferente:
1. O `TracerEntregador` interrompe a escrita para não corromper o cálculo da máquina e registra um conflito local no banco `C:\ProgramData\TracerTransfer-Delivery\data\delivery.sqlite3`.
2. O servidor marca a entrega com status `conflict` (`destination_conflict`).
3. **Para resolver:**
   - Libere a pasta `C:\FreeForm\Tracer` (movendo ou processando o arquivo que estava ocupando o destino).
   - Remova o registro da entrega do banco local do entregador (`delivery.sqlite3`) para invalidar o cache terminal.
   - Altere o status no servidor para `pending`. O entregador processará e entregará o arquivo imediatamente.

---

## Desenvolvimento e Compilação

Para desenvolver ou compilar os artefatos a partir do código fonte no Windows:

```powershell
# 1. Sincronizar dependências com uv
uv sync --frozen --extra dev --extra build

# 2. Executar suíte de testes automatizados
uv run python -m pytest -q

# 3. Compilar executáveis (PyInstaller)
.\scripts\build.ps1 -Version 0.1.4

# 4. Compilar instalador Inno Setup
.\scripts\build_installer.ps1 -PilotUnsigned -IsccPath "caminho/para/ISCC.exe"

# 5. Gerar pacotes de distribuição (.zip e .tar.gz)
uv run python scripts/build_distribution_zips.py --version 0.1.4
uv run python scripts/build_server_archive.py --version 0.1.4 --verify

# 6. Assinar manifests de release com chave Ed25519
uv run python scripts/sign_update_release.py --version 0.1.4 --commit HEAD --key-file release-signing-key.dpapi
```

---

## Licença e Segurança

Este software é de uso restrito e privado. Chaves privadas, tokens de acesso, credenciais e dados de produção não devem ser comitados ou distribuídos publicamente.
