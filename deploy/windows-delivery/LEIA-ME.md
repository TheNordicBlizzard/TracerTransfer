# TracerEntregador 0.1.0 — piloto administrativo não assinado

Componente Windows de entrega HTTPS da fila Linux para uma pasta local. Não é o instalador do cliente e não deve ser distribuído aos remetentes.

**Não instalar em produção antes do corte controlado para o backend Linux.** O hostname público ainda usa o receptor Windows anterior. A homologação deste pacote ocorreu com o EXE/serviço real no Windows do laboratório e uma pasta isolada, não em `C:\FreeForm\Tracer`.

## Conteúdo e requisitos
- EXE autossuficiente em `dist/TracerEntregador.exe`; não requer Python no receptor.
- Scripts/XML e WinSW.NET4.exe oficial 2.12.0 em `deploy/windows-delivery/`; os caminhos padrão funcionam após extrair a árvore completa.
- `manifest.json` contém SHA-256/tamanho dos arquivos. WinSW também tem hash fixado nos scripts e licença MIT incluída.
- Windows com privilégio administrativo para instalar o serviço, saída HTTPS e volume local comum entre dados/staging e destino. Não usa SMB nem abre listener no Windows.
- Conta LocalService, SID exclusivo TracerTransferDelivery, dados privados e DPAPI CurrentUser sob a identidade real do serviço. Não ativar previamente como administrador.

## Instalação controlada
1. Inventariar a pasta operacional existente e os SIDs dos consumidores FreeForm. Não presumir que o grupo Administradores garante leitura ao programa não elevado.
2. Gerar no painel privado um código **delivery**, separado dos códigos **sender** dos clientes. Provisionar `bootstrap.json` com `code` e `name` por canal administrativo seguro, DACL protegida permitindo apenas SYSTEM e Administradores. Código não entra no ZIP, chat, argumento de processo ou log. Endpoint de produção já é o padrão no EXE; `server_url`/`ca_file` adicionais são exclusivos de testes explicitamente autorizados.
3. Extrair todo o pacote; obter o hash do EXE em `manifest.json`. Executar em PowerShell elevado, primeiro com `-ValidateOnly`, usando os parâmetros abaixo. Remover `-ValidateOnly` somente na janela aprovada.

```powershell
.\deploy\windows-delivery\Install-Delivery.ps1 `
  -ExpectedBinarySha256 '<SHA256 do EXE no manifesto>' `
  -BootstrapPath '<arquivo privado de provisionamento>' `
  -ConsumerReadSids '<SID efetivamente inventariado>' `
  -ValidateOnly
```

O destino padrão é `C:\FreeForm\Tracer`; deve existir e seus arquivos são preservados. Não trocar esse destino durante o corte sem revisão. O código é resgatado pelo próprio serviço; o bootstrap é consumido após sucesso, sem promessa de apagamento seguro.

## Operação, atualização e retirada
- `status.json` registra estado genérico; `delivery.sqlite3` mantém journal durável. Sucesso de SCM não substitui polling real, chegada do arquivo/hash e status final no remetente.
- Nome igual e conteúdo igual é idempotente. Conteúdo diferente, arquivo ilegível ou destino inseguro bloqueia a entrega, sem renomear/sobrescrever. Comparação de nomes considera casing Windows.
- Journal entregue só repete ACK; não recria trabalho já consumido. Crash em publicação com arquivo ausente resulta em incerto/intervenção, não promessa de exactly-once industrial.
- `Update-Delivery.ps1 -ExpectedBinarySha256 ... -ValidateOnly` valida antes da atualização. Arquivo anterior fica como `.previous`; identidade e journal não são restaurados para versões antigas. Arquivar/verificar backup anterior antes de nova atualização.
- `Uninstall-Delivery.ps1 -ValidateOnly` valida retirada. Retirada real preserva saída, credencial DPAPI e journal, remove somente grants explícitos do SID do serviço no destino e admite retomada se SCM já foi removido. Revogar o dispositivo no painel ao aposentar.

## Limitações da entrega
Piloto sem assinatura Authenticode. Não homologa reboot Windows, Windows limpo, corte público, consumo/interpretação FreeForm nem backup externo. Testes de confirmação perdida/TLS usam também um servidor sintético local, explicitamente separado da integração real com a VM. Consulte `docs/DELIVERY_WINDOWS_VALIDATION.md` no projeto para evidência completa.
