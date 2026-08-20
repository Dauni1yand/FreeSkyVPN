#!/usr/bin/env python3
"""Интерактивная админка FreeSkyVPN — то же, что делает веб-панель, но по ssh.

Запускается одной командой:

    freeskyvpn

Существует потому, что настраивают сервер по ssh, а панель слушает
loopback: чтобы до неё добраться, нужен туннель и браузер. На машине, к
которой вы уже подключены, это лишний круг.

Устроено тонким слоем: своей логики здесь нет совсем. Всё, что касается
нод, пользователей и доступа, уходит в `python -m app.cli` внутри
контейнера головы; всё, что касается контейнеров и .env, делается здесь,
потому что изнутри контейнера этого не сделать. Такое разделение выбрано
не из аккуратности — меню, которое само решает, кому выдать доступ, стало
бы вторым местом, где живёт это правило, и рано или поздно разошлось бы с
первым.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
ENV_FILE = REPO / ".env"

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GRN = "\033[32m"
YEL = "\033[33m"
CYA = "\033[36m"
OFF = "\033[0m"


# --- вывод -----------------------------------------------------------------


def clear() -> None:
    if sys.stdout.isatty():
        print("\033[2J\033[H", end="")


def title(text: str) -> None:
    print(f"\n{BOLD}{text}{OFF}")
    print(DIM + "─" * max(24, len(text)) + OFF)


def ok(text: str) -> None:
    print(f"  {GRN}✓{OFF} {text}")


def warn(text: str) -> None:
    print(f"  {YEL}!{OFF} {text}")


def fail(text: str) -> None:
    print(f"  {RED}✗{OFF} {text}")


def info(text: str) -> None:
    print(f"    {DIM}{text}{OFF}")


def pause() -> None:
    input(f"\n{DIM}Enter — назад{OFF} ")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"  {CYA}{prompt}{OFF}{suffix}: ").strip()
    return answer or default


def confirm(question: str) -> bool:
    return input(f"  {CYA}{question}{OFF} [y/N]: ").strip().lower() in ("y", "yes", "д", "да")


# --- запуск команд ---------------------------------------------------------


def compose(*args: str, quiet: bool = False) -> int:
    """docker compose в каталоге проекта."""
    stdout = subprocess.DEVNULL if quiet else None
    return subprocess.call(
        ["docker", "compose", *args], cwd=REPO, stdout=stdout, stderr=stdout if quiet else None
    )


def cli(*args: str) -> int:
    """python -m app.cli внутри головы, вывод — на экран как есть."""
    return subprocess.call(
        ["docker", "compose", "exec", "-T", "head", "python", "-m", "app.cli", *args], cwd=REPO
    )


def cli_json(*args: str):
    """То же, но результат разбирается. None — команда не отработала.

    Голова может быть не поднята, и это самая частая причина: сообщение
    должно говорить об этом, а не показывать разбор пустой строки.
    """
    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "head", "python", "-m", "app.cli", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        fail("голова не отвечает")
        info((result.stderr or result.stdout).strip().splitlines()[-1:][0] if (result.stderr or result.stdout).strip() else "")
        info("Проверьте: docker compose ps")
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        fail("не разобрать ответ головы")
        info(result.stdout[:200])
        return None


# --- .env ------------------------------------------------------------------


def env_read() -> dict[str, str]:
    values: dict[str, str] = {}
    if not ENV_FILE.exists():
        return values
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def env_set(key: str, value: str) -> None:
    """Меняет одну строку, сохраняя остальной файл и комментарии.

    Переписать файл целиком было бы проще и стёрло бы объяснения, ради
    которых он и написан по-человечески.
    """
    lines = ENV_FILE.read_text(encoding="utf-8").splitlines() if ENV_FILE.exists() else []
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


#: Гоняется внутри головы: только там есть и токен, и настроенный маршрут.
TELEGRAM_PROBE = """
import os
import httpx

proxy = os.environ.get("TELEGRAM_PROXY_URL") or None
token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
print("маршрут:", proxy or "напрямую")
if not token:
    raise SystemExit("нет TELEGRAM_BOT_TOKEN")
