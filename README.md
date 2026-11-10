# TracerTransfer

Clientes Windows e backend para transferência de arquivos **.oma** e **.vca**, com identidades revogáveis. Projeto independente; não altera o site existente.

## Distribuição Linux/Windows — corte concluído, piloto não assinado

**Interfaces integradas, piloto 0.1.1:** painel novo aplicado na VM com backup e leitura de confirmação, sem publicar administração. Cliente versionado e atualizado de 0.1.0 para 0.1.1 em ensaio instalado, preservando identidade/fila e concluindo entrega. Ativação pelo botão da GUI passou contra backend/TLS reais em laboratório. Pacotes antigos preservados; `docs/UI_REDESIGN_VALIDATION.md` registra o candidato anterior. Assinatura, Windows limpo, login/reboot real e processamento industrial continuam gates separados.

Backend e conector público agora executam na VM Debian em modo queued. O serviço Windows `TracerTransferDelivery`, LocalService, entrega na pasta de destino configurada (por padrão `C:\FreeForm\Tracer`). Corte e reversão foram exercitados; receptor/proxy/conector Windows anteriores ficaram parados/desativados, preservados para contingência. **Não iniciá-los em paralelo com o novo caminho.** Painel administrativo permanece privado por SSH. Uso piloto acompanhado; distribuição ampla ainda bloqueada por assinatura/gates restantes.

- Instalador por usuário: `dist/installers/TracerTransfer-Cliente-0.1.1-windows-x64-pilot-unsigned.exe`, explicitamente não assinado. Ativação obrigatória, endpoint predefinido, controles locais sem senha central, início no login opt-in.
- Pacote administrativo separado: `release/TracerEntregador-0.1.0-Piloto-Windows.zip`. Serviço LocalService; não distribuir aos remetentes.
- ZIP para o piloto dos remetentes: `release/TracerTransfer-Cliente-0.1.1-Piloto.zip`, somente instalador, instruções e hashes. Cadastros JSON/ZIPs antigos estão descontinuados; cliente legado exige migração controlada, não reaproveitar credencial como código.
- Evidências atuais: `docs/RELEASE_0.1.1_VALIDATION.md`, `docs/RELEASE_0.1.1_RESULT.json` e `docs/DISTRIBUTION_VALIDATION.md`; os documentos de corte/ciclo 0.1.0 ficam como histórico. Transporte completo e upgrade comprovados não substituem interpretação industrial, assinatura, Windows limpo ou reboot.

## Atualização automática — candidato local 0.1.2

