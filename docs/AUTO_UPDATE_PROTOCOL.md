# Protocolo de atualização — contrato vigente schema 2

**A seção histórica abaixo é da 0.1.2 e não deve ser usada para builds novos.**
O contrato vigente está em `updates/PROTOCOL.md`, `updates/core.py` e
`docs/RELEASE_AND_ROLLBACK.md`. São três feeds separados:
`update-feed-client.json`, `update-feed-delivery.json` e `update-feed-server.json`.
Cada envelope autoriza exatamente um componente e vincula tag/commit/versionamento.
Não existe componente `updater`, manifesto agregado antigo ou fallback para branch.

Assinatura atual: uma invocação por componente com `--commit <SHA_COMPLETO>` e
`--key-file <DPAPI_FORA_DO_REPO>` obrigatórios; `--client`, `--delivery` ou `--server`.
O caminho antigo de chave em home não é usado implicitamente. A CLI não sobrescreve
saída, não compila nem publica. QA não é promovível a release; drafts/prereleases
não alimentam atualização automática. Auditar antes de abrir o Git ou publicar assets.

## Arquivo histórico schema 1 — não executar as receitas abaixo

## Escopo e identidade

Implementação: `updates/core.py`, versão do pacote `updates.VERSION = '0.1.2'`.
O núcleo **descobre, valida e prepara arquivos; não aplica nem executa EXEs**.
A origem é o repositório público `TheNordicBlizzard/TracerTransfer`, canal `pilot`.
Nenhum token GitHub, segredo de dispositivo ou credencial administrativa integra
este protocolo. Enquanto o repositório estiver privado, HTTP 404 é indisponibilidade
normal, não motivo para adicionar autenticação ao cliente.

A confiança é a assinatura Ed25519, não apenas HTTPS ou o nome do asset.
Chave pública fixada, em base64 (32 bytes):

```text
ahyjfdQIY100LuZktyPretfBo4FUEaUL4YYN8P1/pi0=
```

Alterar essa chave exige uma distribuição confiável do aplicativo; um release não
pode atualizar o próprio pin. A chave privada existente permanece fora do repo,
em `~/.tracer-transfer/release-signing-key.dpapi`, protegida por DPAPI CurrentUser.
Não existe regeneração automática, fallback plaintext ou upload da chave.

## Envelope e canonicalização

Asset obrigatório quando há atualização: **`update-manifest.json`**, UTF-8,
no máximo 128 KiB. JSON duplicado, não finito e campos de schema desconhecidos
são rejeitados. O envelope contém exatamente `payload` e `signature`:

```json
{
  "payload": {
    "schema": 1,
    "repository": "TheNordicBlizzard/TracerTransfer",
    "version": "0.1.2",
    "channel": "pilot",
    "published_at": "2026-11-10T12:00:00Z",
    "expires_at": "2026-11-24T12:00:00Z",
    "artifacts": {
      "delivery": {
        "name": "TracerEntregador.exe",
        "sha256": "<64 caracteres hexadecimais minúsculos do arquivo real>",
        "size": 12345,
        "kind": "exe-delivery"
      }
    }
  },
  "signature": "<assinatura Ed25519 de 64 bytes em base64>"
}
```

O exemplo é estrutural, **não um manifesto verificável**. Assinatura calculada
somente sobre os bytes de:

```python
json.dumps(payload, sort_keys=True, separators=(',', ':'),
           ensure_ascii=False, allow_nan=False).encode('utf-8')
```

A ordenação/espaçamento do envelope não interfere. O envelope original é mantido
integralmente para nova verificação no momento da aplicação.

Datas devem ser ISO-UTC com `Z` ou `+00:00`, com segundos e opcionalmente frações
até seis casas. Publicação futura é rejeitada, sem tolerância de relógio; expiração
igual ou anterior ao instante de verificação é rejeitada. Expiração deve ser
posterior à publicação. O relógio local precisa estar correto.

Versão estrita `x.y.z`, componentes inteiros sem zeros iniciais (salvo `0`), sem
sufixos de pré-release/build, prefixo `v` ou espaços. A tag GitHub é exatamente
`vVERSION`. Comparação numérica: downgrade gera `UpdateRejected`; versão igual
retorna `None` sem baixar/aceitar um artefato. Release draft/prerelease é rejeitado
mesmo quando a versão coincide com a instalada.

