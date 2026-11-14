#!/usr/bin/env bash
# Разовая подготовка сервера к деплою Twenty. Запускается от root ОДИН раз:
#   curl -fsSL https://raw.githubusercontent.com/kmi9work/community/master/deploy/bootstrap.sh | bash
# или после клонирования репозитория: ./deploy/bootstrap.sh
#
# Делает: каталог /opt/community + git clone, nginx-vhost для kmi9site.ru,
# права на deploy/.env, затем первый деплой. Повторный запуск безопасен.
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/kmi9work/community.git}"
APP_DIR="${APP_DIR:-/opt/community}"
DOMAIN="${DOMAIN:-kmi9site.ru}"
NGINX_CONF="/etc/nginx/sites-enabled/community.${DOMAIN}.conf"

log() { printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }
die() { log "ERROR: $*"; exit 1; }

[ "$(id -u)" -eq 0 ] || die "нужен root"
command -v docker >/dev/null || die "docker не установлен"
command -v nginx >/dev/null || die "nginx не установлен"

# 1. Код на сервере (репозиторий публичный → без ключей и токенов)
if [ -d "$APP_DIR/.git" ]; then
  log "Репозиторий уже есть: $APP_DIR"
else
  log "Клонирую $REPO_URL → $APP_DIR"
  mkdir -p "$(dirname "$APP_DIR")"
  git clone --branch master "$REPO_URL" "$APP_DIR"
fi
cd "$APP_DIR"

# 2. nginx vhost
log "nginx: $NGINX_CONF"
install -m 644 deploy/nginx/community.${DOMAIN}.conf "$NGINX_CONF"
nginx -t
systemctl reload nginx

# 3. Первый деплой
chmod +x deploy/deploy.sh
./deploy/deploy.sh

log "Готово: https://${DOMAIN}"