- Releases/binários atuais 0.1.1: [piloto privado não assinado](https://github.com/TheNordicBlizzard/TracerTransfer/releases/tag/v0.1.1). Instalador, cliente portátil e entregador foram anexados; o receptor Windows legado não foi publicado.
- Candidato **0.1.2 local, ainda sem novo commit/release**: `dist/installers/TracerTransfer-Cliente-0.1.2-windows-x64-pilot-unsigned.exe` e `release/TracerTransfer-Cliente-0.1.2-Piloto.zip`. Inclui cliente e `TracerAtualizador.exe`; entregador/helper separados em `dist/0.1.2/`.
- Consulta de releases públicas sem token, com intervalo de seis horas e backoff. Repositório ainda privado retorna 404 sem interromper transferências. Não houve mudança de visibilidade.
- Só aceita versão nova publicada, não marcada como pré-release, com `update-manifest.json` Ed25519 válido para o pin embarcado; verifica origem, versão, validade, componente, tamanho e SHA-256 antes de baixar/aplicar. Assinatura do manifesto **não é Authenticode** nem elimina SmartScreen.
- Uma instalação inicial da versão com atualizador é necessária; 0.1.1 não ganha esse recurso remotamente. Cliente instalado/ativado usa helper destacado entre ciclos e preserva dados; portátil/perfil personalizado/`--once` não instala automaticamente.
- Entregador usa opt-in administrativo separado: tarefa SYSTEM protegida, mantendo o serviço LocalService sem escrita nos binários. Baseline da versão instalada é explícita; serviço parado não é iniciado pelo atualizador. Habilitação e homologação SCM/SYSTEM em produção **ainda não executadas**. Backend Linux não é EXE nem é atualizado por esse mecanismo.
- Verificação do candidato: **374 passed, 18 skipped, 1 warning** no Windows; os 17 testes POSIX do backup também passaram separadamente no Linux. Upgrade real com helper/Inno de 0.1.2 para uma variante 0.1.3 exclusivamente de QA preservou fila/DPAPI e removeu a instalação de ensaio. Não é prova de Windows limpo ou de atualização via GitHub público.
- Protocolo/operador: `docs/AUTO_UPDATE_PROTOCOL.md`, `docs/AUTO_UPDATE_WINDOWS.md`, `scripts/sign_update_release.py`. Chave privada fica fora do repositório, protegida por DPAPI/DACL; definir custódia recuperável antes de distribuição ampla. Registros privados: `docs/RELEASE_0.1.2_VALIDATION.md` e `docs/RELEASE_0.1.2_RESULT.json`.

## Componentes

- `TracerCliente.exe`: pasta monitorada, fila persistente, ativação individual e controles locais do usuário Windows, sem administração global.
- Backend Linux: API, ativações, dispositivos, fila e painel privado; modo queued separa recebido na VM de entregue no Windows.
- `TracerEntregador.exe`: busca autenticada HTTPS, journal e publicação local atômica sem sobrescrita.
- `TracerAtualizador.exe`: aplicador Windows destacado, com verificação Ed25519 e hash; não contém token do GitHub.
- `TracerServidor.exe`: receptor Windows anterior, mantido para transição e rollback; não executar dois receptores autoritativos.
- **Caddy**: componente separado para servir HTTPS em produção. O servidor da aplicação fica em `127.0.0.1:8765`, sem exposição direta à internet.

Os executáveis não exigem Python instalado. A compilação exige o ambiente de desenvolvimento. Binários inicialmente sem assinatura digital podem gerar avisos do SmartScreen; não desative a proteção do Windows.

## Modelo de segurança

**Usuários** e **dispositivos** são identidades diferentes:

- Usuários entram com nome e senha no servidor central. Administradores gerenciam configurações, usuários e dispositivos; operadores consultam monitoramento.
- O dispositivo envia arquivos com uma credencial própria, revogável. O envio não depende de manter um usuário conectado.
- Senhas de usuários usam Argon2id no servidor; sessões de usuário expiram e não devem ser persistidas no cliente.
- No cliente final ativado, pasta/pausa/encerramento/início no login são controles locais, sem senha administrativa central. Indisponibilidade do servidor preserva configuração/fila; não concede administração nem troca arbitrária de endpoint.
- O segredo do dispositivo usa DPAPI no Windows e fica associado à conta Windows que o cadastrou. Execute o cliente e sua tarefa automática sob essa mesma conta.
- Administradores locais do Windows podem modificar arquivos e finalizar processos. Login do aplicativo não é uma barreira contra administradores do sistema operacional. Restrinja também permissões NTFS e proteja backups e discos; DPAPI não é garantia de criptografia de todos os dados.
- O endpoint do cliente final é predefinido. Migração de origem exige procedimento administrativo controlado, não uma senha alternativa offline ou campo livre na interface.

## Preparação legada do laboratório (alternativa de publicação direta)

O roteiro abaixo descreve a implantação anterior e seus artefatos. Não usar o fluxo de login/configuração central para o novo cliente por ativação; não reaplicar DNS/NAT sobre o túnel existente.

**Implantação atual:** Cloudflare Tunnel na VM Linux, sem abertura de portas no roteador. Backend/proxy em loopback, entregador Windows automático. **Novos recebimentos entram na fila Linux (202) e chegam em `C:\FreeForm\Tracer` com nome original; a confirmação Windows distingue delivered/conflict/uncertain.** Mesmo conteúdo é idempotente; conflito não sobrescreve nem renomeia. Os documentos de saída direta/serviços Windows preservam o histórico anterior e remetem ao corte atual. O roteiro abaixo é legado/alternativo: não aplicá-lo sobre o túnel atual.

1. Escolha um subdomínio, por exemplo `tracer.suaempresa.com.br`. Não substitua os registros do site principal.
2. Configure o registro DNS para o IP público real do laboratório; se o IP mudar, providencie atualização do registro.
3. Reserve um IP LAN para o Windows receptor. Configure encaminhamento TCP 80 e 443 para o Caddy e regras restritas de firewall. **Não exponha a porta 8765.**
4. Copie os executáveis para uma pasta permanente. Inicie o servidor localmente e crie o primeiro administrador no assistente. Não existe senha padrão; não publique o serviço antes desse passo.
5. Instale Caddy a partir da distribuição oficial, adapte `deploy/Caddyfile.example` e mantenha o processo funcionando. O certificado público exige domínio real e validação acessível.
6. Acesse `https://SEU-SUBDOMINIO` e autentique-se. Cadastre usuários conforme necessidade; no cliente, configure esse endereço, faça login administrativo e escolha a pasta do tracer.
7. Envie um arquivo real de teste de uma rede externa, confira o monitoramento e compare os bytes recebidos antes de liberar uso operacional.

IP público, DNS correto e porta aberta são etapas diferentes. Receber na rede local não comprova recebimento de outra rede. NAT loopback pode ser necessário para o mesmo domínio funcionar dentro do laboratório; alternativamente use DNS interno consistente com o certificado.

## Funcionamento dos arquivos

- Apenas `.oma` e `.vca`, sem subpastas. Arquivos ainda em gravação aguardam estabilidade; o produtor deve preferir escrever em temporário e renomear ao finalizar.
- Os originais são preservados. Mudança no conteúdo gera uma nova versão.
- O servidor confere SHA-256 e só publica após recebimento completo. Repetições do mesmo nome/conteúdo no mesmo dispositivo não devem criar cópias extras.
- Nomes incompatíveis com o Windows e arquivos acima do limite são rejeitados. O limite padrão é 20 MiB por arquivo.
- Falhas de conexão causam novas tentativas, não exclusão do arquivo.
- O monitoramento distingue última comunicação de sucesso de transferência. Dados antigos não comprovam disponibilidade atual.

## Inicialização automática (opcional)

**No Windows, o serviço atual é `TracerTransferDelivery`. `TracerTransferServer`, `TracerTransferProxy` e `Cloudflared` antigos estão Disabled/Stopped: não ligá-los sem o procedimento de contingência e reconciliação.** Não executar tarefas legadas em paralelo. O serviço usa WinSW, LocalService e SID próprio; boot real do receptor foi conferido em 10/11, com processo em sessão 0 e contato autenticado posterior. Isso não comprova início do cliente no login em outra estação. As opções abaixo são alternativas legadas, não os serviços atuais.

A interface do cliente também oferece início automático por usuário, usando a chave **HKCU Run**; o cliente abre na bandeja. Os scripts abaixo são uma alternativa via Agendador de Tarefas. **Escolha apenas um mecanismo**, para não criar entradas duplicadas. Desative pelo mesmo mecanismo usado para ativar. O mutex impede que duas instâncias usem a mesma fila.

As tarefas são **por usuário e iniciam no login do Windows**, não antes dele. Não são serviços Windows. O servidor é iniciado em modo `--headless`; o cliente usa `--background`. Mantenha uma conta operacional apropriada; não habilite login automático inseguro para contornar essa limitação.

Os scripts legados abaixo pedem login administrativo central via diálogo do Windows e validam HTTPS antes de alterar tarefas. Não são o instalador final por usuário e não devem criar tarefa paralela ao HKCU Run do novo cliente. Não passe senhas pela linha de comando.

```powershell
.\scripts\install-client.ps1 -ServerUrl https://SEU-SUBDOMINIO
.\scripts\install-server.ps1 -ServerUrl https://SEU-SUBDOMINIO
```

Eles registram as tarefas, mas não as iniciam durante a instalação. A conta deve ter permissão no Agendador de Tarefas conforme a política local. Para remover apenas a inicialização, preservando dados:

```powershell
.\scripts\uninstall-client.ps1 -ServerUrl https://SEU-SUBDOMINIO
.\scripts\uninstall-server.ps1 -ServerUrl https://SEU-SUBDOMINIO
```

Caddy precisa de inicialização própria. As tarefas acima não instalam Caddy nem alteram DNS, roteador, firewall ou certificados.

## Desenvolvimento e compilação

No diretório do projeto, com `uv` instalado:

```powershell
uv sync --python 3.13 --extra dev --extra build
uv run python -m pytest -q
uv run python -m tests.integration_auth
uv run python -m tests.integration_transfer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts/build.ps1
uv run python -m tests.smoke_executables
```

A compilação produz `dist/TracerCliente.exe`, `dist/TracerServidor.exe`, `dist/TracerEntregador.exe` e `dist/TracerAtualizador.exe`. O instalador 0.1.2 exige cliente e helper compilados, Inno Setup confiável e escolha explícita de canal em `scripts/build_installer.ps1`; consultar `docs/CLIENT_INSTALLER_VALIDATION.md` para os comandos reais. Arquivos `packaging/*.spec` incluem as dependências necessárias. Os testes TLS usam uma autoridade de teste temporária explicitamente confiada somente naquele teste, sem desativar verificação de certificados nem instalar CA no Windows.

## Operação e recuperação

- Mantenha backup coordenado do banco do servidor e dos arquivos recebidos. Não copie um SQLite ativo ignorando seus arquivos WAL: use backup SQLite ou pare o processo de forma controlada.
- Backup adicional VM106→VM105 automatizado por `tracer-backup-export.timer`, com chave dedicada/forced-command, retenção e reserva de espaço. Execução agendada de prova produziu arquivo restaurado com manifesto/hash e SQLite `ok`; monitoramento preservado. Ambas as VMs estão no mesmo Proxmox: isto não é offsite nem backup completo da pasta industrial Windows. Evidências privadas em `docs/AUTOMATIC_OFFVM_BACKUP_VALIDATION.md` e `docs/RELEASE_0.1.2_RESULT.json`.
- Revogue dispositivos perdidos no servidor. Redefina senhas e desative contas comprometidas; não reutilize a senha de um usuário como credencial de dispositivo.
- A recuperação do administrador é local ao servidor, com o serviço parado; não existe endpoint público de recuperação administrativa.
- Copiar arquivos DPAPI para outro usuário/computador pode torná-los indecifráveis; recadastre o dispositivo sob a identidade Windows correta.
- Não envie bancos, arquivos de configuração reais, senhas ou tokens em chamados de suporte. Logs não devem conter credenciais.

## Estado de validação

Veja `docs/validation.md` para o histórico e `docs/RELEASE_0.1.1_VALIDATION.md` para o estado vigente. Regressão integrada 0.1.1: **265 passed, 1 skipped, 1 warning**; subset Linux 61 passed/1 warning. Instalação/upgrade, transporte, assinatura, Windows limpo, boot e rede externa são gates distintos; não anunciar distribuição ampla liberada.