`artifacts` contém um subconjunto **não vazio** de:

| Componente | Nome público exato | Kind |
|---|---|---|
| `client` | `TracerTransfer-Cliente-VERSION-windows-x64-pilot-unsigned.exe` | `inno-client` |
| `delivery` | `TracerEntregador.exe` | `exe-delivery` |
| `updater` | `TracerAtualizador.exe` | `exe-updater` |

Cada entrada contém exatamente `name`, `sha256`, `size`, `kind`. Tamanho inteiro
positivo, até 256 MiB por binário; bool não é inteiro válido neste protocolo.
Nome, componente e kind são vinculados. Omissão de client/delivery/updater é válida;
não se exige que componentes opcionais inexistentes sejam publicados.

## Descoberta e transporte

1. GET fixo `https://api.github.com/repos/TheNordicBlizzard/TracerTransfer/releases/latest`.
   Resposta limitada a 1 MiB; API não aceita redirects.
2. Rejeitar draft/prerelease, tag inválida ou downgrade. Versão igual não atualiza.
3. Encontrar um único asset `update-manifest.json`. Ausência é indisponibilidade.
4. Validar `browser_download_url` **antes** do acesso: HTTPS, host `github.com`,
   caminho exato `/TheNordicBlizzard/TracerTransfer/releases/download/vVERSION/NOME`,
   sem credenciais, query inicial, fragmento ou porta diferente de 443.
5. Baixar envelope limitado, verificar assinatura, schema, repo, canal, datas e
   correspondência da versão assinada com a tag descoberta.
6. Vincular **todos** os artefatos assinados à lista de assets da mesma resposta:
   nome único, tamanho exato e URL inicial de release/tag/repo exatos.
7. Baixar somente o componente solicitado. Se ele não está no manifesto, retornar
   `None`. Conferir tamanho e SHA-256 antes da publicação no cache.

HTTPX em produção usa TLS validado, `trust_env=False`, `auth=None`, timeout de
conexão 10s/leitura 30s e `follow_redirects=False`; não lê token do ambiente nem
proxies/CA do ambiente. `http` injetado existe somente para testes herméticos.
Redirecionamentos são tratados manualmente, no máximo cinco por download, apenas
HTTPS em `github.com`, `release-assets.githubusercontent.com` ou
`objects.githubusercontent.com`. Não se aceitam hosts IP, userinfo, portas não
padrão, fragmentos ou backslashes. Query em redirect CDN pode conter a assinatura
pública temporária do GitHub. Não há encaminhamento de Authorization.

Respostas com content-encoding comprimido são rejeitadas (`Accept-Encoding:
identity`). `Content-Length` é conferido quando presente, mas nunca substitui a
contagem incremental por chunk: API, manifesto e binário têm limites próprios.
O tamanho assinado é também o limite efetivo de download do binário.

## API de integração e aplicação

```python
from updates.core import (
    PINNED_PUBLIC_KEY, PreparedUpdate, UpdateUnavailable, UpdateRejected,
    prepare_update, verify_manifest, verify_artifact,
)

prepared = prepare_update('client', current_version, private_cache_dir)
# prepare_update(component, current_version, cache_dir, *, http=None,
#                public_key=PINNED_PUBLIC_KEY, now=None) -> PreparedUpdate | None
```

`PreparedUpdate` tem os campos `artifact: Path`, `version: str`, `component: str`,
`manifest_bytes: bytes` e `artifact_metadata: dict`. `now`, quando injetado em
verificações, é datetime com timezone. `public_key` é base64; produção deve usar o
pin default, nunca uma chave obtida pela rede.

**A preparação não autoriza execução posterior sem nova validação.** O aplicador,
implementado separadamente, deve imediatamente antes de aplicar:

1. Chamar `verify_manifest(prepared.manifest_bytes)` usando o pin e relógio atuais.
2. Revalidar versão instalada/anti-downgrade, componente pretendido e versão de
   destino. Conferir que `payload['version'] == prepared.version`.
3. Obter metadata de `payload['artifacts'][prepared.component]`; não confiar na
   cópia mutável `prepared.artifact_metadata` sem comparar com o payload assinado.
