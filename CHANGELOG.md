# Changelog

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
