# Release, homologação e recuperação

## Estados e responsabilidades

Fonte alterada, testes aprovados, binários compilados, assets publicados e serviço implantado são estados separados. O candidato 0.1.3 está em homologação; relatório privado deve listar exatamente o que foi executado. A publicação do código não autoriza atualização industrial.

A distribuição não contém um quarto executável de atualização. Cliente e Entregador destacam cópias do próprio EXE para aplicação. O backend Linux tem comando nativo e aplicação administrativa isolada. A ausência de permissão é erro, não motivo para elevar o daemon.

## Sequência obrigatória

1. Consolidar fontes e configurações; preservar snapshots/bundles privados fora da árvore publicável. Recriar ambiente e usar somente ferramentas de origem conhecida. Um `.venv` copiado entre diretórios não prova relocação correta dos launchers.
2. Alterar código e testes. Registrar mudanças, riscos, dependências e recuperação no changelog do commit. Não apagar o histórico nem modificar uma tag de versão já distribuída para representar bytes novos.
3. Executar suíte de fontes e ensaios locais apropriados. Reconciliar falhas antes de compilar; não excluir testes de segurança para obter verde.
4. Auditar árvore, índice e histórico inteiro, mais arquivos extraídos dos pacotes. Gitleaks é complementar: um resultado sem achados não comprova ausência de topologia, dados industriais ou metadados privados.
5. Fazer commit das fontes, receitas, testes e changelog; construir os artefatos a partir desse commit limpo. O catálogo de proveniência associa cada hash ao SHA de origem real. Gerados/relatórios não entram acidentalmente no commit seguinte.
6. Compilar Cliente/Entregador e instalador, inspecionar módulos e payloads extraídos. Empacotar por allowlist, nunca por cópia de diretório inteiro. Diretórios de release de versões antigas são evidência histórica, não fonte do pacote novo.
7. Fechar bytes de todos os artefatos e só então assinar manifestos Ed25519 e catálogo de hashes. Testar assinatura, versão, componente, expiração e conteúdo imediatamente antes de publicar. A chave privada permanece fora do repositório/distribuição, protegida por DPAPI/ACL e com custódia separada.
8. Preparar release em rascunho vinculada ao commit/tag corretos. Baixar assets de volta e comparar hashes/assinaturas com o catálogo local. Preferir assets de Release, não ZIPs grandes no Git.
9. Somente após auditoria, mudar a visibilidade do repositório autorizado para público e confirmar acesso anônimo. Não embutir token do publicador para habilitar downloads.
10. Publicar inicialmente como pré-release de homologação, não como atualização estável. A promoção estável depende dos gates e de autorização administrativa/operacional específica. Não remover releases/tags antigas como limpeza cosmética.

## Pacotes genéricos e empresariais

- ZIP Cliente: instalador por usuário, guia e hashes. O instalador contém apenas o cliente e, na variante empresarial, endpoint mínimo permitido; sem binários portáteis duplicados.
- ZIP Entregador: executável, wrapper oficial de versão/hash fixados, scripts/XML/licença em árvore compatível com seus caminhos padrão, guia e hashes. Instalação exige inventário e identidade `delivery`; código de ativação não faz parte do ZIP.
- Artefato Linux: código/dependências fixadas e metadados, sem bancos, runtime, credenciais ou configuração empresarial.
- Proibido incluir `.env`, perfil administrativo `deployment.local.json`, chaves, tokens, logs, bancos, spool, ordens `.oma/.vca` e relatórios privados. Examine também arquivos embutidos em instaladores/EXEs.
- Build empresarial pré-configurado é privado e separado. Build genérico sem endpoint novo não ativa em um domínio fictício. Atualização de instalação já ativada deve preservar sua origem/configuração.

## Gates obrigatórios

- Testar upgrade real entre dois binários de QA, instalação isolada, preservação de identidade DPAPI/fila/destino/autostart e falha do instalador.
- Testar tarefas e serviços sob SYSTEM/LocalService em laboratório, não somente parsear scripts ou substituir funções por mocks.
- Testar update Linux em ambiente isolado, com owners/sandbox reais, aplicação atômica e falha de saúde. Rejeitar traversal, links, duplicatas, expansão excessiva, manifesto adulterado/vencido e pacote de outro componente.
- Não confundir SCM Running com polling saudável; health HTTP isolado não comprova transporte completo nem consumo industrial.
- Windows limpo, login real, outra rede e interpretação SIOU/FreeForm continuam gates independentes. Ausência do ambiente é bloqueio documentado, nunca aprovação.
- Authenticode segue adiado: Ed25519 não substitui identidade do publicador reconhecida pelo Windows nem remove SmartScreen.

## Recuperação

Antes de aplicar, preservar executáveis e estado administrativo de execução; registrar fases com publicação atômica. Guardar o diagnóstico sanitizado da falha antes de iniciar rollback.

Restaurar somente o código/binários anteriores quando compatíveis e verificar saúde. Nunca restaurar SQLite antigo sobre dados novos, reviver sessões/códigos revogados ou republicar pedidos já consumidos. Não ligar serviço que estava deliberadamente parado. No Linux, restaurar a cadeia de dependentes que estava ativa, incluindo proxy/conector, sem iniciar componentes desativados.

Se houve migração de schema incompatível, mudança de identidade, queda no checkpoint ambíguo ou novas gravações, bloquear rollback automático e exigir reconciliação administrativa. Um checkout de tag não é rollback operacional de banco.

Não executar testes sintéticos na pasta industrial, não reativar receptor Windows legado, não mudar DNS nem expor painel. Administração por DNS, redesign e MFA foram adiados. Instruções de recuperação não autorizam desligamento/reboot ou implantação em produção.
