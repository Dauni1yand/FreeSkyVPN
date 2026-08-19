#!/usr/bin/env bash
#
# Sets up FreeSkyVPN on a fresh server: asks for what only a human can
# provide, installs the rest, starts it, and verifies it works.
#
#     git clone <repo> && cd FreeSkyVPN && sudo ./install.sh
#
# Safe to re-run. Existing configuration is kept unless you choose to replace
# it, so a failed run can be resumed rather than started over.

set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

ENV_FILE="$REPO_DIR/.env"
SECRETS_DIR="$REPO_DIR/secrets"
LOG="$REPO_DIR/install.log"

# Prompts read from the terminal directly, so the script still works when
# piped (curl | bash) — where stdin is the script itself, not the user.
TTY=/dev/tty
[[ -e $TTY ]] || TTY=/dev/stdin

# ---------------------------------------------------------------- output ---

BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GRN=$'\033[32m'
YEL=$'\033[33m'; CYA=$'\033[36m'; OFF=$'\033[0m'

step()  { printf '\n%s▸ %s%s\n' "$BOLD" "$*" "$OFF"; }
ok()    { printf '  %s✓%s %s\n' "$GRN" "$OFF" "$*"; }
warn()  { printf '  %s!%s %s\n' "$YEL" "$OFF" "$*"; }
fail()  { printf '  %s✗%s %s\n' "$RED" "$OFF" "$*"; }
info()  { printf '    %s%s%s\n' "$DIM" "$*" "$OFF"; }

die() {
    fail "$1"
    shift
    for line in "$@"; do info "$line"; done
    printf '\n%sПодробности установки: %s%s\n' "$DIM" "$LOG" "$OFF"
    exit 1
}

run() {  # run a noisy command, keep its output in the log
    if ! "$@" >>"$LOG" 2>&1; then
        return 1
    fi
}

ask() {  # ask <prompt> <varname> [default]
    local prompt="$1" varname="$2" default="${3:-}" answer=""
    if [[ -n $default ]]; then
        printf '  %s%s%s [%s]: ' "$CYA" "$prompt" "$OFF" "$default"
    else
        printf '  %s%s%s: ' "$CYA" "$prompt" "$OFF"
    fi
    read -r answer < "$TTY"
    printf -v "$varname" '%s' "${answer:-$default}"
}

confirm() {  # confirm <question>  -> 0 for yes
    local answer=""
    printf '  %s%s%s [y/N]: ' "$CYA" "$1" "$OFF"
    read -r answer < "$TTY"
    [[ ${answer,,} == y || ${answer,,} == yes ]]
}

genkey() { python3 -c 'import secrets; print(secrets.token_urlsafe(48))' 2>/dev/null \
           || openssl rand -base64 36 | tr -d '\n=+/' ; }

# Checked before anything touches the filesystem: writing the log first would
# hand a non-root user a permission error instead of the actual reason.
if [[ $EUID -ne 0 ]]; then
    printf '\n  %s✗%s нужны права root\n' "$RED" "$OFF"
    printf '    %sЗапустите: sudo ./install.sh%s\n\n' "$DIM" "$OFF"
    exit 1
fi

: >"$LOG"

cat <<BANNER

${BOLD}FreeSkyVPN — установка${OFF}
${DIM}Всё, что можно поставить и настроить автоматически, скрипт сделает сам.
Спросит только то, что знаете вы: токен бота и, если есть, домен.${OFF}
BANNER

# ------------------------------------------------------------ 1. система ---

step "1/8  Проверяю систему"

if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    ok "$PRETTY_NAME"
    case "$ID" in
        ubuntu|debian) ;;
        *) warn "скрипт рассчитан на Ubuntu/Debian, на $ID может потребоваться ручная установка Docker" ;;
    esac
else
    warn "не удалось определить дистрибутив"
fi

for tool in curl python3; do
    command -v "$tool" >/dev/null && continue
    info "ставлю $tool"
    run apt-get update -qq
    run apt-get install -y -qq "$tool" || die "не удалось установить $tool"
done
ok "curl, python3 на месте"

# ------------------------------------------------------------- 2. docker ---

step "2/8  Docker"

if command -v docker >/dev/null && docker compose version >/dev/null 2>&1; then
    ok "уже установлен"
