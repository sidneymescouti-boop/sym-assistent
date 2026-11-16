# IA por instalação — piloto

O aplicativo cadastra cada instalação com uma credencial própria. Cada pedido usa somente o perfil daquela loja. A chave Gemini fica no serviço e nunca no APK.

## Configuração do responsável

- Configure `SYM_AI_KEY` como segredo do serviço e `SYM_AI_MODEL` com um modelo disponível na sua conta.
- Para liberar novos aparelhos no piloto, configure `SYM_AI_ENROLLMENT_OPEN=true`. O padrão é fechado.
- Limites padrão: `SYM_AI_DAILY_LIMIT=100`, `SYM_AI_GLOBAL_DAILY_LIMIT=500`, `SYM_AI_MAX_INSTALLATIONS=100`. Falhas também consomem reserva; limites são de pedidos e não de valor em reais.
- Preserve `SYM_OWNER_TOKEN`, `SYM_DEVICE_TOKEN` e `SYM_PUBLIC_URL` do serviço existente.
- Use SQLite persistente em `SYM_DB` e uma única instância. Não use disco temporário para atendimento real.
- No projeto Android, configure a origem HTTPS do serviço antes de compilar. O cliente não precisa configurar hospedagem ou chave.

Não há hospedagem obrigatória específica. O APK 0.6.1 anterior ainda aponta para o endereço Render usado nos testes; mudar de hospedagem exige recompilá-lo.

## Validação

Execute `python3 -m unittest discover -p 'test_*.py'`. Os testes usam provedor simulado. Antes de liberar clientes, teste geração real e dois aparelhos com lojas diferentes.

## Limites do piloto

Autenticação por instalação, sem conta recuperável ou validação de assinatura. Reinstalações podem criar outra identidade. Cadastro aberto deve ficar limitado ao piloto. Mensagens e perfis são transmitidos ao provedor para gerar respostas; o gateway não os grava no banco. Complete a política de privacidade antes de distribuir.

As rotas são `/ai/enroll`, `/ai/reply` e `/admin/ai/revoke`. Sem chave e modelo configurados, a IA permanece indisponível. A assinatura mensal ainda precisa ser implementada.
