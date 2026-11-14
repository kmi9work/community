#!/usr/bin/env bash
# Деплой Twenty на сервере. Вызывается из CI (.github/workflows/deploy.yml)
# и вручную: ./deploy/deploy.sh
#
# Идемпотентен: .env создаётся один раз, бэкап БД делается перед каждым
# обновлением, после up ждём /healthz и проверяем публичный https.
set -Eeuo pipefail

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DEPLOY_DIR"

DOMAIN="${DOMAIN:-kmi9site.ru}"
HOST_PORT="${HOST_PORT:-3100}"
COMPOSE=(docker compose -p community --env-file .env -f docker-compose.yml)

log() { printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

ensure_env() {
  [ -f .env ] && return 0
  log "Первый деплой: генерирую deploy/.env (секреты остаются только на сервере)"
  umask 077
  {
    printf 'TAG=%s\n' "${TAG:-v2.58.0}"
    printf 'SERVER_URL=https://%s\n' "$DOMAIN"
    printf 'HOST_PORT=%s\n' "$HOST_PORT"
    printf 'PG_DATABASE_USER=postgres\n'
    printf 'PG_DATABASE_NAME=default\n'
    printf 'PG_DATABASE_PASSWORD=%s\n' "$(openssl rand -hex 24)"
    printf 'ENCRYPTION_KEY=%s\n' "$(openssl rand -base64 32 | tr -d '\n')"
    printf 'APP_SECRET=%s\n' "$(openssl rand -base64 48 | tr -d '\n')"
  } > .env
  chmod 600 .env
}

backup_db() {
  mkdir -p backups
  if ! "${COMPOSE[@]}" ps db --status running --quiet 2>/dev/null | grep -q .; then
    return 0
  fi
  local stamp file
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  file="backups/pre-${stamp}.sql.gz"
  log "Бэкап БД → ${file}"
  if "${COMPOSE[@]}" exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"' | gzip >"$file"; then
    log "бэкап готов: $(du -h "$file" | cut -f1)"
    ls -1t backups/pre-*.sql.gz | tail -n +8 | xargs -r rm -f # держим 7 последних
  else
    rm -f "$file"
    log "WARN: pg_dump не удался, продолжаю деплой без бэкапа"
  fi
}

wait_health() {
  log "Жду готовности http://127.0.0.1:${HOST_PORT}/healthz"
  for i in $(seq 1 60); do
    if curl -fsS -m 3 "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1; then
      log "OK: Twenty отвечает на /healthz"
      return 0
    fi
    if [ "$i" -eq 60 ]; then
      log "ERROR: Twenty не поднялся за 5 минут"
      "${COMPOSE[@]}" ps || true
      "${COMPOSE[@]}" logs --tail=80 server || true
      return 1
    fi
    sleep 5
  done
}

command -v docker >/dev/null || die "docker не установлен"
docker compose version >/dev/null || die "docker compose plugin не установлен"

ensure_env
backup_db

log "docker compose pull (TAG=$(grep -E '^TAG=' .env | cut -d= -f2))"
"${COMPOSE[@]}" pull

log "docker compose up -d"
"${COMPOSE[@]}" up -d --remove-orphans

wait_health

public_code="$(curl -fsS -o /dev/null -m 15 -w '%{http_code}' "https://${DOMAIN}/healthz" || echo 000)"
log "https://${DOMAIN}/healthz -> ${public_code}"
[ "$public_code" = "200" ] || log "WARN: публичная проверка вернула ${public_code} (nginx/TLS?)"

log "Готово. Контейнеры:"
"${COMPOSE[@]}" ps --format 'table {{.Name}}\t{{.Status}}'
