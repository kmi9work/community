# Деплой

Twenty v2.58.0 (prebuilt-образ `twentycrm/twenty`) в Docker Compose на сервере
**45.133.210.22**, публичный адрес **https://kmi9site.ru**
(nginx на этом же сервере проксирует на `127.0.0.1:3100`).

## Как работает CI/CD

`.github/workflows/deploy.yml` — на каждый push в `master` (и по кнопке
«Run workflow»):

1. GitHub Actions подключается к серверу по SSH ключом из секрета `DEPLOY_SSH_KEY`;
2. на сервере: `git fetch && git reset --hard origin/master` в `/opt/community`;
3. `deploy/deploy.sh`: бэкап БД → `docker compose pull` → `up -d` → ожидание
   `/healthz` (до 5 мин) → проверка `https://kmi9site.ru/healthz`.

Секреты приложения (пароль Postgres, `ENCRYPTION_KEY`, `APP_SECRET`) генерируются
на сервере при первом деплое в `deploy/.env` (chmod 600) и в git не попадают.

### Секрет репозитория

Settings → Secrets and variables → Actions → **New repository secret**:

| Имя | Значение |
|---|---|
| `DEPLOY_SSH_KEY` | приватный SSH-ключ деплоя целиком (от `-----BEGIN OPENSSH PRIVATE KEY-----` до `-----END OPENSSH PRIVATE KEY-----`) |

Публичная часть ключа уже добавлена в `/root/.ssh/authorized_keys` на сервере.
Хост, пользователь и путь заданы в `env` воркфлоу (`DEPLOY_HOST`, `DEPLOY_USER`,
`DEPLOY_PATH`) — при переезде на другой сервер правится только там.

## Первичная установка на новом сервере

```bash
# от root на сервере
curl -fsSL https://raw.githubusercontent.com/kmi9work/community/master/deploy/bootstrap.sh | bash
```

Скрипт клонирует репозиторий в `/opt/community`, ставит nginx-vhost
(HTTP→HTTPS + проксирование), затем запускает первый деплой.

## Ручной деплой / отладка

```bash
cd /opt/community && git fetch --prune origin && git reset --hard origin/master
./deploy/deploy.sh
docker compose -p community --env-file deploy/.env -f deploy/docker-compose.yml ps
tail -f /opt/community/deploy/backups/../logs 2>/dev/null || docker compose -p community logs -f --tail 100 server
```

## Бэкапы

Перед каждым обновлением `deploy/deploy.sh` делает `pg_dump` в
`/opt/community/deploy/backups/pre-YYYYmmdd-HHMMSS.sql.gz`, хранятся 7 последних.
Восстановление:

```bash
gunzip -c deploy/backups/pre-....sql.gz | docker compose -p community exec -T db \
  sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

## Обновление версии Twenty

1. Обновить код в `twenty/` (см. `../README.md`) — если нужны изменения ядра;
2. поменять `TAG=` в `deploy/.env` на сервере (или в `.env.example` для новых
   установок) и запустить `./deploy/deploy.sh`;
3. образ потянется, миграции применит `server` (у `worker` они отключены).

## Ресурсы сервера

VPS: 1 vCPU, 2 ГБ RAM + 4 ГБ swap, ~13 ГБ диска. Ограничения в compose:
`--max-old-space-size=1024` у server, `512` у worker, `shared_buffers=128MB` у
Postgres. Стек рассчитан на «минимальный» режим Twenty; при росте нагрузки
(много пользователей, вложения, воркфлоу) нужен сервер от 2 vCPU / 4 ГБ RAM.

## Откат

```bash
cd /opt/community && git reset --hard <предыдущий-коммит> && ./deploy/deploy.sh
```

Для отката только образа — вернуть прежний `TAG` в `deploy/.env` и перезапустить.
