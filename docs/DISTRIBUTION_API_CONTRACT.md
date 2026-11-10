# Contrato de distribuição Linux — implementação aprovada

Contrato de integração dos componentes. Não contém credenciais reais.

## Compatibilidade
`create_app(data_dir, max_size=..., *, output_dir=None, delivery_mode='direct')`; CLI `--delivery-mode direct|queued`. Direct conserva legado apenas para transição/testes existentes. Linux usa queued: exige ativação/proveniência e bloqueia POST /api/devices legado. Banco/fila locais. Nunca apontar o modo queued diretamente à pasta FreeForm.

## Ativação
- POST `/api/activations` admin: `{name,role:'sender'|'delivery',expires_hours:24}`; retorna `{id,code,role,expires_at}`. GET lista metadados sem códigos/hashes/segredos; DELETE `/{id}` revoga código não utilizado. Dispositivo já inscrito é revogado por DELETE `/api/devices/{id}`.
- Código: `TT-` seguido de 40 dígitos hexadecimais MAIÚSCULOS gerados com secrets.token_hex(20).upper(); validar formato exato, não corrigir códigos recebidos. Armazenar apenas SHA-256.
- POST `/api/activate`: `{code,installation_id,installation_secret,name}`. installation_id UUID hex de 32 caracteres; segredo `secrets.token_urlsafe(32)` gerado e protegido com DPAPI ANTES da requisição. Bearer futuro = installation_secret; servidor só guarda fingerprint. Resposta `{device_id,activation_id,role}`. Não há senha administrativa no cliente. Papel vem do código, não do pedido. Retentativa do mesmo código+installation_id+segredo é idempotente; mudança de segredo/dispositivo, expiração antes do uso ou revogação rejeitam. Validade padrão 24h ajustável. Nenhum código/segredo em logs ou argumentos.

## Remetente
- PUT `/api/files/{filename}` com Bearer sender e X-Content-SHA256: em queued, HTTP 202 e `{receipt_id,sha256,name,duplicate,status}`; estado `pending|delivered|conflict|uncertain`. Direct mantém resposta antiga 200.
- GET `/api/files/status/{receipt_id}`: somente remetente dono, `{receipt_id,name,sha256,status,error}`. Remetente só considera `sent` quando delivered. Recebido na VM não é entregue ao FreeForm. Conservar spool até confirmação final; conflitos ficam visíveis sem gerar novas cópias/requisições de upload desnecessárias.
- Mesmo dispositivo/nome/hash reutiliza o recibo, inclusive depois da confirmação/retentativa. Servidor não aceita/remetente não faz downgrade silencioso do protocolo.

## Entregador
- Credencial/ativação independente, role delivery. Não permite upload de remetente nem administração. Sender não pode listar/baixar entregas de outros.
- POST `/api/delivery/lease` `{limit:1}`: `{items:[{id,name,sha256,size,lease_token,lease_until}]}`. Limite pequeno, lease com expiração, nunca duas leases simultâneas do mesmo nome case-insensitive. Lease token opaco, hash armazenado, nunca logado.
- GET `/api/delivery/files/{id}`, Bearer delivery + `X-Delivery-Lease`: stream binário somente com lease válido e pertencente ao entregador.
- POST `/api/delivery/ack/{id}`: `{lease_token,sha256,result:'delivered'|'conflict'|'uncertain',error:null|codigo_seguro}`. Conferir holder/lease/checksum; confirmação repetida idêntica é idempotente. Não aceitar confirmação arbitrária/por remetente. Conflict/uncertain não são repetidos automaticamente; ação admin explícita para reabrir, se implementada.
- Serviço Windows: journal persistente pré-publicação, staging no volume da pasta final, hash e publicação atômica sem overwrite, idempotência por receipt_id. Depois de journal delivered, apenas repetir ACK, nunca recriar arquivo já consumido pelo FreeForm. Estado publishing + destino ausente após crash => uncertain, não republicar automaticamente. Preservar todos originais.

## Interface e segurança
Painel privado via transporte protegido, admin para códigos/revogação, sessões só em memória. Endpoint público fixo definido no perfil de implantação (ex.: https://tracer.example.com). Testes podem fornecer endpoint HTTPS/CA temporários explicitamente, nunca verify=False. Usuário Windows opera seu cliente local sem login central; não ganha privilégios de servidor. Tokens DPAPI sob a identidade real do processo. Papéis/limites verificados no servidor e em cada operação.

## Divisão de trabalho
Backend: server/database.py, storage.py, app.py, auth.py, __main__.py, activation.py, delivery.py, dashboard.py e testes de servidor/ativação/entrega. Cliente: client/* e testes de cliente. Entregador: delivery/*, launch_delivery.py, packaging/delivery.spec, deploy/windows-delivery/* e seus testes. Integração principal: manutenção, Linux/VM, proxies, instalador/builds, migração e evidências. Cada responsável deve avisar antes de alterar arquivo de outro fluxo. Nenhum subfluxo altera produção/VM/rede.
