# Respostas da Twitch usadas nos testes

- `game_directory.json`, `slug_redirect.json`: respostas **reais** da Twitch, gravadas sem login
  em 20/11/2026 e anonimizadas (canais trocados por `canal1`, `canal2`...; sem IDs de requisição).
- `viewer_drops_dashboard.json`, `integrity_failed.json`, `available_drops.json`: feitas **à mão**,
  no formato que o código já lê dessas consultas. A lista de campanhas só vem com login, e nenhum
  token pode entrar no repositório. O `integrity_failed.json` reproduz o erro que a Twitch devolve
  hoje para a lista de campanhas. Datas, IDs e links são de exemplo.

Nenhum arquivo aqui contém token, cookie ou cabeçalho de sessão.