else
    info "ставлю Docker с get.docker.com — это займёт пару минут"
    if ! curl -fsSL https://get.docker.com | sh >>"$LOG" 2>&1; then
        die "установка Docker не удалась" \
            "Поставьте вручную и запустите скрипт снова:" \
            "  curl -fsSL https://get.docker.com | sh"
    fi
    ok "установлен"
fi

if ! docker info >/dev/null 2>&1; then
    info "запускаю Docker"
    run systemctl enable --now docker
    sleep 3
    docker info >/dev/null 2>&1 || die "Docker не запускается" \
        "Посмотрите: systemctl status docker"
fi
ok "демон работает"

docker compose version >/dev/null 2>&1 || die "нет 'docker compose'" \
    "Установлен Docker без плагина compose. Обновите Docker до актуальной версии."

# -------------------------------------------------------------- 3. .env ---

step "3/8  Настройки"

KEEP_ENV=false
if [[ -f $ENV_FILE ]]; then
    warn ".env уже существует"
    if confirm "Оставить как есть? (нет — заполним заново)"; then
        KEEP_ENV=true
        ok "использую существующий .env"
    else
        cp "$ENV_FILE" "$ENV_FILE.backup.$(date +%s)"
        info "прежний сохранён рядом с расширением .backup"
    fi
fi

if [[ $KEEP_ENV == false ]]; then
    echo
    info "Токен бота выдаёт @BotFather: /newbot, затем скопируйте строку вида"
    info "1234567890:AAH... — её и вставьте."
    echo

    BOT_TOKEN=""
    TG_REACHABLE=true
    while [[ -z $BOT_TOKEN ]]; do
        ask "Токен Telegram-бота" BOT_TOKEN
        # Вставка из Windows приносит с собой \r, он уходит прямо в URL и
        # делает верный токен неверным. Пробелы по краям — то же самое.
        BOT_TOKEN="${BOT_TOKEN//[$'\r\n\t ']/}"
        [[ -z $BOT_TOKEN ]] && { fail "без токена бот работать не будет"; continue; }

        # Проверяется у Telegram, а не по маске: опечатка иначе всплывёт
        # много позже как молчащий бот.
        #
        # «Telegram отказал» и «до Telegram не достучались» разводятся
        # намеренно. Раньше и то и другое печаталось как «токен не принят»,
        # и человек с верным токеном перебирал его снова и снова, хотя
        # чинить надо было сеть.
        TG_BODY=$(mktemp); TG_ERR=$(mktemp)
        HTTP_CODE=$(curl -sS --max-time 15 -o "$TG_BODY" -w '%{http_code}' \
                    "https://api.telegram.org/bot${BOT_TOKEN}/getMe" 2>"$TG_ERR")
        CURL_RC=$?

        if [[ $CURL_RC -ne 0 ]]; then
            TG_REACHABLE=false
            fail "не удалось связаться с api.telegram.org"
            sed 's/^/      /' "$TG_ERR"
            info "Токен здесь, скорее всего, ни при чём: сервер не достучался"
            info "до Telegram. Проверьте с этого же сервера:"
            info "    curl -sS -o /dev/null -w '%{http_code}\\n' https://api.telegram.org"
            info "Частое: нет DNS, закрыт исходящий 443, нужен прокси у хостера."
            warn "Пока сервер не видит Telegram, бот работать не будет."
            if confirm "Всё равно продолжить, проверив токен позже?"; then
                ok "токен принят без проверки"
            else
                BOT_TOKEN=""
            fi
        elif BOT_NAME=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["result"]["username"] if d.get("ok") else "")' "$TG_BODY" 2>/dev/null) \
             && [[ -n $BOT_NAME ]]; then
            ok "бот @$BOT_NAME"
        else
            TG_REASON=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("description",""))' "$TG_BODY" 2>/dev/null)
            fail "Telegram отказал (HTTP $HTTP_CODE)${TG_REASON:+: $TG_REASON}"
            info "Это уже про сам токен: неверен или отозван."
            info "Действующий покажет @BotFather командой /mytoken."
            BOT_TOKEN=""
        fi
        rm -f "$TG_BODY" "$TG_ERR"
    done

    echo
    info "Ваш Telegram id нужен, чтобы слать вам оповещения и спрашивать"
    info "подтверждение на обновления. Узнать: напишите @userinfobot."
    echo
    ask "Ваш Telegram id (Enter — пропустить)" ADMIN_CHAT_ID

    if [[ -n $ADMIN_CHAT_ID ]] && [[ $TG_REACHABLE == false ]]; then
        warn "проверочное сообщение не отправляю — сервер не видит Telegram"
    elif [[ -n $ADMIN_CHAT_ID ]]; then
        if curl -fsS --max-time 15 -X POST \
             "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
             -d "chat_id=${ADMIN_CHAT_ID}" \
             -d "text=FreeSkyVPN: установка запущена. Оповещения будут приходить сюда." \
             >>"$LOG" 2>&1; then
            ok "проверочное сообщение отправлено — загляните в Telegram"
        else
            warn "сообщение не доставлено"
            info "Обычно это значит, что вы ещё не написали боту /start —"
            info "Telegram не даёт писать первым. Оповещения включатся после этого."
        fi
    fi

    echo
    ask "Домен для админки (Enter — пока без домена)" DOMAIN

    umask 077
    cat > "$ENV_FILE" <<EOF
