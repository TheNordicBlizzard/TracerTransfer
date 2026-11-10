# TracerTransfer

Transferência de `.oma` e `.vca` com ativação por dispositivo, HTTPS validado, fila persistente e confirmação de entrega.

## Candidato 0.1.3

Em desenvolvimento/homologação. Preparado, compilado, testado, publicado e implantado são estados distintos. Consulte `CHANGELOG.md` e `docs/RELEASE_AND_ROLLBACK.md`; não reutilize versões para bytes diferentes.

## Componentes

- **Cliente Windows:** monitora pasta, preserva originais, enfileira snapshots e envia por HTTPS. Só uma confirmação de entrega conclui o envio.
- **Servidor Linux:** autenticação, códigos de ativação, dispositivos, fila e painel administrativo.
- **Entregador Windows:** busca pedidos e publica atomicamente na saída. Nome existente com conteúdo diferente gera conflito, sem sobrescrita/renomeação automática.

A 0.1.3 incorpora a aplicação de updates nos próprios programas; não distribui `TracerAtualizador.exe`. O Windows ainda precisa de cópia temporária do mesmo EXE para substituir um arquivo em uso. Entregador continua LocalService; aplicação privilegiada usa tarefa SYSTEM protegida. Backend Linux continua sem escrita no código e usa comando/unidade administrativa de update.

## Instalação e configuração

- Cliente: instalação por usuário, Windows x64, sem Python/VPN/CA global.
- Pacote empresarial: endpoint preparado privadamente; instalar, ativar com código individual e escolher pasta.
- Pacote genérico novo sem destino não está provisionado. Solicite uma distribuição empresarial; não ativar contra um domínio de exemplo.
- Atualizações preservam origem vinculada, fila, DPAPI e preferências. Não transportar identidade para outra conta/PC.
- `deployment.local.json` é perfil administrativo privado, ignorado pelo Git e proibido nos ZIPs. Perfil distribuível contém no máximo `server_url`, sem hosts SSH ou tokens.
- Entregador exige instalação administrativa, identidade própria e volume comum entre staging e saída. Consulte `deploy/windows-delivery/LEIA-ME.md`.

## Atualização e segurança

Feeds separados por componente apontam para artefatos imutáveis. Exigir manifesto Ed25519, componente, versão/commit, expiração, tamanho e SHA-256. Não executar fontes soltos da branch. Hash sozinho não autentica um download.

Nunca embutir tokens GitHub ou chaves privadas. Visibilidade pública só após auditoria; pré-releases não são updates estáveis. Upload exige autenticação do operador, mesmo com download público. Publicação não é implantação.

Piloto sem Authenticode comercial: manifesto assinado não remove SmartScreen. Não desabilitar proteções do Windows.

Painel permanece privado por SSH. DNS administrativo, redesign e alterações de credenciamento/MFA estão adiados.

## Operação

- `.oma`/`.vca` após estabilidade, padrão de 20 MiB. Arquivos anteriores à ativação são ignorados; originais nunca são apagados.
- HTTP 202 é recebido na fila, não entregue. Conflito/incerteza exige análise; não reenviar pedidos cegamente.
- DPAPI CurrentUser vincula o segredo ao usuário Windows. O sistema operacional/administrador local não é uma fronteira de proteção da própria configuração.
- Autostart do cliente no login é diferente de serviço do entregador antes do login.
- Rollback de executável não reverte SQLite, identidades ou recibos.
- Outra VM no mesmo host não é backup offsite. Custódia da chave de assinatura é uma responsabilidade separada.

## Desenvolvimento

```powershell
uv sync --frozen --extra dev --extra build
uv run --frozen python -m pytest -q
```

Receitas de build, empacotamento, gates e recuperação: `docs/RELEASE_AND_ROLLBACK.md`. Use dados isolados; não execute testes destrutivos na pasta industrial.

## Gates de liberação

Fonte testada, inspeção de binários, instalação real, Windows limpo, login/autostart, rede externa e consumo SIOU/FreeForm são verificações distintas. Resultados operacionais e inventários da implantação são privados e não pertencem ao repositório público.
