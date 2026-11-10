# Changelog

## [0.1.3] — Candidato em desenvolvimento/homologação

### Correções e escopo
- Reconciliar as cópias divergentes e consolidar fontes/configurações exclusivos em P, com snapshots privados e hashes antes da limpeza de C.
- Carregamento estrito de implantação: ausência de destino impede ativação, perfil explícito inválido não escolhe fallback; atualizações preservam origem vinculada.
- Eliminar o quarto produto de atualização; modos internos do Cliente/Entregador e comando nativo Linux, com privilégio separado, assinatura e recuperação sem rollback de dados.
- Gerar pacotes por allowlist, sem perfil administrativo, credenciais ou payloads industriais; WinSW exige caminho/pin explícitos.
- Auditoria de histórico/índice/pacotes e rastreabilidade commit–artefato antes de abertura pública do GitHub.

### Limites de homologação
- Integração local inicial: 467 testes aprovados, 20 skips e um warning de dependência; EXEs reais exercitados em HTTPS de laboratório com fila, idempotência e conflito sem sobrescrita.
- Linux: 16 testes aprovados com UID 0 em árvore/venv isoladas; novas units verificadas com `systemd-analyze verify`. Isso não habilita o timer nem comprova bootstrap da instalação legada.
- Migração: snapshots e hashes conferidos, ambiente virtual recriado no destino, chaves dedicadas validadas e origem de desenvolvimento removida; atalhos administrativos atualizados sem expor o painel.
- Upgrade real do Cliente 0.1.3 para 0.1.4 QA-only aprovado com Inno real e cópia do próprio EXE: handshake, backup de binário, hash novo, origem, configuração, DPAPI e fila preservados. Instalação de laboratório removida; rollback de Inno e Windows limpo continuam gates separados.
- Arquivo Linux QA determinístico validado contra descritor e hashes dos 19 membros; QA não é promovível a release.
- Builds finais `0.1.3` compilados a partir de commit limpo; a auditoria (`scripts/audit_publication.py` + Gitleaks) passa com **0 achados (`PASS`)** na worktree atual, no índice, em todo o histórico Git e em todos os artefatos/pacotes `0.1.3`.
- Aplicação do entregador sob SYSTEM/LocalService exige laboratório administrativo; token Windows atual não é elevado. Não é comprovação de implantação industrial.
- Painel por DNS, redesign e MFA adiados. Authenticode continua adiado.
- Recuperação e gates: `docs/RELEASE_AND_ROLLBACK.md`. Não reutilizar a versão 0.1.2 para bytes novos nem apagar o histórico.

---

Todas as alterações notáveis neste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
e este projeto adere ao [Versionamento Semântico](https://semver.org/lang/pt-BR/).

---

## [0.1.2] - 2026-11-10

### Adicionado
- **Motor de Atualização Automática Segura (`updates/core.py`):**
  - Consulta a releases no GitHub sem necessidade de token pessoal para repositórios públicos.
  - Validação criptográfica de manifestos via assinatura digital **Ed25519** com chave pública pinada.
  - Verificação de integridade dos binários por hash **SHA-256** e proteção rigorosa contra downgrade.
- **Orquestrador de Atualização Desacoplado (`TracerAtualizador.exe`):**
  - Helper executável independente para aplicar atualizações sem conflito de arquivo em uso.
  - Espera determinística de liberação de processo por handles nativos do Windows.
  - Execução silenciosa de instaladores Inno Setup mantendo dados, configurações e tokens DPAPI.
- **Loop em Background de Atualização (`updates/runtime.py`):**
  - Descoberta periódica desacoplada da fila no cliente (`ClientUpdates`) e no entregador (`DeliveryUpdates`).
  - Aplicação segura exclusivamente entre ciclos de sincronização.
- **Desacoplamento de Configuração de Implantação (`configuration.py`):**
  - Suporte a perfil de configuração `deployment.local.json` e template `deploy/deployment.example.json`.
  - URLs de servidor, diretórios de entrega e hosts administrativos parametrizáveis por variáveis de ambiente ou arquivo JSON local.
- **Rotina de Backup Automático Off-VM:**
  - Exportação agendada diária via timer systemd (`tracer-backup-export.timer`) da VM 106 para VM 105 via streaming SSH.
  - Validação de integridade SQLite (`PRAGMA quick_check`) imediata após o recebimento.
- **Scripts de Assinatura e Auditoria:**
  - `scripts/sign_update_release.py`: ferramenta CLI para gerar manifestos assinados com chave privada protegida sob DPAPI.
  - `scripts/audit_publication.py`: auditor automatizado de dados de rede, IPs e domínios internos.

### Modificado
- Atualização de empacotamento Inno Setup (`packaging/client.iss`) para embutir o `TracerAtualizador.exe` e integrar atualização silenciosa.
- Parametrização dos scripts administrativos (`Open-AdminPanel.ps1`, `Definir-Administrador.ps1`) para leitura dinâmica de host.
- Reforço do `.gitignore` para bloquear artefatos sensíveis, credenciais e configurações locais da empresa.

### Segurança
- Chave privada de assinatura Ed25519 mantida fora do repositório sob DPAPI do usuário.
- Binários continuam com status Authenticode não assinado (piloto interno), com integridade garantida pelo protocolo Ed25519 + SHA256.

---

## [0.1.1] - 2026-11-09

### Adicionado
- Redesenho completo da interface do cliente (`client/config_ui.py`) em tema grafite/verde escuro com tela dedicada de ativação inicial e status em tempo real.
- Redesenho responsivo do painel web administrativo (`server/dashboard.py`) com controle de acesso baseado em papéis (RBAC).
- Pipeline de corte para arquitetura queued na VM Linux Debian (VM 106).

### Corrigido
- Contato de lease e renovação automática de sessão entre cliente e servidor.

---

## [0.1.0] - 2026-11-06

### Adicionado
- Primeira versão piloto do cliente Windows com monitoramento de pastas `.oma` e `.vca`.
- Serviço Windows de entrega (`TracerEntregador.exe`) com publicação atômica e prevenção de sobreposição de pedidos.
- API de ingestão segura com tokens protegidos via DPAPI.