try:
    with httpx.Client(proxy=proxy, timeout=20) as client:
        data = client.get(f"https://api.telegram.org/bot{token}/getMe").json()
except Exception as exc:
    raise SystemExit(f"не достучаться: {type(exc).__name__}: {exc}")
if data.get("ok"):
    print("ок, бот @" + data["result"]["username"])
else:
    raise SystemExit("Telegram отказал: " + str(data.get("description")))
"""


# --- экраны ----------------------------------------------------------------


def screen_nodes() -> None:
    while True:
        clear()
        title("Ноды")
        nodes = cli_json("list-nodes", "--json")
        if nodes is None:
            pause()
            return
        if not nodes:
            warn("нод нет — без них приложение не выдаст ни одного конфига")
        for number, node in enumerate(nodes, 1):
            state = node["status"]
            mark = GRN if state == "active" and node["channel"] == "active" else YEL
            if node["channel"] == "isolated":
                mark = RED
            print(
                f"  {number}. {mark}{node['host']:<16}{OFF} {node['country']:<14} "
                f"{state:<9} канал:{node['channel']:<9} "
                f"{node['users']}/{node['capacity']}"
            )

        print(f"\n  {BOLD}a{OFF} добавить   {BOLD}c{OFF} проверить связь   "
              f"{BOLD}e{OFF} ёмкость   {BOLD}s{OFF} в ротацию / из ротации   "
              f"{BOLD}d{OFF} удалить   {BOLD}0{OFF} назад")
        choice = input("\n  > ").strip().lower()

        if choice in ("0", "q", ""):
            return
        if choice == "a":
            host = ask("IP ноды")
            if not host:
                continue
            country = ask("Страна (метка для вас)", "Netherlands")
            password = ask("Пароль root от хостера")
            port = ask("Порт ssh", "22")
            if not password:
                fail("без пароля подключиться не к чему")
                pause()
                continue
            print()
            cli("add-node", host, country, password, "--ssh-port", port)
            pause()
            continue
        if choice == "c":
            host = ask("IP ноды (Enter — проверить выход головы наружу)", "github.com")
            print()
            cli("check-node", host)
            pause()
            continue

        node = _pick(nodes, choice)
        if node is None:
            continue
        if choice == "e":
            value = ask(f"Новая ёмкость для {node['host']}", str(node["capacity"]))
            if value.isdigit():
                print()
                cli("node-capacity", node["id"], value)
                pause()
        elif choice == "s":
            new_state = "draining" if node["status"] == "active" else "active"
            print()
            cli("node-status", node["id"], new_state)
            pause()
        elif choice == "d":
            if node["users"]:
                warn(f"на {node['host']} сейчас {node['users']} чел. — они останутся без связи")
            if confirm(f"Удалить {node['host']}?"):
                print()
                cli("node-delete", node["id"], "--yes")
                pause()


def _pick(nodes: list[dict], action: str) -> dict | None:
    """Спрашивает, к какой ноде применить действие."""
    if not nodes:
        fail("нод нет")
        pause()
        return None
    if action not in ("e", "s", "d"):
        return None
    raw = ask("Номер ноды")
    if not raw.isdigit() or not 1 <= int(raw) <= len(nodes):
        return None
    return nodes[int(raw) - 1]


def screen_users() -> None:
    while True:
        clear()
        title("Пользователи и доступ")
        data = cli_json("status", "--json")
        if data is None:
            pause()
            return
        print(f"  Всего            {data['users_total']}")
        print(f"  Со временем      {data['users_online']}")
        print(f"  Заблокировано    {data['users_banned']}")
        print(f"  Занято мест      {data['assignments_live']} из {data['capacity']}")
        info("Время покупается просмотром рекламы в приложении.")
        info("Выдача руками — для тестов; она обходит то, чем оплачиваются серверы.")

        print(f"\n  {BOLD}g{OFF} выдать время по user_id   {BOLD}0{OFF} назад")
        choice = input("\n  > ").strip().lower()
        if choice in ("0", "q", ""):
            return
        if choice == "g":
            user_id = ask("user_id")
            if not user_id:
                continue
            minutes = ask("Сколько минут", "60")
            print()
            cli("grant", user_id, minutes)
            pause()


def screen_telegram() -> None:
    while True:
        clear()
        title("Telegram и бот")
        env = env_read()
        proxy = env.get("TELEGRAM_PROXY_URL", "")
        profiles = env.get("COMPOSE_PROFILES", "")
        egress_on = "egress" in profiles

        print(f"  Выход к Telegram   {proxy or 'напрямую'}")
        print(f"  Контейнер egress   {'включён' if egress_on else 'выключен'}")
        print(f"  Операторы бота     {env.get('TELEGRAM_ALLOWED_CHAT_IDS') or env.get('TELEGRAM_ADMIN_CHAT_ID') or '— никого'}")
        if not (env.get("TELEGRAM_ALLOWED_CHAT_IDS") or env.get("TELEGRAM_ADMIN_CHAT_ID")):
            warn("бот не ответит никому, включая вас — впишите свой id")

        print(f"\n  {BOLD}1{OFF} проверить доступность Telegram")
        print(f"  {BOLD}2{OFF} {'выключить' if egress_on else 'включить'} выход через свои ноды")
        print(f"  {BOLD}3{OFF} задать операторов бота")
        print(f"  {BOLD}0{OFF} назад")
        choice = input("\n  > ").strip()

        if choice in ("0", "q", ""):
            return
        if choice == "1":
            print()
            subprocess.call(
                ["docker", "compose", "exec", "-T", "head", "python", "-c", TELEGRAM_PROBE],
                cwd=REPO,
            )
            pause()
        elif choice == "2":
            if egress_on:
                env_set("COMPOSE_PROFILES", profiles.replace("egress", "").strip(", "))
                env_set("TELEGRAM_PROXY_URL", "")
                ok("выключено; применяю")
                compose("stop", "egress", quiet=True)
                compose("up", "-d")
            else:
                nodes = cli_json("list-nodes", "--json") or []
                if not any(n["status"] == "active" for n in nodes):
                    fail("нет активной ноды — выходить некуда")
                    info("Сначала добавьте ноду.")
                    pause()
                    continue
                env_set("COMPOSE_PROFILES", ",".join(filter(None, [profiles, "egress"])))
                env_set("TELEGRAM_PROXY_URL", "socks5://egress:1080")
                ok("включено; поднимаю контейнер")
                compose("up", "-d")
                info("Конфиг он возьмёт у головы сам и сменит, если нода перестанет работать.")
            pause()
        elif choice == "3":
            current = env.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
            value = ask("Telegram id через запятую", current)
            env_set("TELEGRAM_ALLOWED_CHAT_IDS", value)
            if not env.get("TELEGRAM_ADMIN_CHAT_ID"):
                env_set("TELEGRAM_ADMIN_CHAT_ID", value.split(",")[0].strip())
            ok("записано; перезапускаю бота")
            compose("up", "-d")
            pause()


def screen_app() -> None:
    clear()
    title("Параметры для сборки приложения")
    env = env_read()
    print("  Впишите в android/local.properties:\n")
    print(f"    headApiUrl={env.get('ADMIN_DOMAIN') and 'https://' + env['ADMIN_DOMAIN'] or '<ваш домен>'}")
    print(f"    headServiceToken={env.get('HEAD_SECRET_KEY', '<нет в .env>')}")
    print()
    info("Это HEAD_SECRET_KEY — он и так публичен, потому что лежит внутри APK.")
    info("ADMIN_API_TOKEN сюда класть нельзя: им выдаётся доступ и перезапускаются ноды.")
    pause()


def screen_maintenance() -> None:
    while True:
        clear()
        title("Обслуживание")
        print(f"  {BOLD}1{OFF} состояние контейнеров")
        print(f"  {BOLD}2{OFF} логи (head)")
        print(f"  {BOLD}3{OFF} логи (bot)")
        print(f"  {BOLD}4{OFF} перезапустить всё")
        print(f"  {BOLD}5{OFF} обновить из репозитория")
        print(f"  {BOLD}6{OFF} резервная копия базы")
        print(f"  {BOLD}7{OFF} сменить пароль администратора")
        print(f"  {BOLD}0{OFF} назад")
        choice = input("\n  > ").strip()

        if choice in ("0", "q", ""):
            return
        if choice == "1":
            print()
            compose("ps")
            pause()
        elif choice in ("2", "3"):
            service = "head" if choice == "2" else "bot"
            print(f"\n{DIM}Ctrl-C — выйти из просмотра{OFF}\n")
            try:
                compose("logs", "-f", "--tail", "50", service)
            except KeyboardInterrupt:
                pass
        elif choice == "4":
            print()
            compose("up", "-d")
            pause()
        elif choice == "5":
            print()
            if subprocess.call(["git", "pull"], cwd=REPO) == 0:
                compose("up", "-d", "--build")
            pause()
        elif choice == "6":
            env = env_read()
            user = env.get("POSTGRES_USER", "freeskyvpn")
            name = env.get("POSTGRES_DB", "freeskyvpn")
            target = Path.home() / f"freeskyvpn-{_stamp()}.sql.gz"
            print()
            with target.open("wb") as handle:
                dump = subprocess.Popen(
                    ["docker", "compose", "exec", "-T", "db", "pg_dump", "-U", user, name],
                    cwd=REPO, stdout=subprocess.PIPE,
                )
                gzip = subprocess.Popen(["gzip"], stdin=dump.stdout, stdout=handle)
                dump.stdout.close()
                gzip.communicate()
            if target.stat().st_size > 0:
                ok(f"сохранено: {target}")
                warn("Отдельно сохраните .env и secrets/ — без них дамп бесполезен для доступа к нодам.")
            else:
                fail("дамп пустой — база не ответила")
                target.unlink(missing_ok=True)
            pause()
        elif choice == "7":
            print()
            cli("create-admin", "admin")
            pause()


def _stamp() -> str:
    return time.strftime("%Y%m%d-%H%M")


# --- главный экран ---------------------------------------------------------


def main() -> int:
    if not shutil.which("docker"):
        fail("docker не найден — запускать на сервере, где стоит стек")
        return 1
    if not ENV_FILE.exists():
        fail(f"нет {ENV_FILE}")
        info("Сначала установка: sudo ./install.sh")
        return 1

    while True:
        clear()
        print(f"\n{BOLD}  FreeSkyVPN{OFF}")
        data = cli_json("status", "--json")
        if data is None:
            info("Стек не запущен? docker compose up -d")
        else:
            line = (
                f"  {data['nodes_active']} нод в работе · "
                f"{data['assignments_live']}/{data['capacity']} мест занято · "
                f"{data['users_online']} чел. со временем"
            )
            if data["nodes_isolated"]:
                print(f"{RED}{line} · {data['nodes_isolated']} изолировано{OFF}")
            elif not data["nodes_active"]:
                print(f"{YEL}{line} — без ноды конфиги не выдаются{OFF}")
            else:
                print(f"{DIM}{line}{OFF}")

        print(f"\n  {BOLD}1{OFF} Ноды")
        print(f"  {BOLD}2{OFF} Пользователи и доступ")
        print(f"  {BOLD}3{OFF} Telegram и бот")
        print(f"  {BOLD}4{OFF} Параметры для приложения")
        print(f"  {BOLD}5{OFF} Обслуживание")
        print(f"  {BOLD}6{OFF} Проверка системы")
        print(f"  {BOLD}0{OFF} Выход")

        choice = input("\n  > ").strip()
        if choice in ("0", "q"):
            return 0
        if choice == "1":
            screen_nodes()
        elif choice == "2":
            screen_users()
        elif choice == "3":
            screen_telegram()
        elif choice == "4":
            screen_app()
        elif choice == "5":
            screen_maintenance()
        elif choice == "6":
            env = env_read()
            print()
            subprocess.call(
                ["docker", "compose", "exec", "-T", "head", "python", "smoke_test.py", "--deep",
                 "--token", env.get("HEAD_SECRET_KEY", ""),
                 "--admin-token", env.get("ADMIN_API_TOKEN", "")],
                cwd=REPO,
            )
            pause()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print()
        raise SystemExit(130) from None