4. Chamar `verify_artifact(prepared.artifact, metadata)` e só então iniciar o
   procedimento específico de aplicação. Nunca executar antes de parar/confirmar
   corretamente a aplicação alvo e cumprir os guards de instalação.

`verify_manifest(envelope_bytes, *, public_key=..., now=None) -> dict` devolve o
payload verificado. `verify_artifact(path, metadata) -> None` rejeita adulterações,
links simbólicos, junctions/reparse points em qualquer ancestral e hardlinks.
Não há execução Windows neste módulo.

## Cache, limites de confiança e erros

Use pasta **dedicada** em dados locais do usuário, nunca pasta compartilhada. O
núcleo rejeita reparse points antes de criar conteúdo e aplica DACL protegida no
Windows (SYSTEM e proprietário; herança restrita para filhos), ou modo 0700 em
POSIX. Cria subpasta imprevisível por tentativa; arquivos abertos exclusivamente,
flush/fsync, rename atômico no mesmo diretório. O EXE só recebe nome final após
verificação. O envelope original fica ao lado como `update-manifest.json`.
Falhas removem staging quando o caminho ainda é seguro; quedas abruptas podem
exigir limpeza posterior de subpastas incompletas. Cache pronto permanece para o
aplicador, que também é responsável por limpeza posterior.

Comparações de identidade de arquivo, tamanho, mtime e link count cercam a leitura;
ctime é comparado dentro da mesma API de stat devido à diferença Windows Python
3.13 entre `lstat` e `fstat`. A verificação não é uma barreira contra administrador
ou processo malicioso executando como o mesmo usuário e disputando paths/handles.
Um aplicador que precise resistir a esse atacante deve verificar e executar um
snapshot protegido com handles/ACLs apropriados. O protocolo não oferece proteção
contra relógio local adulterado ou replay de versão ainda válida superior à versão
instalada; não mantém um contador monotônico de releases.

- `UpdateUnavailable`: offline, timeouts, HTTP não-200 (inclui 404 de repo privado,
  403/429 de rate limit) ou manifesto ainda não publicado. O aplicativo existente
  continua funcionando; exibir indisponibilidade discreta, sem pedir token.
- `UpdateRejected`: assinatura/schema/validade/tag/URL/redirect/tamanho/hash/path
  inválido, downgrade ou cache que não pode ser protegido. Não aplicar nem tentar
  contornar as verificações.
- `None`: mesma versão ou componente ausente no manifesto válido de release nova.

## CLI local de assinatura

No usuário Windows proprietário da chave DPAPI já provisionada:

```bash
.venv/Scripts/python.exe scripts/sign_update_release.py \
  --version 0.1.2 \
  --client dist/installers/TracerTransfer-Cliente-0.1.2-windows-x64-pilot-unsigned.exe \
  --delivery dist/TracerEntregador.exe \
  --updater dist/TracerAtualizador.exe \
  --key-file ~/.tracer-transfer/release-signing-key.dpapi \
  --output CAMINHO_LOCAL/update-manifest.json \
  --days-valid 14
```

Passe somente componentes existentes: pelo menos um dos três, os demais podem
ser omitidos. O caminho local de entrada pode ter outro basename; o nome público
assinado é sempre o nome canônico da tabela e deve ser usado no upload. Validade
aceita de 1 a 365 dias. A CLI valida o pin derivado da chave, recusa chave dentro
do repo e não sobrescreve o arquivo de saída. A saída atômica é apenas o manifesto
público; mensagens de falha são sanitizadas, sem valores de exceção/segredo.
Ela não cria chave, não compila, não publica release e não altera credenciais.
A assinatura Ed25519 deste protocolo **não é Authenticode**; EXEs continuam
`pilot-unsigned` para o sistema operacional.

## Testes

```bash
.venv/Scripts/python.exe -m pytest tests/test_updates_core.py -q \
  --basetemp /tmp/tracer-update-core-tests
```

A suíte usa HTTPX MockTransport e chaves efêmeras **somente para testes**; não
substitui o pin/chave reais. Exercita assinatura/payload adulterados, schema/datas,
hash/tamanho, componentes opcionais, downgrade, mesma versão, draft/prerelease,
URL/tag/repo, redirects hostis/permitidos/limite, offline/404/403/429, limites por
chunks, cache com junction Windows real, hardlinks, opções HTTP de produção e CLI.
Não publica nem executa um artefato baixado.
