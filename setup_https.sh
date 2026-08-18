#!/usr/bin/env bash
#
# Puts HTTPS in front of the admin panel, so it can be reached from a browser
# without exposing a password over plain HTTP.
#
#   ./setup_https.sh admin.example.com
#
# The head deliberately listens on loopback only. This script does not change
# that — it puts Caddy in front, which terminates TLS and forwards locally.
# Run it on the head server, from the repository directory.

set -euo pipefail

DOMAIN="${1:-}"
if [[ -z "$DOMAIN" ]]; then
    cat >&2 <<'USAGE'
Укажите домен:

    ./setup_https.sh admin.вашдомен.ru

Домен должен уже указывать A-записью на этот сервер — Caddy проверяет это,
когда запрашивает сертификат, и без работающей записи получит отказ.

Домена нет? Тогда в админку можно зайти через SSH-туннель, см. DEPLOY.md,
раздел «Попасть в админку».
USAGE
    exit 2
fi

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*"; }

say "1/5 Проверяю, что домен указывает сюда"
# Failure here is fine — the check below degrades to "cannot compare",
# so the error itself would only be noise.
SERVER_IP="$(curl -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
DOMAIN_IP="$(getent hosts "$DOMAIN" | awk '{print $1; exit}' || true)"

if [[ -z "$DOMAIN_IP" ]]; then
    bad "$DOMAIN никуда не резолвится — A-запись ещё не создана или не разошлась"
    echo "     Создайте у регистратора запись: A  ${DOMAIN%%.*}  ->  ${SERVER_IP:-<ip этого сервера>}"
    echo "     Обновление занимает от пары минут до часа. Запустите скрипт снова позже."
    exit 1
elif [[ -n "$SERVER_IP" && "$DOMAIN_IP" != "$SERVER_IP" ]]; then
    bad "$DOMAIN указывает на $DOMAIN_IP, а этот сервер — $SERVER_IP"
    echo "     Caddy не сможет получить сертификат. Поправьте A-запись и запустите снова."
    exit 1
else
    ok "$DOMAIN -> $DOMAIN_IP"
fi

say "2/5 Проверяю, что голова запущена"
if curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null; then
    ok "голова отвечает на 127.0.0.1:8000"
else
    bad "голова не отвечает — сначала 'docker compose up -d', потом этот скрипт"
    exit 1
fi

say "3/5 Ставлю Caddy"
if command -v caddy >/dev/null; then
    ok "уже установлен"
else
    # Caddy is not in the default Debian/Ubuntu repositories; this is the
    # vendor's own repository, as documented by the project.
    apt-get update -qq
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gnupg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
        | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update -qq
    apt-get install -y -qq caddy
    ok "установлен"
fi

say "4/5 Настраиваю"
if [[ -f /etc/caddy/Caddyfile ]] && ! grep -q "FreeSkyVPN" /etc/caddy/Caddyfile; then
    cp /etc/caddy/Caddyfile "/etc/caddy/Caddyfile.backup.$(date +%s)"
    ok "прежний Caddyfile сохранён рядом с расширением .backup"
fi

cat > /etc/caddy/Caddyfile <<EOF
# FreeSkyVPN — admin panel
$DOMAIN {
    reverse_proxy 127.0.0.1:8000
}
EOF
systemctl restart caddy
ok "Caddyfile записан, Caddy перезапущен"

say "5/5 Жду сертификат"
# Issuance normally takes a few seconds, but a cold ACME challenge can be
# slower; poll rather than sleep a fixed amount and hope.
for attempt in $(seq 1 24); do
    if curl -fsS --max-time 5 "https://$DOMAIN/health" >/dev/null 2>&1; then
        ok "сертификат получен, HTTPS работает"
        cat <<DONE

Готово. Админка: https://$DOMAIN/admin

Логин 'admin', пароль — тот, что напечатала команда create-admin.
Забыли: docker compose exec head python -m app.cli create-admin admin

ADMIN_COOKIE_SECURE трогать не нужно — HTTPS есть, оставьте true.
DONE
        exit 0
    fi
    sleep 5
done

bad "сертификат за две минуты не выпустился"
cat <<'HINT'
     Смотрите причину: journalctl -u caddy -n 40 --no-pager
     Частое:
       - порт 80 занят или закрыт файрволом. Let's Encrypt проверяет домен
         именно через него, и без 80-го выпуск не пройдёт, даже если 443 открыт.
       - A-запись поменяли только что и она ещё не разошлась.
HINT
exit 1
