# Bling → Gertec TC506

Integração para Windows que consulta o catálogo principal do Bling e publica nome, preço e GTIN/EAN de produtos ativos em um TXT lido pelo TC Server. O preço é o do cadastro principal do ERP; preços de plataformas de venda online não são consultados. A integração não altera produtos no Bling.

## Escopo e requisitos

- Somente Windows: o armazenamento de credenciais usa DPAPI, a trava usa `msvcrt`, e a instalação automática usa PowerShell e Agendador de Tarefas.
- Testado em Python 3.12; outras versões não foram testadas.
- Observação técnica não verificada em versões antigas: o SQL usa `ON CONFLICT ... DO UPDATE`, recurso do SQLite 3.24.0 ou posterior.
- Bibliotecas padrão do Python, incluindo Tkinter para o painel; não há pacotes externos a instalar.
- Conta Bling, aplicativo OAuth com permissão de leitura **Produtos** e retorno cadastrado `http://localhost:8765/callback`.
- TC Server disponível separadamente e com acesso ao TXT.

## Estrutura e raiz do repositório

A raiz do repositório é a pasta `Automacao/`. A pasta que a contém (TXT de produção, TC Server e manuais) não faz parte dele.

```text
Automacao/
  README.md
  .gitignore
  config.example.ini            exemplo fictício; não é carregado automaticamente
  Programa/
    Bling automatico.pyw        abre o painel
    bling.py                    interface, cliente HTTP e OAuth
    sincronizacao.py            SQLite e sincronização incremental
    sincronizar_background.py   execução e instalação da tarefa
    conversor.py                funções CSV, normalização e escrita
    nomes_produtos.py           nomes de pais e filhos
    conflitos_ean.py            verificação de códigos repetidos
    configuracao.py             caminhos avaliados sob demanda
    adotar_base.py              adoção assistida de CSV/TXT aprovado
    test_sincronizacao.py
    test_normalizacao_ean.py
    test_nomes_produtos.py
    test_conflitos_ean.py
    test_configuracao.py
  Relatorios/                  arquivos operacionais; ignorados
  Backups/backups_bling/       backups rotativos; ignorados
```

Na sincronização, os relatórios são criados em `<pasta do TXT>/Automacao/Relatorios/` e os backups rotativos em `<pasta do TXT>/Automacao/Backups/backups_bling/`: `<pasta do TXT>` é a pasta que contém o arquivo de destino efetivamente resolvido pela configuração, independentemente de onde o código está instalado. Na atualização manual pelo conversor, os relatórios seguem a mesma regra para o TXT escolhido, mas o backup fica diretamente em `<pasta do TXT>/Automacao/Backups/`; ao gerar um novo TXT sem atualização, o log fica junto desse TXT.

As pastas operacionais aparecem agrupadas no mapa porque seus arquivos variam durante o uso; elas são ignoradas pelo Git e não integram o repositório publicado. Caches Python podem ser regenerados e são ignorados.

## Instalação: configure os caminhos antes do primeiro uso

Prepare um ambiente Windows com Python e Tkinter. Antes de abrir o painel ou executar a tarefa pela primeira vez, crie a pasta `%LOCALAPPDATA%\GertecBling`, copie o modelo de configuração conforme descrito abaixo e preencha os destinos. Como alternativa ao INI, configure as variáveis de ambiente para o processo que executará o programa.

Os caminhos são resolvidos somente quando necessários. Importar `configuracao.py`, `sincronizacao.py` ou `conversor.py` não lê configuração nem acessa bancos. O painel tenta mostrar o destino ao abrir; se faltar configuração, apresenta a mensagem e continua disponível. A tentativa de sincronizar/publicar exige um destino válido.

Precedência, independente para cada caminho:

1. Parâmetro explícito de função (por exemplo, `sincronizar(..., banco=destino)` ou `obter_banco_fixo(destino)`). Dispensa ambiente e INI.
2. Variável de ambiente não vazia: `GERTEC_BANCO_REDE` ou `GERTEC_BANCO_FIXO`.
3. Chave `banco_rede` ou `banco_fixo` da seção `[caminhos]` em `%LOCALAPPDATA%\GertecBling\config.ini`.

O programa não cria nem altera esse INI. `config.example.ini` é apenas um modelo versionável, sem dados reais. Para configurar a instalação, copie-o manualmente para o local indicado e substitua os marcadores. Exemplo fictício:

```ini
[caminhos]
banco_rede = \\<servidor>\<compartilhamento>\Gertec TC506\Banco de dados.txt
banco_fixo = \\<servidor>\<compartilhamento>\Gertec TC506\Banco de dados.txt
```

