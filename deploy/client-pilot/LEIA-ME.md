# TracerTransfer Cliente 0.1.2 — piloto acompanhado

**Pacote ainda não assinado. Uso somente no piloto autorizado pelo responsável; não redistribuir em massa e não desativar SmartScreen/antivírus.** O hash verifica integridade, não substitui a identidade de um publicador assinado.

## Instalar e ativar
1. Execute o instalador deste ZIP na conta Windows que usará o programa. Não precisa de Python, VPN, configuração de IP ou certificado.
2. Instalação é somente para seu usuário. Atalho na área de trabalho e início no login são opcionais e vêm desmarcados.
3. Abra **TracerTransfer Cliente** no menu Iniciar.
4. Escolha a pasta local que recebe os arquivos do tracer, informe o nome do computador e use o código de ativação individual fornecido pelo responsável. Código é secreto, tem validade e uso único; não encaminhe nem publique.
5. Clique **Ativar computador**. O endereço já está configurado; não há senha de administrador central para operar o cliente.

**Somente arquivos novos .OMA/.VCA serão enviados depois da ativação.** Os arquivos que já estavam na pasta são preservados e ignorados como ponto inicial. Não use pasta com subpastas, links/junctions ou diretório compartilhado não homologado. Limite por arquivo: 20 MiB.

## Durante o uso
- Escolher/salvar pasta, pausar/retomar e sair são controles locais. Pausa/retomada pode levar um ciclo de observação para refletir no status.
- Fechar a janela mantém o cliente na bandeja; **Sair** encerra o programa.
- Recebido no servidor não é entregue: acompanhe **Aguardando entrega** até **Entregues**. Se o Windows receptor estiver indisponível, a fila permanece no servidor.
- Mesmo nome com conteúdo diferente gera conflito e exige análise do responsável. Não renomeie/reenvie repetidamente como tentativa de correção. O destino não é sobrescrito.
- Falta de rede/espaço ou revogação deve ser comunicada ao responsável. Não apague configuração, banco ou fila.

## Atualizar ou remover
Esta versão inclui um atualizador auxiliar. Depois da instalação inicial 0.1.2, a consulta automática usa somente releases publicadas, não marcadas como pré-release, do repositório oficial, sem token no programa. Enquanto o repositório continuar privado, a consulta ficará indisponível e as transferências continuarão normalmente. O pacote só é aceito com manifesto Ed25519 válido e hash/tamanho conferidos; não basta colocar um EXE no GitHub. A assinatura do manifesto NÃO é Authenticode e não elimina SmartScreen. Executar a partir das fontes, perfil de ensaio, modo portátil ou `--once` não deve disparar instalação automática.

Encerre pela opção **Sair** antes de atualizar/desinstalar. O instalador usa o mesmo produto por usuário; configuração/ativação/fila ficam fora da pasta de executáveis. Desinstalar não revoga o dispositivo no servidor: solicite revogação ao aposentar o computador. DPAPI vincula a identidade à mesma conta Windows; copiar config.json para outra conta/PC não é migração válida.

Se aparecer **cadastro antigo** ou já existir uma instalação/configuração, preserve tudo e solicite migração ao responsável. O cadastro JSON técnico anterior não equivale a código de ativação e não serve para o backend atual.

## Limites do piloto
Ciclo local e atualização real 0.1.0 → 0.1.1 foram exercitados no computador existente de Hiro, com identidade/fila isoladas preservadas e entrega confirmada após atualizar. Ativação pelo botão da GUI foi exercitada com backend/TLS reais em ensaio automatizado. Isso não equivale a Windows limpo, interação manual de ativação no EXE, reboot/login real, assinatura ou processamento industrial. Arquivos/bancos/credenciais individuais, painel administrativo e entregador não fazem parte deste ZIP.
