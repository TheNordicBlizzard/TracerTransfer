# Atualização automática Windows — piloto 0.1.2

## Limites e confiança

- O cliente e o **entregador Windows** são EXEs separados. O backend Linux não é um EXE; este recurso não reinstala nem reativa o antigo servidor Windows.
- Descoberta pública, sem token, em `TheNordicBlizzard/TracerTransfer`. Repositório privado/HTTP 404, indisponibilidade ou rate limit são estados de atualização indisponível: transferências e heartbeat continuam.
- Somente `update-manifest.json` com assinatura Ed25519 válida para a chave pública fixada autoriza os artefatos. SHA-256/tamanho validam os bytes **depois** da assinatura; o checksum do GitHub sozinho nunca autoriza executar um download. Nunca incluir chave privada no código, instalador ou máquina de destino.
- O helper verifica novamente assinatura, validade temporal, versão, componente e bytes imediatamente antes da aplicação. Não há URL, comando, switches de instalador ou diretório de instalação arbitrários no pedido local do cliente.
- Trata-se de piloto não assinado com Authenticode. A assinatura do manifesto não elimina alertas do Windows/SmartScreen e não exige desativar proteção.

## Build

No checkout de desenvolvimento Windows, com as dependências de build instaladas:

```powershell
uv sync --extra build --extra dev
uv run python -m PyInstaller --noconfirm --clean packaging/updater.spec
# ou todos os EXEs (inclui o servidor legado como artefato de build, NÃO implantação):
.\scripts\build.ps1
```

Resultado do helper: `dist\TracerAtualizador.exe`, onefile, console habilitado, Python/HTTPX/cryptography incorporados e `deploy/windows-delivery/Update-Delivery.ps1` embutido como dado. Não requer Python no alvo. O compilador Inno deve receber `/DUpdaterExe="<caminho absoluto do helper>"` além das macros existentes. `packaging/client.iss` mantém a macro opcional para compatibilidade com builders anteriores; **sem ela, uma instalação nova não terá atualização automática**. O builder de release deve torná-la obrigatória para a distribuição 0.1.2 com auto-update.

O build não publica releases nem autoriza uso em produção. Produzir os hashes/manifesto assinado no fluxo separado de release, com a chave privada fora do repositório.

## Cliente por usuário

**Bootstrap manual uma vez:** o cliente 0.1.1 já distribuído não possui código de auto-update. Encerrá-lo pela bandeja e instalar manualmente a versão com helper. Uma instalação antiga não passa a atualizar magicamente só porque uma release foi publicada.

A atualização só é habilitada se:

1. O programa é frozen e está exatamente em `%LOCALAPPDATA%\Programs\TracerTransfer\Client\TracerCliente.exe`.
2. O perfil é o padrão `%LOCALAPPDATA%\TracerTransfer\client`.
3. Há `TracerAtualizador.exe` instalado na mesma pasta do cliente.
4. O perfil está ativado e autorizado no modo de fila/servidor fixo.
5. Não está em `--once`. Código Python, executável portátil e perfis personalizados ficam fora.

Uma thread de descoberta independente verifica a cada seis horas; indisponibilidade usa retry de 15 min com backoff limitado a seis horas. Não adquire o lock da fila nem compartilha a conexão de heartbeat. Depois de preparar um download verificado, o agente só aplica **entre ciclos**, sob seu lock de fila, após revalidar a ativação.

O helper instalado é copiado para um pedido único em `client\updates\request-*`, evitando bloquear o próprio EXE instalado. Ele abre um **handle Windows do processo exato**, confere o caminho do executável e confirma prontidão antes de o cliente fechar. A UI observa um evento e encerra Tk/bandeja na própria thread. O helper aguarda a saída sem `taskkill`, salva cópias do primeiro cliente/helper e então executa exclusivamente:

```text
<instalador verificado> /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=<mesma instalação> /TASKS=
```

A fila SQLite, spool, DPAPI, configuração, pasta de origem e HKCU Run ficam intocados. `/TASKS=` não muda a preferência de autostart já gravada. Não há reboot. Somente saída 0 do instalador permite reiniciar o cliente na mesma pasta e com os mesmos dados, usando `--background --data-dir <perfil padrão>`.

### Estados e recuperação do cliente

- `client\updates\status.json`: `current`, `prepared`, `unavailable`, `rejected`, `failed` ou `apply_failed`.
- Cada pedido retém `manifest.json`, `request.json`, payload, helper destacado e `state.json`: `waiting`, `installing`, `installed`, `complete` ou `failed`.
- `request-*\backup\TracerCliente.exe` e `TracerAtualizador.exe` são cópias únicas anteriores à instalação. Um pedido já aplicado/falho não pode sobrescrevê-las.
- Falha do instalador restaura somente esses binários e **não** reinicia automaticamente o cliente. Os dados nunca são revertidos. Saída de reboot requerido também é tratada como falha.
- Queda abrupta em `installing` exige inspeção manual. Encerrar apenas os processos exatos dessa instalação; preservar pedido/backups; restaurar os dois binários com o cliente fechado ou reinstalar manualmente um instalador conhecido e autorizado. Não excluir dados nem restaurar uma fila antiga. Metadados do Inno/atalhos/desinstalador podem requerer reparo por reinstalação após uma instalação parcial: o rollback automático é dos binários, não uma transação completa do Windows.
- Backups/pedidos são mantidos para recuperação; o administrador deve arquivar/remover evidências antigas após conferir funcionamento e espaço. Não há rotação automática destrutiva.

