# Деплой

Система сообщества в Docker Compose на сервере **45.133.210.22**, публичный адрес
**https://kmi9site.ru** (nginx на этом же сервере проксирует на `127.0.0.1:3100`).

Контейнеры (проект compose `community-system`):

| Сервис | Что делает | Память |
| --- | --- | --- |
| `api` | админка (React PWA), API, webhook Телеграм и MAX; при старте — миграции и начальное наполнение | 450 МБ |
| `worker` | очередь «Доставка рассылки», напоминания, очистка служебных таблиц | 200 МБ |
| `db` | PostgreSQL 16, том `pgdata` | 256 МБ |

Афиши лежат в томе `media`. Тексты бота, уровни и стартовые вопросы — каталог
`/opt/community/personal_docs` (в git не хранится, монтируется только на чтение).

## Как работает CI/CD

`.github/workflows/deploy.yml` — на каждый push в `master` (и по кнопке «Run workflow»):

1. тесты бэкенда (pytest на PostgreSQL) и сборка админки;
2. GitHub Actions подключается к серверу по SSH ключом из секрета `DEPLOY_SSH_KEY`;
3. на сервере: `git fetch && git reset --hard origin/master` в `/opt/community`;
4. `system/deploy/deploy.sh`:
   - создаёт `system/deploy/.env` с секретами (один раз; недостающие ключи дописывает);
   - один раз удаляет прежний деплой Twenty: дамп его базы в
     `system/deploy/backups/twenty-final-*.sql.gz`, контейнеры, тома и образы
     Twenty, старый каталог `/opt/community/deploy` (его `.env` и бэкапы — в
     `system/deploy/backups/twenty-legacy/`);
   - бэкап базы → сборка образа → `up -d` → ожидание `/healthz`;
   - синхронизирует nginx-vhost;
   - регистрирует webhook ботов, если токены заданы;
   - проверяет `https://kmi9site.ru/healthz`.

Деплои не идут параллельно: второй ждёт первого.

## Первичная настройка

1. **Тексты сообщества.** С машины, где лежит `personal_docs/`:

   ```bash
   rsync -a --delete personal_docs/ root@45.133.210.22:/opt/community/personal_docs/
   ```

   После правки текстов — `docker compose ... restart api worker` (тексты читаются
   при старте). Уровни, типы мероприятий и вопросы берутся из файлов только при
   первом наполнении пустой базы — дальше они правятся в админке.

2. **Пароль администратора.** Генерируется при первом деплое:

   ```bash
   grep ADMIN_ /opt/community/system/deploy/.env
   ```

   Войти на https://kmi9site.ru и сменить пароль в «Профиле».

3. **Боты.** Вписать токены в `/opt/community/system/deploy/.env`:

   ```
   TELEGRAM_BOT_TOKEN=123456:ABC...   # от @BotFather
   MAX_BOT_TOKEN=...                  # из кабинета MAX для партнёров
   ```

   и запустить `./system/deploy/deploy.sh` — он перезапустит контейнеры и
   зарегистрирует webhook (`https://kmi9site.ru/webhook/telegram` и `/webhook/max`
   с секретами из `.env`).

4. **Своя карточка участника.** Чтобы работала кнопка «Отправить себе»,
   администратор регистрируется в боте и выбирает свою карточку в «Профиле».

## Секрет репозитория

Settings → Secrets and variables → Actions → `DEPLOY_SSH_KEY` — приватный SSH-ключ
деплоя целиком. Публичная часть — в `/root/.ssh/authorized_keys` на сервере. Хост,
пользователь и путь — в `env` воркфлоу.

## Новый сервер

```bash
curl -fsSL https://raw.githubusercontent.com/kmi9work/community/master/system/deploy/bootstrap.sh | bash
```

Нужны docker с compose-плагином, nginx и сертификат Let's Encrypt для домена.

## Отладка

```bash
cd /opt/community/system/deploy
C="docker compose -p community-system --env-file .env -f docker-compose.yml"
$C ps
$C logs -f --tail 100 api worker        # журналы — JSON, одна строка — одно событие
curl -s 127.0.0.1:3100/healthz
curl -s 127.0.0.1:3100/metrics | grep delivery_queue
$C exec api python -m app.cli create-admin LOGIN   # новый администратор / сброс пароля
```

Локально без webhook: `python -m app.worker --poll` (long polling Телеграм и MAX).

## Бэкапы и откат

Перед каждым обновлением — `pg_dump` в `system/deploy/backups/pre-*.sql.gz`,
хранятся 7 последних. Восстановление:

```bash
gunzip -c backups/pre-....sql.gz | $C exec -T db psql -U crm -d crm
```

Откат кода: `git reset --hard <коммит> && ./system/deploy/deploy.sh`. Миграции
вперёд-назад — `$C exec api alembic downgrade -1`.