`banco_rede` é o destino da sincronização automática. `banco_fixo` é o destino da atualização manual pelo conversor. Eles podem apontar para o mesmo TXT, conforme a instalação. Use caminhos completos. O INI não expande variáveis como `%NOME%`: os valores são lidos literalmente, sem interpolação. Não coloque credenciais OAuth nesse arquivo. Variável de ambiente vazia equivale a ausente; parâmetro explícito vazio é erro, sem fallback silencioso.

Não há hostname, unidade ou destino de produção como padrão. Se faltar configuração, a mensagem informa a chave, o local esperado do INI e a variável alternativa. INI inválido ou sem permissão gera mensagem controlada sem reproduzir seu conteúdo. A resolução ocorre antes de abrir a base SQLite ou processar uma publicação pendente. Na rotina agendada habilitada, a ausência do destino é registrada no log existente e encerra com falha, sem avançar o período ou alterar o TXT. Não há consulta à API para descobrir o destino.

Caminhos UNC evitam depender de uma unidade mapeada em outra sessão do Windows. As variáveis devem estar disponíveis para o processo que executa a tarefa; defini-las apenas em um terminal não garante que o Agendador as herde. O usuário precisa de acesso à pasta compartilhada. O exemplo não é uma configuração pronta para uso.

O TXT fica no computador do TC Server. Se o computador que consulta o Bling perder internet ou acesso à rede, o último TXT permanece disponível no servidor. A continuidade da consulta também depende de o servidor e o terminal conseguirem se comunicar. O TC Server precisa reler o arquivo; o intervalo de releitura é configurado nele, separadamente do Python.

## Conexão e uso após configurar os caminhos

Abra `Programa/Bling automatico.pyw`. Preencha Client ID e Client Secret e clique em **Conectar ao Bling**. Autorize no navegador; a conexão e os tokens são salvos automaticamente para o usuário do Windows. O callback escuta somente em `127.0.0.1:8765`, valida `state` aleatório e aguarda até cinco minutos. Se a porta estiver ocupada, libere-a antes de tentar novamente. O código atual depende do retorno cadastrado no aplicativo do Bling; confira se o retorno cadastrado coincide exatamente com o endereço esperado pelo programa.

Clique em **Atualizar agora** para sincronizar. A importação CSV inicial é opcional e acelera a preparação do catálogo. Após a primeira carga, não é necessário exportar CSV a cada atualização. A primeira consulta completa pode ser lenta porque os detalhes ainda desconhecidos são buscados individualmente; o progresso é salvo a cada vinte cadastros.

Defina o intervalo e use **Instalar/atualizar automação** quando desejar instalar ou atualizar a tarefa. O nome é `Gertec Bling - Sincronizacao incremental`: execução ao entrar no Windows e no intervalo escolhido, normalmente quinze minutos. Depois de instalada, o painel pode ser fechado. O computador deve estar ligado, acordado e com o usuário conectado. Para pausar, desmarque as consultas automáticas e salve as preferências. Alterar o intervalo exige atualizar a tarefa.

A instalação copia os módulos necessários para `%LOCALAPPDATA%\GertecBling\programa`. Editar o código compartilhado não atualiza essa cópia automaticamente. O instalador não cria nem copia o INI. Após atualizar os módulos, feche e reabra painéis antigos e confira a próxima rodada da tarefa.

## Dados locais e credenciais

Em `%LOCALAPPDATA%\GertecBling` ficam:

- `conexao_v2.dat`: Client ID, Client Secret, tokens, validade e preferências, protegidos por DPAPI do usuário do Windows.
- `conexao.dat`: fallback de migração. `carregar_config()` só o lê se o arquivo novo não existir; chama a mesma função de descriptografia DPAPI. Não é lido como JSON em texto claro. No código atual, `salvar_config()` grava somente `conexao_v2.dat`; não foi encontrado caminho que grave o arquivo antigo. Não exclua esse fallback apenas para limpar a pasta; ele pode ser necessário à migração da conexão.
- `produtos.sqlite3`: catálogo de trabalho, vínculos, ordem do CSV, checkpoints, conflitos e estado da publicação.
- `execucao.lock`: trava de exclusão mútua.
- `sincronizacao.log`, `produtos - erros.log`, `duplicidades_confirmadas.txt` e `conflitos_ean - resolucoes.log`: andamento e diagnósticos.
- `programa/`: cópia local usada pela tarefa automática.

Esses arquivos ficam fora da raiz do projeto. Não os copie para o GitHub, mesmo os protegidos por DPAPI. Essa proteção está vinculada ao usuário do Windows e não garante migração para outro computador. Pare a instância anterior antes de migrar e, se necessário, faça nova autorização no destino. Um processo com acesso ao mesmo usuário pode ter acesso às credenciais; criptografia local não elimina todos os riscos.

