#!/usr/bin/env bash
# Деплой системы сообщества на сервере. Вызывается из CI (.github/workflows/deploy.yml)
# и вручную: ./system/deploy/deploy.sh
#
# Идемпотентен: .env создаётся один раз (недостающие ключи дописываются), остатки
# прежнего деплоя Twenty удаляются один раз (с дампом базы), перед обновлением —
# бэкап базы, после up — ожидание /healthz, регистрация webhook ботов и проверка https.
set -Eeuo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$DEPLOY_DIR/../.." && pwd)"
cd "$DEPLOY_DIR"

DOMAIN="${DOMAIN:-kmi9site.ru}"
COMPOSE=(docker compose -p community-system --env-file .env -f docker-compose.yml)
NGINX_CONF="/etc/nginx/sites-enabled/community.${DOMAIN}.conf"

log() { printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*"; exit 1; }
rand() { openssl rand -hex "${1:-24}"; }

ensure_env() {
  umask 077
  if [ ! -f .env ]; then
    log "Первый деплой: генерирую system/deploy/.env (секреты остаются только на сервере)"
    : > .env
  fi
  add() { grep -q "^$1=" .env || printf '%s=%s\n' "$1" "$2" >> .env; }
  add PUBLIC_URL "https://${DOMAIN}"
  add HOST_PORT 3100
  add SECURE_COOKIES true
  add TIMEZONE Europe/Moscow
  add PG_PASSWORD "$(rand 24)"
  add SESSION_SECRET "$(rand 32)"
  add ADMIN_LOGIN admin
  add ADMIN_PASSWORD "$(rand 12)"
  add TELEGRAM_BOT_TOKEN ""
  add TELEGRAM_WEBHOOK_SECRET "$(rand 24)"
  add MAX_BOT_TOKEN ""
  add MAX_WEBHOOK_SECRET "$(rand 24)"
  chmod 600 .env
}

env_value() { grep -E "^$1=" .env | tail -1 | cut -d= -f2-; }

# Прежний деплой Twenty (проект compose «community», каталог /opt/community/deploy):
# дамп его базы в backups/, затем контейнеры, тома, образы и старые файлы удаляются.
cleanup_twenty() {
  local ids
  ids="$(docker ps -aq --filter label=com.docker.compose.project=community || true)"
  if [ -z "$ids" ] && [ ! -d "$REPO_DIR/deploy" ]; then
    return 0
  fi
  log "Удаляю прежний деплой Twenty"
  mkdir -p backups
  local db
  db="$(docker ps -q --filter label=com.docker.compose.project=community --filter label=com.docker.compose.service=db || true)"
  if [ -n "$db" ]; then
    local file="backups/twenty-final-$(date -u +%Y%m%d-%H%M%S).sql.gz"
    if docker exec "$db" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' | gzip > "$file"; then
      log "дамп базы Twenty: $file ($(du -h "$file" | cut -f1))"
    else
      rm -f "$file"
      log "WARN: дамп базы Twenty не удался"
    fi
  fi
  [ -n "$ids" ] && docker rm -f $ids >/dev/null
  for vol in community_db-data community_server-local-data; do
    docker volume rm "$vol" >/dev/null 2>&1 && log "том $vol удалён" || true
  done
  docker network rm community_default >/dev/null 2>&1 || true
  docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '^twentycrm/twenty:' | xargs -r docker rmi >/dev/null 2>&1 || true
  if [ -d "$REPO_DIR/deploy" ]; then
    # старые секреты и бэкапы Twenty — в архив, каталог удаляется
    mkdir -p backups/twenty-legacy
    cp -a "$REPO_DIR/deploy/." backups/twenty-legacy/ 2>/dev/null || true
    rm -rf "$REPO_DIR/deploy"
  fi
  log "Twenty удалён"
}

backup_db() {
  mkdir -p backups
  if ! "${COMPOSE[@]}" ps db --status running --quiet 2>/dev/null | grep -q .; then
    return 0
  fi
  local file="backups/pre-$(date -u +%Y%m%d-%H%M%S).sql.gz"
  log "Бэкап базы → ${file}"
  if "${COMPOSE[@]}" exec -T db pg_dump -U crm -d crm | gzip > "$file"; then
    log "бэкап готов: $(du -h "$file" | cut -f1)"
    ls -1t backups/pre-*.sql.gz | tail -n +8 | xargs -r rm -f # 7 последних
  else
    rm -f "$file"
    log "WARN: pg_dump не удался, продолжаю без бэкапа"
  fi
}

sync_nginx() {
  command -v nginx >/dev/null || { log "WARN: nginx не установлен — пропуск"; return 0; }
  if ! cmp -s nginx/community.${DOMAIN}.conf "$NGINX_CONF"; then
    log "nginx: обновляю $NGINX_CONF"
    install -m 644 nginx/community.${DOMAIN}.conf "$NGINX_CONF"
    nginx -t && systemctl reload nginx
  fi
}

wait_health() {
  local port; port="$(env_value HOST_PORT)"
  log "Жду http://127.0.0.1:${port}/healthz"
  for i in $(seq 1 60); do
    if curl -fsS -m 3 "http://127.0.0.1:${port}/healthz" >/dev/null 2>&1; then
      log "OK: $(curl -fsS -m 3 "http://127.0.0.1:${port}/healthz")"
      return 0
    fi
    sleep 5
  done
  "${COMPOSE[@]}" ps || true
  "${COMPOSE[@]}" logs --tail=80 api || true
  die "система не поднялась за 5 минут"
}

command -v docker >/dev/null || die "docker не установлен"
docker compose version >/dev/null || die "docker compose plugin не установлен"

ensure_env
cleanup_twenty
[ -d "$REPO_DIR/personal_docs/messages" ] || log "WARN: нет $REPO_DIR/personal_docs (тексты бота, уровни, вопросы) — скопируйте каталог на сервер, см. system/deploy/README.md"
backup_db

log "docker compose build"
for attempt in 1 2 3; do
  if "${COMPOSE[@]}" build api; then break; fi
  [ "$attempt" = 3 ] && die "сборка образа не удалась (3 попытки)"
  log "WARN: сборка сорвалась (попытка ${attempt}/3), повтор через 15 с"
  sleep 15
done

log "docker compose up -d"
"${COMPOSE[@]}" up -d --remove-orphans
wait_health
sync_nginx

if [ -n "$(env_value TELEGRAM_BOT_TOKEN)" ] || [ -n "$(env_value MAX_BOT_TOKEN)" ]; then
  log "Регистрирую webhook ботов"
  "${COMPOSE[@]}" exec -T api python -m app.cli set-webhooks || log "WARN: webhook не зарегистрирован"
else
  log "WARN: токены ботов не заданы (TELEGRAM_BOT_TOKEN / MAX_BOT_TOKEN в system/deploy/.env) — боты не подключены"
fi

public_code="$(curl -fsS -o /dev/null -m 15 -w '%{http_code}' "https://${DOMAIN}/healthz" || echo 000)"
log "https://${DOMAIN}/healthz -> ${public_code}"
[ "$public_code" = "200" ] || log "WARN: публичная проверка вернула ${public_code} (nginx/TLS?)"

docker image prune -f >/dev/null 2>&1 || true
log "Готово. Контейнеры:"
"${COMPOSE[@]}" ps --format 'table {{.Name}}\t{{.Status}}'
