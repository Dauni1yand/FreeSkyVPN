#!/usr/bin/env bash
#
# Достаёт три вещи, которых нет в Maven и без которых приложение собирается,
# но не подключается:
#
#   1. libXray.aar               ядро Xray             (MPL-2.0)
#   2. libhev-socks5-tunnel.so   tun2socks             (MIT)
#   3. geoip.dat / geosite.dat   базы для geoip:ru
#
# Запускать со своей машины: из среды, где писался проект, GitHub недоступен.
#
#   cd android && ./fetch-libs.sh
#
# Ассеты релизов ищутся по маске через GitHub API, а не по зашитой ссылке:
# имена файлов в релизах меняются, и скрипт, который молча качает 404,
# хуже, чем скрипт, который скажет, что именно лежит в релизе.

set -uo pipefail
cd "$(dirname "$0")"

BOLD=$'\033[1m'; DIM=$'\033[2m'; GRN=$'\033[32m'; RED=$'\033[31m'; YLW=$'\033[33m'; OFF=$'\033[0m'
ok()   { printf "  ${GRN}✓${OFF} %s\n" "$*"; }
bad()  { printf "  ${RED}✗${OFF} %s\n" "$*"; }
warn() { printf "  ${YLW}!${OFF} %s\n" "$*"; }
step() { printf "\n${BOLD}%s${OFF}\n" "$*"; }

need() {
    command -v "$1" >/dev/null || { bad "нужен $1"; exit 1; }
}
need curl

# Ищет в последнем релизе ассет по регулярке и печатает его URL.
asset_url() {
    local repo="$1" pattern="$2"
    curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" 2>/dev/null \
        | grep -o '"browser_download_url": *"[^"]*"' \
        | cut -d'"' -f4 \
        | grep -iE "$pattern" \
        | head -1
}

list_assets() {
    local repo="$1"
    curl -fsSL "https://api.github.com/repos/${repo}/releases/latest" 2>/dev/null \
        | grep -o '"browser_download_url": *"[^"]*"' \
        | cut -d'"' -f4 \
        | sed 's|.*/||' \
        | sed 's/^/      /'
}

fetch() {
    local repo="$1" pattern="$2" dest="$3"
    if [[ -s $dest ]]; then
        ok "$(basename "$dest") уже на месте"
        return 0
    fi

    local url
    url="$(asset_url "$repo" "$pattern")"
    if [[ -z $url ]]; then
        bad "в последнем релизе $repo нет файла по маске /$pattern/"
        printf "    ${DIM}что там есть:${OFF}\n"
        list_assets "$repo"
        return 1
    fi

    mkdir -p "$(dirname "$dest")"
    if curl -fsSL --retry 3 -o "$dest" "$url"; then
        ok "$(basename "$dest")  ${DIM}$(du -h "$dest" | cut -f1)${OFF}"
    else
        bad "не скачался: $url"
        rm -f "$dest"
        return 1
    fi
}

FAILED=0

step "1/3  Ядро Xray"
printf "  ${DIM}MPL-2.0. Намеренно не AndroidLibXrayLite из v2rayNG:\n"
printf "  та под GPL-3.0 и обязала бы открыть исходники приложения.${OFF}\n"
fetch "XTLS/libXray" '\.aar$' "app/libs/libXray.aar" || FAILED=1

step "2/3  tun2socks"
printf "  ${DIM}У Xray нет tun-инбаунда: пакеты с tun-устройства кто-то\n"
printf "  должен превращать в потоки и отдавать в SOCKS.${OFF}\n"
if [[ -s app/src/main/jniLibs/arm64-v8a/libhev-socks5-tunnel.so ]]; then
    ok "уже на месте"
else
    warn "готовых .so для Android в релизах обычно нет — собирается из исходников"
    cat <<'HINT'
      git clone --recursive https://github.com/heiher/hev-socks5-tunnel
      cd hev-socks5-tunnel
      # нужен Android NDK; путь к нему в ANDROID_NDK_HOME
      ndk-build -C android
      # затем разложить получившиеся .so по ABI:
      #   android/app/src/main/jniLibs/{arm64-v8a,armeabi-v7a,x86,x86_64}/
HINT
    FAILED=1
fi

step "3/3  Базы geoip / geosite"
printf "  ${DIM}Без них правило geoip:ru не находит ничего, и часть\n"
printf "  российского трафика уходит в туннель вместо прямого пути.${OFF}\n"
fetch "Loyalsoldier/v2ray-rules-dat" 'geoip\.dat$'   "app/src/main/assets/geoip.dat"   || FAILED=1
fetch "Loyalsoldier/v2ray-rules-dat" 'geosite\.dat$' "app/src/main/assets/geosite.dat" || FAILED=1

step "Итог"
if [[ $FAILED -eq 0 ]]; then
    ok "всё на месте — можно собирать: ./gradlew :app:assembleDebug"
else
    warn "чего-то не хватает. Приложение соберётся и запустится, но"
    warn "подключиться не сможет и скажет об этом — см. app/libs/README.md"
fi
exit $FAILED