## Modelo do catálogo e validações

A identidade estável é o **ID do Bling**. SKU aparece nos diagnósticos; EAN é a chave publicada e pode mudar. SQLite armazena também produtos que não podem ir ao TXT, como pais sem GTIN. Quantidade de cadastros analisados não equivale ao número de códigos publicáveis.

O TXT tem formato `EAN|Descrição|Preço`, UTF-8 sem BOM, sem cabeçalho, com quebras CRLF e vírgula decimal. Exemplo inteiramente fictício:

```text
9999999999918|Produto fictício Quantidade:500ml|10,00
```

Nome, preço e EAN são obrigatórios para publicar um produto novo. EAN deve conter somente dígitos ASCII; não há exigência adicional de tamanho ou dígito verificador nesta versão. Preço zero é permitido; preço negativo, não finito ou com fração de centavo é inválido. SKU não substitui GTIN. O CSV usa ID, Código/SKU, Descrição, Preço, Situação, GTIN/EAN e Código Pai; no layout esperado pelo conversor, Descrição está na coluna C, Preço na G e GTIN/EAN na T.

EAN é texto, nunca número. Um código de exatamente doze dígitos recebe um zero inicial para corresponder à leitura configurada do terminal. As formas com doze dígitos e com o zero são consideradas equivalentes. Demais tamanhos são preservados e a normalização não acrescenta zeros repetidamente.

Um produto novo incompleto é ignorado por inteiro, com motivo por ID/SKU. Para um ativo já conhecido, nome/preço inválidos podem preservar a última versão válida. Se o Bling informar explicitamente remoção ou troca de EAN, o código antigo é liberado; ele não deve continuar reservado por uma versão desatualizada.

## Nomes de pais e filhos

O nome de exibição combina nome do pai e nome do filho com espaço. Os nomes originais ficam separados na base. A resolução usa ID do pai; vínculos de CSV por Código Pai/SKU são fixados por ID para resistir a mudanças posteriores de SKU. A API pode informar o vínculo no detalhe ou na listagem. Pais ausentes são consultados quando necessários e uma consulta é reutilizada entre irmãos.

Pai sem EAN pode fornecer nome sem ser publicado como item vendável. Uma alteração no nome do pai repercute na próxima geração dos nomes dos filhos. Não se herdam EAN, preço ou estoque. HTML, entidades e espaços repetidos são limpos; prefixo de pai já presente não é duplicado. O TXT não trunca nomes, embora a apresentação dependa do TC Server e do terminal. Vínculos ausentes, ambíguos ou cíclicos geram diagnóstico; não se adivinha o pai pelo texto. Falha ao consultar um pai necessário impede confirmar o período.

## Por que polling e como funciona o incremental

Polling local evita depender de hospedagem e de um endpoint público para webhooks. A atualização é periódica, não em tempo real. Webhooks não estão implementados.

Cada rodada usa a última janela concluída, com sobreposição de quinze minutos e término dois minutos antes do início da execução, para tolerar atraso de indexação. O horário do código é UTC-3. Consulta inclusões e alterações de ativos, além de inativações/exclusões de IDs conhecidos. Repetições são esperadas e tratadas por atualização do mesmo ID. Janelas longas são divididas em intervalos de trinta dias.

A listagem usa páginas de cem registros. Os detalhes necessários ao GTIN são obtidos quando preciso. Uma conferência diária lista ativos para reconciliar ausências e comparar assinaturas; não equivale a consultar novamente os detalhes de todos os produtos em cada rodada. Na carga inicial, checkpoints permitem retomar dados já obtidos.

A conexão HTTPS é reutilizada, com validação padrão de certificado e hostname. Há intervalo mínimo de 0,42 segundo entre requisições e tentativas limitadas de GET em falhas transitórias, incluindo HTTP 429/5xx. Limites da conta são compartilhados com outras integrações; atingir o limite diário interrompe a rodada. POST de token não é repetido indiscriminadamente, pois o refresh token pode já ter sido rotacionado. Novos tokens são salvos imediatamente. A trava impede operações concorrentes de sincronização/renovação no mesmo estado local.

## Duplicidades de EAN

Colisões são agrupadas pelo código normalizado. Ao surgir ou mudar um conflito, os participantes são conferidos no Bling; detalhes já obtidos na rodada são reutilizados. Se o antigo titular não tiver mais o EAN ou estiver inativo, sua ocupação é liberada. Se mais de um ativo continuar com o código, a duplicidade é confirmada e relatada. Mantém-se a primeira ocorrência válida na ordem persistida do CSV, com desempate pela ordenação do ID usada na publicação.