## Entregador: opt-in administrativo separado

O serviço continua `TracerTransferDelivery` / **LocalService**, com WinSW 2.12.0 de SHA-256 fixado. Não elevar a conta de serviço nem conceder escrita em `bin`/wrapper/configuração. Somente uma tarefa separada **SYSTEM** aplica atualizações, instalada por administrador mediante `-Enable` explícito.

Não executar estes comandos em produção sem autorização específica e inventário. O helper é fornecido localmente; `ExpectedUpdaterSha256` deve ser um pin obtido por canal confiável (release previamente verificada), não calculado de um download desconhecido e aceito como autenticação.

```powershell
# Primeiro, validação sem instalar tarefa ou mudar arquivos/serviço/ACL:
.\deploy\windows-delivery\Enable-AutoUpdate.ps1 `
  -BaseDirectory 'C:\ProgramData\TracerTransfer-Delivery' `
  -UpdaterPath '.\dist\TracerAtualizador.exe' `
  -ExpectedUpdaterSha256 '<SHA256 previamente autenticado>' -ValidateOnly

# Somente após autorização administrativa explícita, no alvo correto:
.\deploy\windows-delivery\Enable-AutoUpdate.ps1 `
  -BaseDirectory 'C:\ProgramData\TracerTransfer-Delivery' `
  -UpdaterPath '.\dist\TracerAtualizador.exe' `
  -ExpectedUpdaterSha256 '<SHA256 previamente autenticado>' `
  -InstalledVersion '0.1.1' -Enable
```

O script verifica caminho/nome/conta do serviço, pin do wrapper, configuração XML, ativação existente, ausência de junctions e escrita/ownership dos destinos privilegiados. `updater` tem DACL protegida exclusiva SYSTEM/Administrators; LocalService não ganha escrita. Se uma instalação antiga apresentar ownership/permissões incompatíveis, a validação falha: inventariar/corrigir explicitamente, não ampliar permissões como workaround.

A tarefa `TracerTransferDelivery-AutoUpdate` roda a cada seis horas, primeira execução cinco minutos após habilitar, sem instâncias concorrentes, com três retries de 15 minutos. Para suspender, um administrador pode usar `Disable-ScheduledTask -TaskName TracerTransferDelivery-AutoUpdate` e conferir `Get-ScheduledTask` (nenhuma tarefa é instalada pelos testes).

O helper invoca `prepare_update('delivery', versão_instalada, cache_privado)` usando exclusivamente a versão persistida em `updater\installed_version`. Na primeira habilitação, `-InstalledVersion` é obrigatório e deve corresponder ao entregador realmente instalado (o `0.1.1` acima é exemplo, não inferência). A versão do helper não é a versão do entregador. Sem baseline válido, a execução falha antes de consultar/baixar ou substituir o EXE. Uma baseline existente não é sobrescrita pela reinstalação do helper. O arquivo só avança após readiness bem-sucedida. `updater\state.json` registra `current`, `unavailable`, `rejected`, `applying`, `failed` e `complete`. A atualização automática do entregador troca seu EXE, não o helper privilegiado; mudanças futuras do protocolo/chave/aplicador exigem atualização administrativa explícita do helper.

O script embutido para o serviço:

- Para o serviço exato e aguarda `Stopped`.
- Cria backup de EXE distinto `bin\TracerEntregador.exe.previous.<GUID>`; não bloqueia updates futuros por existir `.previous` e não apaga a primeira evidência.
- Confere os bytes copiados, troca somente o EXE e inicia o serviço.
- Aguarda até 90 segundos um novo `data\status.json` com `poll_ok` posterior ao início da tentativa; SCM `Running` sozinho não basta.
- Em falha, restaura somente o EXE anterior, inicia e verifica readiness novamente. Não restaura SQLite, identity, journal, recibos ou staging.
- Não escreve/limpa `C:\FreeForm\Tracer`, ordens ou pasta operacional. O entregador atualizado continua usando os caminhos já definidos no XML existente.

## Verificação e limites de evidência

```powershell
uv run python -m pytest tests/test_updates_windows.py -q `
  --basetemp "$env:LOCALAPPDATA\hermes\profiles\code-architect\cache\scratch\updates-windows-pytest"
uv run python -m py_compile updates/windows.py updates/runtime.py launch_updater.py client/agent.py client/config_ui.py client/__main__.py
```

A suíte usa locks/handles Windows reais, junction real, cópias reais e subprocessos controlados para sucesso/falha/rollback; também parseia os scripts via parser PowerShell, sem executá-los. As fixtures de aplicação substituem explicitamente autorização de release e caminhos instalados: elas não representam uma instalação Inno/SCM real. A rejeição de envelope não assinado usa a chave pública real do produto. A validação criptográfica positiva pertence à suíte do núcleo.

Ainda exigir gate separado: instalador com cliente/helper reais, upgrade/restart/autostart efetivos, falha de Inno, serviço SYSTEM/LocalService e rollback em ambiente autorizado, mais Windows limpo quando disponível. Compilar e testar fixtures localmente não comprova esses gates. Nenhuma execução em produção é necessária para os testes acima.
