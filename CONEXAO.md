# Conexão SYM — versão de desenvolvimento 0.2

Esta entrega contém app Android, painel, API e conector MCP com OAuth. Tudo foi compilado/testado localmente; NÃO foi publicado, pareado com seu celular nem conectado a uma conta real de ChatGPT. O painel é funcional quando o servidor está rodando, não uma imagem.

## O que está pronto
- App: conexão HTTPS opcional, chave de aparelho criptografada no Android Keystore, consulta manual e tarefa periódica de melhor esforço (mínimo 15 minutos). Consulta também antes de preparar e de enviar uma resposta.
- Servidor: configuração versionada em SQLite; estado desejado separado da última versão aplicada pelo aparelho; auditoria limitada a 200 ações sem conteúdo de mensagens.
- Painel responsivo: estado recebido, link, informações da loja, pausa e histórico. A chave do proprietário fica só na memória da aba, sem localStorage.
- MCP: consultar_bot, consultar_alteracoes e configurar_bot. OAuth authorization-code + PKCE S256 pelo SDK oficial MCP; autorização manual do proprietário e callbacks previamente autorizados. Tokens e códigos expiram; refresh rotativo; reiniciar o processo invalida sessões e exige reconectar.
- A conexão não usa API de modelos da OpenAI. A integração de gerenciamento não transforma o ChatGPT em atendente permanente do WhatsApp. A IA opcional do app continua separada.

## Limites e funcionamento
O painel só gerencia link, conhecimento usado pela IA e pausa remota. Não altera contatos autorizados, chave de IA ou botão local de ativação. As respostas locais usam os textos do código, não interpretam livremente o campo de conhecimento. Não lê mensagens nem controla a tela do telefone.

A pausa remota pode não cancelar um envio que já começou. Não é push: vale quando a próxima consulta ao servidor termina. Em conexão ruim, a tentativa pode demorar até os timeouts. O aparelho só envia versão, permissões, estado dos botões, revisão aplicada e contagem diária. Não envia nomes de clientes, mensagens ou chaves de IA.

Com a conexão ativada, falha na sincronização bloqueia os envios. Desligar a conexão no celular permite voltar ao modo local. A configuração recebida continua salva localmente, mas a pausa remota deixa de ser aplicada. A verificação online exige internet mesmo no modo de respostas prontas.

## Rodar localmente (desenvolvedor)
Requer Python 3.12. Dentro desta pasta:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Defina segredos DIFERENTES e aleatórios, com pelo menos 32 caracteres, nas variáveis SYM_OWNER_TOKEN e SYM_DEVICE_TOKEN. Gere-os com secrets.token_urlsafe(40); guarde-os em gerenciador de senhas. Nunca coloque valores reais no código, em Git, screenshots ou mensagens do chat.

Defina SYM_PUBLIC_URL=http://127.0.0.1:8765 e rode `python server.py`. O painel ficará em /panel. O Android exige HTTPS e não conecta nesse endereço local. Para funcionar no celular e no ChatGPT, precisa de hospedagem/túnel HTTPS real. Este ambiente de desenvolvimento não é hospedagem permanente.

## Publicação
Inclui Dockerfile. Configure:

| Variável | Valor |
|---|---|
| SYM_PUBLIC_URL | Origem HTTPS pública exata, sem barra final ou caminho |
| SYM_OWNER_TOKEN | Segredo exclusivo do dono, pelo menos 32 caracteres |
| SYM_DEVICE_TOKEN | Outro segredo, exclusivo do app |
| SYM_DB | Caminho SQLite em volume persistente |
| SYM_OAUTH_REDIRECTS | Lista de callbacks exatos separados por vírgula; copie da configuração do conector ChatGPT |
| SYM_BIND | 0.0.0.0 |
| PORT | Porta interna indicada pela hospedagem |

O proxy deve preservar Host e transmitir Authorization. Execute UM processo/worker; as sessões OAuth estão em memória. Use disco persistente, backup privado e HTTPS. Não publique os segredos nem a base de dados. A versão é pessoal e experimental, sem auditoria de segurança externa.

Um plano gratuito de hospedagem deve ser verificado antes de ativar. Por exemplo, o Render gratuito suspende serviços após inatividade e perde arquivos locais, inclusive SQLite, em reinícios/suspensões. Com esta versão, esse plano só serve para teste descartável, não uso contínuo. O servidor recomeça pausado quando perde a base. Não há contratação nem pagamento nesta entrega. Não use a conexão no atendimento real até escolher armazenamento durável e testar estabilidade.
Fonte: https://render.com/docs/free (consultado em 15/11/2026).

## Parear o celular
1. Instale a versão 0.2 por cima da 0.1. O certificado de teste é o mesmo; não desinstale primeiro.
2. Mantenha IA desativada se não tiver cota de provedor.
3. Na seção Conexão com o painel, informe a origem HTTPS e SOMENTE SYM_DEVICE_TOKEN.
4. Ative gerenciamento e toque em Salvar conexão e sincronizar. A primeira configuração do servidor começa pausada.
5. No painel /panel, use SOMENTE SYM_OWNER_TOKEN. Retome pelo painel, mantendo autorizados apenas contatos de teste no celular. Para enviar, o botão local também precisa estar ativo.
6. Faça novas consultas de estado para conferir a revisão aplicada. O servidor recebe a revisão anterior em cada sincronização, portanto a confirmação aparece no contato seguinte.

## Conectar o ChatGPT
A disponibilidade de conectores personalizados depende da conta/interface. Não foi verificada na conta do usuário. Não há instalação automática desta conexão nesta conversa.

1. Após publicar e testar HTTPS, adicione um conector MCP personalizado em uma interface ChatGPT que ofereça esse recurso. Endpoint: sua origem HTTPS + /mcp. Escolha OAuth e DCR; CIMD não está implementado.
2. Copie o callback exato mostrado pelo ChatGPT e adicione a SYM_OAUTH_REDIRECTS antes de autorizar. Sem callback autorizado, o servidor rejeita o registro.
3. Na página de consentimento SYM, confira nome do cliente e URL de retorno. Digite seu segredo do proprietário SOMENTE na página do seu servidor HTTPS.
4. Depois de conectado, teste primeiro consultar_bot. Só depois teste uma mudança de pausa com a revisão atual. Alterações são pendentes até o aparelho sincronizar.
5. Revogar o token pelo OAuth invalida as sessões desse cliente. Reiniciar o servidor invalida todas as sessões; registros de clientes ficam no SQLite. Para revogar o acesso de dono/aparelho, troque os segredos do ambiente e atualize o app quando aplicável.

Referências oficiais:
https://developers.openai.com/plugins/build/auth
https://developers.openai.com/plugins/build/app-quickstart

## Testes realizados
- 8 verificações das regras Java.
- 6 cenários de integração HTTP: separação de dono/aparelho, sincronização sem mensagens, validação e conflito de revisão, proteção web/tamanho de corpo, desafio OAuth, fluxo DCR+consentimento+PKCE+MCP+refresh/replay.
- Compilação Android 35 e verificação criptográfica do APK.
- Sintaxe Python/JavaScript validada.
- A tentativa de teste visual com Chromium foi bloqueada pelo ambiente; interface não foi verificada visualmente em navegador.
- Não testado: aparelho físico, API de IA, serviço publicado, conexão com conta real ChatGPT ou comportamento contínuo em segundo plano no Samsung.
