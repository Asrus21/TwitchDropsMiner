# Changelog

Todas as mudanças do app, por versão. A seção de cada versão vira o texto do Release no
GitHub e aparece na janela de atualização dentro do app.

**Para lançar uma versão:** troque o número em `version.py` (formato `X.Y.Z`) e acrescente
aqui uma seção `## [X.Y.Z] - AAAA-MM-DD` no topo. Quando isso chega ao `master`, o GitHub
Actions gera o executável e publica o Release `vX.Y.Z` sozinho. Sem a seção no changelog, o
build falha de propósito.

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
