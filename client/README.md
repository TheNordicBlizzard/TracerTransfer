# Cliente Windows — ativação e entrega confirmada

Entrada: `python -m client` / `client.__main__.main()`. Dados padrão: `%LOCALAPPDATA%\TracerTransfer\client`. `--data-dir PATH` isola os dados; `--background` abre na bandeja; `--headless` executa sem GUI; `--once` faz uma rodada e imprime JSON, sem pular estabilização.

## Fluxo final

Escolher uma pasta, informar nome e colar exatamente o código `TT-` + 40 dígitos hexadecimais MAIÚSCULOS fornecido pelo responsável. Não se corrigem espaços/casing. A GUI usa o endpoint HTTPS do perfil empresarial mínimo; uma instalação genérica nova sem destino bloqueia ativação e informa o responsável, sem campo de servidor, CA, login ou senha administrativa.

O usuário Windows pode salvar/trocar pasta, pausar/retomar, sair e optar pelo início no login, inclusive offline. Isso não concede permissões centrais. Fechar oculta na bandeja (fallback: minimiza); Sair encerra. Requisições e aquisição do lock rodam fora do loop Tk.

`ActivationService` gera UUID hex e `secrets.token_urlsafe(32)`, protege o segredo DPAPI CurrentUser e salva atomicamente com fsync ANTES de POST `/api/activate`. Retentativas/reinícios reutilizam a identidade. O código nunca é salvo. A resposta deve conter `device_id`, `activation_id`, `role='sender'`; o Bearer é o segredo original, não um token retornado. Papel delivery é rejeitado. Antes de ativar não há envio nem heartbeat.

O inventário de assinaturas de arquivos existentes é persistido ao concluir a ativação e ao trocar pasta. Backlog anterior fica ignorado; arquivos novos/versões alteradas passam pela estabilidade. Fontes nunca são removidas. Configuração contém identidade, baseline, modo, pasta, pausa, intervalos e preferência de autostart. O segredo é DPAPI CurrentUser sem fallback plaintext; executar sob a mesma conta. Configuração não é fronteira de segurança contra o usuário/administrador do SO.

## Fila e acompanhamento

Apenas arquivos imediatos .oma/.vca case-insensitive, 1 a 20 MiB; sem recursão/symlinks/junctions. Duas observações da mesma assinatura separadas por `max(poll_seconds, stable_seconds)` e idade mínima `stable_seconds`.

`queue.sqlite3` e `spool/` conservam snapshots imutáveis limitados, com fsync antes do registro. Hash e corpo vêm do snapshot. Chave caminho+SHA impede repetir o mesmo conteúdo. Retries preservados, backoff 2s a 3600s, até 10 itens elegíveis por rodada; servidor deduplica caso a resposta se perca.

PUT HTTP 202 persiste receipt_id. Estado pending vira awaiting, NÃO sent. GET `/api/files/status/{receipt_id}` consulta o recibo; somente delivered com nome/hash/recibo compatíveis permite sent e remoção do spool. Reinício retoma GET sem novo PUT. Conflict/uncertain preservam spool/erro visível sem reupload automático; HTTP 409 também bloqueia reenvio. 401/403 exibe credencial revogada/sem permissão; nunca corpo HTTP/token/código.

Status: pending (todos não concluídos), awaiting_delivery, sent (entregues), conflicts, uncertain, error, paused, configured, last_heartbeat. Heartbeat em thread/conexão independente a cada 15s. Pausa mantém heartbeat, mas interrompe uploads/consultas até retomar.

Autostart opcional: HKCU Run, valor `TracerTransfer-<16 caracteres de hash do diretório>`, com leitura de verificação. Não é serviço antes do login. Não habilitar simultaneamente outro mecanismo no instalador.

## Compatibilidade interna e testes

`Agent(data_dir, transport=None, clock=time.time)` permite injeções. `ActivationService(..., server_url=..., ca_file=..., transport=...)` aceita HTTPS/CA temporários somente como dependências de testes, nunca opções da GUI. TLS sempre validado; sem redirecionamento/proxy de ambiente/verify=False.

`ConfigurationService.apply(config, auth, old_auth=..., enroll_name=...)` e `Authorizer` estão DEPRECIADOS para provisionamento legado direct/testes internos e não são chamados pela GUI. apply exige revalidação admin antiga/nova e grava delivery_mode='direct'; queued bloqueia cadastro legado. Testes direct declaram explicitamente `Config(delivery_mode='direct', ...)` para respostas antigas 200. O padrão queued não faz downgrade silencioso. A GUI exige ativação queued no endpoint empresarial mesmo ao encontrar configuração legada; credenciais antigas demandam migração pelo responsável.

Ensaios usam dados isolados, DPAPI/SQLite/Tk reais, HTTPS loopback com CA temporária. Teste de autostart injeta adaptador sem gravar HKCU. EXE, instalador, Windows limpo e rede externa são gates separados.