# Сгенерировано install.sh $(date -Iseconds)

POSTGRES_USER=freeskyvpn
POSTGRES_PASSWORD=$(genkey)
POSTGRES_DB=freeskyvpn

HEAD_SECRET_KEY=$(genkey)
SECRETS_KEY=$(genkey)
ADMIN_API_TOKEN=$(genkey)

TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_ADMIN_CHAT_ID=$ADMIN_CHAT_ID
TELEGRAM_ALLOWED_CHAT_IDS=$ADMIN_CHAT_ID

ADMIN_COOKIE_SECURE=$([[ -n $DOMAIN ]] && echo true || echo false)
ADMIN_DOMAIN=$DOMAIN
EOF
    umask 022
    ok ".env создан"

    if [[ -z $DOMAIN ]]; then
        warn "без домена cookie админки помечена как небезопасная (ADMIN_COOKIE_SECURE=false)"
        info "Это нужно, чтобы вход работал через SSH-туннель по http."
        info "Настроите домен — запустите ./setup_https.sh и верните true."
    fi
fi

set -a; . "$ENV_FILE"; set +a

# An .env written by an older release is missing whatever keys a newer one
# added. Re-running this script is how people upgrade, and the branch above
# deliberately leaves an existing .env untouched — so fill the gaps here.
# Without this the head starts with an empty secret and refuses every request
# that needs it, which surfaces as a bot that stopped working rather than as
# anything pointing back at the upgrade.
ensure_key() {  # ensure_key <ИМЯ> <значение>; 0 — уже было, 1 — создан
    local key=$1 value=$2
    [[ -n ${!key:-} ]] && return 0
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
    export "$key=$value"
    return 1
}

ensure_key HEAD_SECRET_KEY "$(genkey)" || info "в .env добавлен HEAD_SECRET_KEY"
ensure_key ADMIN_API_TOKEN "$(genkey)" || info "в .env добавлен ADMIN_API_TOKEN"

# A regenerated SECRETS_KEY is not a neutral fix: node SSH passwords in the
# database were encrypted with the old one and become unreadable. Said out
# loud, because the alternative is discovering it at the next node push.
if ! ensure_key SECRETS_KEY "$(genkey)"; then
    warn "в .env не было SECRETS_KEY — создан новый"
    info "Если ноды уже добавлены, их SSH-пароли прежним ключом не расшифровать —"
    info "введите их заново в админке."
fi

# ------------------------------------------------------- 4. сертификаты ---

step "4/8  Сертификат головы"

if [[ -f $SECRETS_DIR/head_client_cert.pem ]]; then
    ok "уже есть"
else
    mkdir -p "$SECRETS_DIR"
    if ! run ./provisioning/generate_head_client_cert.sh "$SECRETS_DIR"; then
        die "не удалось создать сертификат" "Проверьте, что установлен openssl"
    fi
    chmod 600 "$SECRETS_DIR/head_client_key.pem"
    ok "создан"
fi
info "Каждая нода будет доверять именно ему. Потеряете — ноды придётся переустановить."

# ------------------------------------------------------------ 5. сборка ---

step "5/8  Сборка и запуск"
info "первый раз это 3–5 минут"

