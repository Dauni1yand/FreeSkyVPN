# FreeSkyVPN

Freemium VPN-сервис: головной сервер управляет заграничными нодами
(VLESS + Reality + XTLS Vision), MVP-клиент — Telegram-бот, дальше —
Android-приложение без выбора страны.

- [`head/`](head/) — головной сервер: FastAPI, PostgreSQL, устойчивое
  управление нодами (`app/node_manager`)
- [`provisioning/`](provisioning/) — разовый bootstrap новой ноды по SSH

Полный чертёж (архитектура, схема данных, алгоритмы, roadmap) — отдельный
документ, см. историю разработки; актуальные технические решения по ходу
кодирования фиксируются в `head/README.md`.