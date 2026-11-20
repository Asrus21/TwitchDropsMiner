# Changelog

Todas as mudanças do app, por versão. A seção de cada versão vira o texto do Release no
GitHub e aparece na janela de atualização dentro do app.

**Para lançar uma versão:** troque o número em `version.py` (formato `X.Y.Z`) e acrescente
aqui uma seção `## [X.Y.Z] - AAAA-MM-DD` no topo. Quando isso chega ao `master`, o GitHub
Actions gera o executável e publica o Release `vX.Y.Z` sozinho. Sem a seção no changelog, o
build falha de propósito.

## [16.2.0] - 2026-11-20

Esta versão tenta pegar a lista completa de campanhas, a mesma que aparece em
twitch.tv/drops/campaigns, e organiza melhor o que dá e o que não dá para minerar.

### Novidades
- **Lista oficial de campanhas primeiro:** o app pede à Twitch a mesma lista que a página de
  campanhas mostra. Quando a Twitch responde, essa lista vale sobre as outras fontes. Quando
  não responde, o app continua com a varredura dos canais ao vivo, como antes.
- **Lista de reserva:** se a lista oficial e a varredura não acharem nada, o app usa uma
  lista salva com os 79 jogos que tinham campanha em 20/11/2026. Jogos com campanha já
  encerrada ficam de fora.
- **Contas para vincular:** os jogos com campanha que precisam da sua conta do jogo vinculada
  aparecem na janela principal com o link, e também na aba Inventory (marcados como
  "Not linked").
- **Aguardando stream:** jogos com campanha e sem nenhum canal ao vivo no momento aparecem
  como "aguardando stream ao vivo" e são procurados de novo na próxima atualização.
- **Botão Reload:** agora busca as campanhas de novo na hora, sem esperar os 30 minutos.
- **Ordem padrão:** em instalações novas, o app começa minerando as campanhas que terminam
  antes. Quem já usa o app mantém a configuração atual.
- **Modo de diagnóstico (`--discover-debug`):** testa cada forma de pedir as campanhas à
  Twitch e grava o resultado no `log.txt`, sem gravar senha, token nem cookie.
- **Nova opção `campaign_source`** no `settings.json`: `auto` (padrão), `official`, `scan`
  ou `snapshot`.

### Correções
- **Jogos com acento** (como Pokémon GO) não eram encontrados na Twitch. Agora são.
- Campanhas encerradas e campanhas de **Recompensas** (que não são Drops de jogo) são
  ignoradas sem erro, e contadas no log.

## [16.1.0] - 2026-10-27

Primeira versão do fork Asrus21, com o app funcionando de novo depois das mudanças que a
Twitch fez entre setembro e outubro de 2026.

### Novidades
- **Atualização dentro do app:** ao abrir (e a cada 6 horas), o app confere se saiu uma
  versão nova. Se saiu, mostra o que mudou e pergunta se quer baixar; depois de baixar,
  pergunta se quer instalar. Ao instalar, o app fecha, troca o executável e abre de novo.
  O arquivo baixado é conferido com o checksum (SHA-256) do Release antes de ser usado.
  Dá para desligar com `"check_updates": false` no `settings.json`.
- **Releases no GitHub:** cada versão nova vira um Release com o `TwitchDropsMiner.exe`
  (portátil) e um `.zip` com o executável e o manual.
- **Campanhas de todos os jogos:** como a Twitch parou de listar as campanhas, o app faz uma
  varredura de todos os streams ao vivo com drops ativados (em duas passadas) e pergunta aos
  canais mais assistidos de cada jogo quais drops a sua conta pode ganhar. Os jogos
  encontrados ficam salvos em `drops_games.json` e são procurados de novo nas próximas
  varreduras. O resultado é reaproveitado por 30 minutos.
- **Resumo da varredura:** a aba Main mostra uma linha `Drops scan:` com quantas campanhas e
  jogos foram encontrados.

### Correções
- **Login:** a Twitch passou a recusar o client ID do app Android (`KeyError: 'device_code'`).
  O app agora tenta os clientes conhecidos (SMARTBOX primeiro) e guarda o que funcionou em
  `settings.json` (`client_type`). Logins antigos que ainda valem continuam funcionando.
- **Crash logo depois do login** (`NameError: JsonType`).
- **Campanhas descartadas por inteiro** quando um drop vinha sem nome ou data: agora os dados
  que faltam são completados com os da campanha.
- **Um erro numa consulta perdia todas as respostas** da varredura: agora só o lote com erro
  é pulado.
- **Canais sem drops para a conta:** antes de assistir, o app confere se o canal tem drops
  disponíveis para você (`available_drops_check`, ligado por padrão).
