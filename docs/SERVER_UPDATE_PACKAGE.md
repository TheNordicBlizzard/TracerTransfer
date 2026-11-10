# Pacote nativo do servidor Linux

`scripts/build_server_archive.py` é independente do empacotador Windows. Só prepara arquivos localmente, sem rede, pip, assinatura, commit, publicação ou alteração de serviços. Não produz EXE.

## Uso

Requer Python 3.12+ e Git. Executar na raiz do checkout:

```bash
python3 scripts/build_server_archive.py --version 0.1.3 --output /caminho/privado/server-candidates
```

No ambiente Windows deste projeto:

```bash
.venv/Scripts/python.exe scripts/build_server_archive.py --version 0.1.3 --output P:/TracerTransfer-Private/qa-server --qa --allow-dirty
```

O diretório de saída deve ficar **fora do repositório**. Saídas existentes são recusadas, não sobrescritas. Para repetir o teste de determinismo, usar outro diretório vazio.

### Release candidate e QA

- Versão estrita `x.y.z`, sem zeros iniciais, prefixo `v` ou sufixos; deve coincidir com `project.version` em `pyproject.toml`.
- Sem `--qa`: exige Git HEAD válido, árvore inteira limpa (incluindo arquivos não rastreados) e cada entrada idêntica aos bytes do commit. Checkout com conversão CRLF pode ser recusado mesmo com status limpo: usar checkout com bytes originais; não normalizar silenciosamente.
- `--qa` marca sempre o recibo `channel=qa-only`, `release_eligible=false` e nome `TracerServidor-0.1.3-linux-QA-ONLY.tar.gz`, mesmo se o checkout estiver limpo.
- Árvore suja exige **ambos** `--qa --allow-dirty`. `--allow-dirty` sem `--qa` é erro. HEAD identifica apenas a base, não os bytes modificados: os hashes do recibo identificam esses bytes.
- Candidate limpo usa `TracerServidor-0.1.3-linux.tar.gz`, `channel=release-candidate`, `release_eligible=true`, mas **sempre** `publication_approved=false`. Isso não autoriza publicação nem atesta testes operacionais.
- Nunca renomear/reutilizar QA como release: reconstruir do commit revisado e limpo. O descritor interno não admite campo QA; o atualizador não conhece esse canal. A barreira QA é de preparação/proveniência/nome e revisão, não uma autorização criptográfica do servidor. Quem assina deve recusar recibos QA e verificar artefato/recibo/fontes independentemente.

## Contrato exato de `server/update.py`

Arquivo gzip com `mtime=0`, sem nome original; tar **USTAR**, membros ordenados, somente arquivos regulares, modo `0644`, uid/gid/mtime zero, uname/gname vazios. Sem diretórios, links, PAX/GNU ou metadados do host. Bytes idênticos com mesmos inputs e implementação Python/zlib; não se promete gzip idêntico entre versões diferentes de zlib.

Allowlist não recursiva:

- `server/*.py` e `updates/*.py`;
- `configuration.py`;
- `requirements.txt`: **bytes exatos de `deploy/linux/requirements.txt`**, não um novo export;
- `server-release.json`: JSON determinístico com exatamente `schema: 1`, `version`, `requirements_sha256` (SHA-256 do requirements incluído), `database_compatible: true`, `migrations: "none"`.

Exige entradas essenciais do backend/updater; impõe os limites do unpacker (2048 membros, 16 MiB por arquivo, 128 MiB expandidos). Rejeita symlinks/junctions em arquivos/ancestrais e arquivos com hardlinks. Não inclui `.env`, perfis de implantação, bancos, spool, certificados, logs, wheels, venv, testes ou recibos no tar.

O recibo adjacente `<arquivo>.build.json` registra commit, dirty, canal, versão, hash/tamanho do tar, hashes/tamanhos de todos os membros e hashes dos inputs. Inclui receita, pyproject e uv.lock como evidência externa, não como payload. Não registra conteúdo, caminho absoluto, ambiente, remotes Git ou diff. Compara snapshots e proveniência antes/depois para recusar mutação detectada. SHA-256 é integridade/proveniência local, **não assinatura**.

## Testes

```bash
.venv/Scripts/python.exe -m pytest tests/test_server_archive.py tests/test_server_updates.py -q --basetemp /caminho/temporario/server-archive-tests
```

Usam repositórios Git isolados, CLI real, comparação byte a byte, inspeção USTAR e `server.update.unpack` / `release_contract` reais. Sem rede/systemctl/produção. Testes POSIX de aplicação do updater são pulados no Windows; isso não substitui homologação Linux. Teste de symlink pode ser pulado por falta de privilégio Windows.

## Limites e gates antes de promover

1. A declaração de compatibilidade de banco e ausência de migrações é uma política deste pacote, **não uma análise automática do código**. Revisar código e compatibilidade antes da assinatura.
2. O atualizador exige SHA-256 do requirements **igual ao baseline instalado**. Lock novo, mesmo semanticamente equivalente (inclusive comentários/line endings), bloqueia atualização automática; preparar/migrar dependências manualmente em procedimento autorizado. Este script não instala nada e não verifica a venv do destino.
3. Tripwires bloqueiam PEM privado, tokens GitHub reconhecíveis e URL HTTP(S) com senha; não são auditoria completa de segredos. Código allowlisted ainda pode conter outros segredos: revisão e auditoria independente continuam obrigatórias. Ignorados fora da allowlist não são copiados.
4. Checkout/saída devem ser privados e sem escritor concorrente hostil. A comparação antes/depois detecta alterações persistentes, não toda troca transitória (ABA); recibo e tar não são transação atômica conjunta em queda de energia. Arquivo sem recibo válido não pode ser promovido.
5. Assinar manifesto Ed25519, validar gates nativos, bootstrap versionado/root-owned, dependências offline, backup e aprovação de produção são etapas separadas. Não habilitar timer nem aplicar este QA em produção.