A confirmação pode ser reutilizada por até vinte e quatro horas se os dados relevantes não mudarem. Mudança de participante invalida o cache. Falha da API ou ausência do campo GTIN não autoriza resolver por suposição: a publicação e o período anterior são preservados. Há limite para reavaliação de cadeias de conflitos. O relatório operacional local, não destinado à publicação, identifica todos os envolvidos; conflitos resolvidos deixam de figurar como pendentes, mantendo o histórico local.

## Publicação, falhas e backups

SQLite e o arquivo remoto não participam de uma transação única. Por isso, a base guarda uma intenção de publicação pendente com conteúdo, hashes anterior/novo, período e resumo. O arquivo é escrito por inteiro em temporário na mesma pasta e substituído ao final; somente após sucesso o período é confirmado. Na retomada, os hashes permitem reconhecer o arquivo antigo ou já publicado. Alteração externa incompatível bloqueia a substituição.

Arquivo vazio é bloqueado. Redução superior a vinte por cento exige revisão e confirmação pelo painel. Não apagar SQLite ou metadados para contornar esses bloqueios. São mantidos até cinco backups rotativos no diretório descrito na seção “Estrutura e raiz do repositório”. Gravação pela rede pode falhar por indisponibilidade, permissão ou bloqueio; a rotina deve conservar o estado necessário à retomada, sem prometer garantias de todo sistema de arquivos remoto.

`adotar_base.py` é ferramenta de manutenção para alinhar CSV/TXT aprovado por ID e comparar o conteúdo gerado. Não consulta a API nem publica sozinho. Trocar apenas o TXT não atualiza SQLite. Nova adoção exige pausar gravações, preservar os dados atuais e alinhar o estado antes de reativar.

## Diagnóstico e manutenção

1. Verifique no Agendador o estado da tarefa e no painel o último período concluído. O painel acompanha o log aproximadamente a cada quinze segundos quando ocioso e alerta após duas horas sem atualização; não há notificação automática na área de trabalho.
2. Leia `sincronizacao.log`. Zero cadastros consultados pode indicar ausência de mudanças, não falha. No relatório `<nome do TXT sem extensão> - erros.log`, dentro da pasta de relatórios descrita na seção “Estrutura e raiz do repositório”, procure ID/SKU e motivo; muitos pais sem EAN podem ser pendências esperadas.
3. Consulte `duplicidades_confirmadas.txt` na mesma pasta de relatórios para colisões ainda confirmadas e o histórico local de resoluções. A existência do produto no ERP não garante EAN publicável.
4. Procure o EAN no TXT e compare com o código enviado pelo leitor. Confira zero inicial, ativo/inativo, dados obrigatórios, conflitos e vínculos. Depois confira se o TC Server releu o arquivo. O atraso total combina os intervalos do Python e do Java, além de falhas eventuais.
5. Se houver erro de autorização, confira permissões, retorno OAuth e porta local. Não exponha Client Secret, tokens ou URL com código de autorização em pedidos de suporte.
6. Antes de atualizar código ou recuperar dados, pause a tarefa e aguarde a rodada corrente terminar. Não interrompa renovação de token sem necessidade. Guarde SQLite e TXT; restaurar apenas um deles pode ser sobrescrito pela próxima rodada ou bloquear uma publicação pendente.
7. Rode testes em bases temporárias. Ao implantar uma atualização, atualize a cópia local pelo painel, reabra painéis antigos e confira a rodada real.

## Testes e limites de validação

Execute na pasta `Programa`, em Windows:

```powershell
python -B -m unittest test_sincronizacao test_normalizacao_ean test_nomes_produtos test_conflitos_ean test_configuracao -v
```

São **48 testes**: treze em `test_sincronizacao.py`, quatro em `test_normalizacao_ean.py`, oito em `test_nomes_produtos.py`, dez em `test_conflitos_ean.py` e treze em `test_configuracao.py`. Usam bases temporárias e clientes simulados. Cobrem incremental, inclusão/inativação, nome/preço/EAN, dados inválidos, retomada, publicação pendente, redução, normalização, vínculos e colisões. As fixtures de produtos são fictícias e não vêm da instalação real. Não validam firmware, leitor físico, disponibilidade real do Bling nem garantias do compartilhamento de produção.

## Licença e materiais de terceiros

O código deste repositório está sob a licença indicada em `LICENSE`. O TC Server (Java) e os manuais do fabricante não fazem parte do repositório e têm licenças próprias, que não foram confirmadas aqui. Credenciais, bancos, relatórios, backups e dados reais da instalação nunca devem ser versionados.

Toda mudança futura deve atualizar este README com regra, motivo, validação e limites.
