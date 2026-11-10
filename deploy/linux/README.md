# Linux — implantação e operação

Instalação sem GUI; receptor em loopback `127.0.0.1:8765`, modo `queued`.

- Fixe a identidade SSH do guest pelo console/guest agent autenticado antes de acessar.
- Defina rede persistente fora da faixa DHCP, com sondagem de conflito e rollback temporizado. O script `set-static-network.py` deve rodar somente dentro da VM indicada; depois da reconexão, confirme IP/rota/DNS e cancele o timer de rollback.
- Exporte dependências do `uv.lock` com hashes, excluindo bibliotecas exclusivas da GUI Windows:
  `uv export --no-dev --no-emit-project --prune pystray --prune pillow --format requirements-txt --output-file deploy/linux/requirements.txt`.
- Copie apenas fontes de backend e arquivos de implantação por SSH para uma pasta de staging privada.
- Exija um banco consistente previamente inicializado ou arquivo bootstrap JSON privado. Nunca passe senha/código/token em argumentos. `sudo sh deploy/linux/install.sh [caminho-do-bootstrap]` instala somente uma implantação nova; não sobrescreve `/opt/tracer-transfer` existente.
- Aplicação executa como usuário `tracer`, sem login e sem privilégios, com código/venv somente leitura para o serviço. Dados em `/var/lib/tracer-transfer`; backups locais em `/var/backups/tracer-transfer` não protegem contra perda do disco/VM.
- Bootstrap é consumido pela aplicação; remova também qualquer cópia original de transporte após validar a inicialização, sem prometer apagamento seguro.
- Confira `systemctl status tracer-transfer`, `journalctl -u tracer-transfer`, e corpo JSON de `http://127.0.0.1:8765/health` pela própria VM.
- Não exponha o backend/administração diretamente à LAN/internet. Inicialmente, abra o painel por SSH local forwarding para `127.0.0.1:8765`; nunca use `0.0.0.0` no túnel da estação.
- A instalação isolada não efetua o corte do endpoint público nem instala entregador no Windows. Cloudflared e receptor antigo permanecem preservados até homologação e migração coordenada.

Estado concreto da VM, rede, testes e limites: `docs/LINUX_DEPLOYMENT.md`.