if ! run docker compose up -d --build; then
    die "сборка или запуск не удались" \
        "Последние строки — в $LOG" \
        "Часто: не хватает места (df -h) или недоступен github.com"
fi
ok "контейнеры запущены"

# ---------------------------------------------------------- 6. проверка ---

step "6/8  Жду готовности"

READY=false
for _ in $(seq 1 60); do
    if curl -fsS --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1; then
        READY=true; break
    fi
    sleep 2
done

if [[ $READY == false ]]; then
    fail "голова не поднялась за две минуты"
    info "Последние строки лога головы:"
    docker compose logs --tail 25 head 2>&1 | sed 's/^/      /'
    die "установка остановлена" "Исправьте причину выше и запустите ./install.sh снова"
fi
ok "голова отвечает"

for service in db head bot; do
    state=$(docker compose ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$service" '$1==s{print $2}')
    case "$state" in
        running) ok "$service работает" ;;
        "")      warn "$service не найден" ;;
        *)       warn "$service в состоянии '$state'"
                 docker compose logs --tail 15 "$service" 2>&1 | sed 's/^/      /' ;;
    esac
done

# ------------------------------------------------------- 7. администратор ---

step "7/8  Администратор"

ADMIN_OUT=$(docker compose exec -T head python -m app.cli create-admin admin 2>&1)
ADMIN_PASS=$(printf '%s' "$ADMIN_OUT" | awk '/^password: /{print $2}')

if [[ -n $ADMIN_PASS ]]; then
    ok "создан"
else
    warn "не удалось разобрать вывод команды:"
    printf '%s\n' "$ADMIN_OUT" | sed 's/^/      /'
fi

# ------------------------------------------------------------- 8. HTTPS ---

step "8/8  Доступ к админке"

ADMIN_URL="http://127.0.0.1:8000/admin"
if [[ -n ${ADMIN_DOMAIN:-} ]]; then
    if ./setup_https.sh "$ADMIN_DOMAIN" 2>&1 | tee -a "$LOG" | sed 's/^/    /'; then
        ADMIN_URL="https://$ADMIN_DOMAIN/admin"
    else
        warn "HTTPS настроить не удалось — админка пока только через SSH-туннель"
        info "Причина выше. Исправите — запустите ./setup_https.sh $ADMIN_DOMAIN"
    fi
else
    info "домен не задан, наружу админка не открыта"
fi

# ------------------------------------------------------------- итог ---

step "Проверка"
docker compose exec -T head python smoke_test.py \
    --token "$HEAD_SECRET_KEY" \
    --admin-token "$ADMIN_API_TOKEN" \
    ${ADMIN_PASS:+--admin-user admin --admin-password "$ADMIN_PASS"} 2>&1 | sed 's/^/  /'

cat <<SUMMARY

${BOLD}Установка завершена${OFF}

  Админка   ${CYA}${ADMIN_URL}${OFF}
  Логин     admin
SUMMARY

if [[ -n $ADMIN_PASS ]]; then
    printf '  Пароль    %s%s%s\n' "$BOLD" "$ADMIN_PASS" "$OFF"
    printf '  %sЗапишите его сейчас — второй раз он не покажется.%s\n' "$DIM" "$OFF"
fi

if [[ -z ${ADMIN_DOMAIN:-} ]]; then
    cat <<TUNNEL

  ${BOLD}Как зайти без домена${OFF}
  На своём компьютере:
      ssh -L 8000:127.0.0.1:8000 root@$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || echo '<ip>')
  Затем в браузере: http://127.0.0.1:8000/admin
TUNNEL
fi

cat <<NEXT

  ${BOLD}Дальше${OFF}
  1. Зайдите в админку и добавьте ноду: раздел «Ноды».
     Понадобится IP заграничного сервера и SSH-доступ к нему.
  2. Напишите боту /start и нажмите «Подключиться».
     Доступ выдаётся за просмотр рекламы в приложении; в боте рекламу
     показать нельзя, поэтому для тестов добавьте свой id в
     TELEGRAM_ALLOWED_CHAT_IDS.
  3. Обновления Xray голова находит сама и спрашивает вас в Telegram —
     сама ничего не ставит. То же в админке, раздел «Обновления».

  ${DIM}Сохраните отдельно от сервера: .env и папку secrets/.
  Без них доступ к нодам не восстановить.${OFF}

NEXT
