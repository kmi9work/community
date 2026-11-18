#!/usr/bin/env bash
# Разовая подготовка нового сервера. От root:
#   curl -fsSL https://raw.githubusercontent.com/kmi9work/community/master/system/deploy/bootstrap.sh | bash
# Делает: git clone в /opt/community, nginx-vhost, первый деплой. Повторный запуск безопасен.
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/kmi9work/community.git}"
APP_DIR="${APP_DIR:-/opt/community}"

log() { printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "нужен root"
command -v docker >/dev/null || die "docker не установлен"
command -v nginx >/dev/null || die "nginx не установлен"

if [ -d "$APP_DIR/.git" ]; then
  log "Репозиторий уже есть: $APP_DIR"
else
  log "Клонирую $REPO_URL → $APP_DIR"
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --branch master "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"
chmod +x system/deploy/deploy.sh
./system/deploy/deploy.sh
log "Готово. Пароль первого администратора: grep ADMIN_ system/deploy/.env"
