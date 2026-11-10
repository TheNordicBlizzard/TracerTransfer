# Contrato update 0.1.3 (integração dos componentes)

- API preservada: `prepare_update(component, current_version, cache_dir, *, http=None, public_key=..., now=None)` → `PreparedUpdate | None`; dataclass preserva os cinco campos anteriores.
- Componentes exclusivos: `client: inno-client`, `delivery: exe-delivery`, `server: tar-server`. `updater` foi removido.
- Nomes exatos: `TracerTransfer-Cliente-<version>-windows-x64-pilot-unsigned.exe`, `TracerEntregador.exe`, `TracerServidor-<version>-linux.tar.gz`.
- Cada release GitHub estável (nem draft nem prerelease) publica assets separados `update-feed-client.json`, `update-feed-delivery.json`, `update-feed-server.json`. Descritores locais podem ficar em `update-feeds/{component}.json`, mas assets GitHub não têm barras. Sem fallback para manifesto agregado antigo ou fonte de branch.
- Envelope Ed25519: `{ "payload": {...}, "signature": "base64" }`; canonicalização JSON ordenada, compacta, UTF-8.
- Payload schema **2** tem EXATAMENTE: `schema`, `repository`, `version`, `channel`, `published_at`, `expires_at`, `component`, `tag`, `commit`, `artifacts`.
- `channel` continua `pilot`; `tag` é `v<version>`; `commit` exige SHA completo lowercase com 40 caracteres. `artifacts` tem EXATAMENTE a chave `component`; metadata mantém `name`, `sha256`, `size`, `kind`.
- Criar release com `target_commitish` igual ao SHA compilado (não `main`); discovery exige igualdade entre esse campo e `payload.commit`. Tag e versão também são vinculadas. Nenhum download usa credencial GitHub.
- Assinador: uma invocação por componente: `uv run python scripts/sign_update_release.py --version 0.1.3 --commit <SHA> --delivery <EXE> --key-file <DPAPI-fora-repo> --output <update-feed-delivery.json>`. `--client` e `--server` equivalentes. Chave pinada não alterada; nenhum segredo emitido.

## Windows

- Apenas `TracerCliente.exe` e `TracerEntregador.exe`; nenhum quarto EXE.
- Cliente: `TracerCliente.exe --apply-update <request-dir>` antes de adquirir SingleInstance. Cópia privada de `TracerCliente.exe` em `<data>/updates/request-*/` usa handshake `ready` e handle do processo exato (também bootloader externo onefile) antes do Inno. Revalida manifesto e bytes, backup apenas do EXE, sem alterar config, DPAPI, fila ou preferências.
- Tarefa `TracerTransferDelivery-AutoUpdate`: SYSTEM/Highest, ação EXATA `<base>/bin/TracerEntregador.exe --update --base-directory "<base>"`, diretório de trabalho `<base>/updater`. `Enable-AutoUpdate.ps1` recebe `-ExpectedBinarySha256`, `-InstalledVersion` real e `-Enable` (sem UpdaterPath).
- `--update` verifica token SYSTEM, serviço LocalService/exato, wrapper pinado, tarefa e ACLs; copia o próprio EXE para `<base>/updater/run-*/TracerEntregador.exe` e lança `--apply-update <request-dir> --base-directory <base>`. O processo destacado adquire handle do launcher, confirma `ready` e espera ambos os processos do launcher liberarem a imagem instalada.
- SYSTEM nunca executa comando, manifesto ou payload recebidos do cache de LocalService: faz seu próprio `prepare_update` no cache protegido. LocalService só pode disparar a tarefa; falha bloqueia aplicação, sem fallback.
- Baseline obrigatória `<base>/updater/installed_version`; não se infere versão do helper. Journal em `applying`/`failed` bloqueia nova tentativa até reconciliação administrativa. Atualiza baseline só após polling confirmado; rollback apenas do executável.
- `Update-Delivery.ps1` deve ser embutido em `delivery.spec` na localização `deploy/windows-delivery/Update-Delivery.ps1`. Não executa em SCM parado nem reinicia quando parada inicial falha. Readiness correlaciona polling recente com PID/caminho/criação e pai WinSW (incluindo camada onefile).
- `updates.runtime.activated_profile` delega a `client.state.authorized_sender_profile`; config persistida conserva `server_url`. Threads discovery têm cancelamento próprio e são juntadas antes de fechar estado.

## Gates

Testes de filesystem/handles/PowerShell com mocks não equivalem a homologação de instalador real, conta SYSTEM/LocalService ou SCM industrial. Esses gates exigem laboratório autorizado; nenhuma publicação ou reinicialização de SCM real é feita por esta alteração.
