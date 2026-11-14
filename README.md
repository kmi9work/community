# Комьюнити (Community) — бот и CRM на Twenty

Репозиторий проекта сообщества «Эра перемен»: код Twenty CRM (бэкенд/CRM для
участников и уровней сообщества) и инфраструктура деплоя.

Домен: **kmi9site.ru** · Сервер: **45.133.210.22**

## Структура

```
community/
├── twenty/            # код Twenty v2.58.0 (vendored из github.com/twentyhq/twenty)
├── deploy/            # compose-стек, скрипты деплоя, конфиг nginx
├── .github/workflows/ # CI/CD: деплой по коммиту в master
└── Описание.md        # описание проекта сообщества
```

## Twenty: версия и правила

- Upstream: https://github.com/twentyhq/twenty, тег **twenty/v2.58.0**
  (коммит `bd8ab939f0e238b5faeefcbeb897ecd6862b1a59`, 2026-11-12).
- Код в `twenty/` — неизменённое дерево тега без истории upstream (чужую историю
  в этот репозиторий не тащим). Обновление:

  ```bash
  git clone --depth 1 --branch twenty/vX.Y.Z https://github.com/twentyhq/twenty /tmp/twenty-up
  rsync -a --delete --exclude .git /tmp/twenty-up/ twenty/
  ```

- Правки ядра и расширения — по правилам `twenty/DEVELOPMENT-RULES.md`:
  расширения только через `apps/` (twenty-sdk), ядро — минимальными патчами
  поверх тега.

## Локальная разработка

Инструкции upstream — `twenty/README.md`, `twenty/AGENTS.md`, `twenty/CLAUDE.md`.
Кратко: Node 26, Yarn 4 (`corepack`), PostgreSQL 16, Redis 7.

## Деплой

См. `deploy/README.md`.
