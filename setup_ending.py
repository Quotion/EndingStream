#!/usr/bin/env python3
"""
Установщик Ending.

Запускается через install.bat (Windows) или `python3 setup_ending.py` (macOS/Linux).
Что делает:
  1. создаёт виртуальное окружение venv и ставит зависимости;
  2. готовит файл .env (существующий никогда не перезаписывает);
  3. по желанию спрашивает ключи Twitch / DonationAlerts / OBS;
  4. поднимает сервер на свободном порту и проверяет, что он отвечает.

Безопасен к повторному запуску. `--configure` пропускает установку и только спрашивает ключи.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import NoReturn


ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / "venv"
IS_WINDOWS = os.name == "nt"
VENV_PYTHON = VENV_DIR / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
MIN_PYTHON = (3, 10)
DEFAULT_PORT = 8080

TWITCH_CONSOLE_URL = "https://dev.twitch.tv/console/apps"
DONATIONALERTS_CONSOLE_URL = "https://www.donationalerts.com/application/clients"


def say(message: str = "") -> None:
    print(message, flush=True)


def fail(message: str) -> NoReturn:
    say("")
    say(f"ОШИБКА: {message}")
    raise SystemExit(1)


def read_version() -> str:
    try:
        return (ROOT / "VERSION").read_text(encoding="utf-8").strip() or "dev"
    except OSError:
        return "dev"


def ask(prompt: str, default: str = "") -> str:
    """input(), который не падает без терминала: тогда возвращает default."""
    try:
        answer = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        say("")
        return default
    return answer or default


def ask_secret(prompt: str) -> str:
    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        say("")
        return ""


def ask_yes_no(prompt: str, default: bool) -> bool:
    suffix = "[Д/н]" if default else "[д/Н]"
    answer = ask(f"{prompt} {suffix}: ").lower()
    if not answer:
        return default
    return answer[0] in {"д", "y", "1"}


# ───────────────────────────── Python и venv ─────────────────────────────


def check_python() -> None:
    if sys.version_info < MIN_PYTHON:
        found = ".".join(map(str, sys.version_info[:3]))
        need = ".".join(map(str, MIN_PYTHON))
        fail(f"нужен Python {need} или новее, а запущен {found}.")


def venv_is_healthy() -> bool:
    if not VENV_PYTHON.exists():
        return False
    try:
        result = subprocess.run(
            [str(VENV_PYTHON), "-c", "import sys; print(sys.version_info[:2])"],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def ensure_venv() -> None:
    if venv_is_healthy():
        say("Окружение venv уже есть, используем его.")
        return

    if VENV_DIR.exists():
        # Папку с проектом переносили или Python переустановили: venv хранит абсолютный путь к интерпретатору.
        say("Окружение venv не запускается (проект перенесли или Python переустановили). Создаю заново.")
        shutil.rmtree(VENV_DIR, ignore_errors=True)
        if VENV_DIR.exists():
            fail(f"не удалось удалить {VENV_DIR}. Закройте программы, которые его используют, и повторите.")

    say("Создаю окружение venv...")
    result = subprocess.run([sys.executable, "-m", "venv", str(VENV_DIR)])
    if result.returncode != 0 or not VENV_PYTHON.exists():
        hint = ""
        if not IS_WINDOWS:
            hint = " В Debian/Ubuntu поставьте пакет python3-venv."
        fail(f"не удалось создать виртуальное окружение.{hint}")


def install_requirements() -> None:
    say("Ставлю зависимости (нужен интернет, 1–2 минуты)...")
    say("")
    result = subprocess.run(
        [
            str(VENV_PYTHON),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "-r",
            str(ROOT / "requirements.txt"),
        ]
    )
    if result.returncode != 0:
        fail(
            "pip не смог поставить зависимости. Проверьте интернет (и VPN/прокси, если они у вас есть) "
            "и запустите install.bat ещё раз."
        )


# ───────────────────────────────── .env ─────────────────────────────────


def ensure_env_file() -> bool:
    """Создаёт .env из .env.example. Возвращает True, если файл создан сейчас."""
    if ENV_PATH.exists():
        say("Файл .env уже есть, не трогаю.")
        return False
    if not ENV_EXAMPLE_PATH.exists():
        fail("не найден .env.example. Распакуйте архив целиком.")
    shutil.copyfile(ENV_EXAMPLE_PATH, ENV_PATH)
    say("Создан файл .env из шаблона.")
    return True


def get_env_value(key: str) -> str:
    if not ENV_PATH.exists():
        return ""
    for raw_line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def set_env_value(key: str, value: str) -> None:
    """Меняет одну строку KEY=... в .env, сохраняя комментарии и порядок остальных."""
    value = re.sub(r"[\r\n]+", "", value).strip()
    text = ENV_PATH.read_text(encoding="utf-8-sig") if ENV_PATH.exists() else ""
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    replacement = f"{key}={value}"
    if pattern.search(text):
        text = pattern.sub(lambda _match: replacement, text, count=1)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + replacement + "\n"
    # Без BOM: backend читает .env как utf-8-sig, но другие программы могут не уметь.
    ENV_PATH.write_text(text, encoding="utf-8")


def normalize_channel_login(raw: str) -> str:
    login = raw.strip().lower()
    login = re.sub(r"^https?://(www\.)?twitch\.tv/", "", login)
    return login.strip("/@ ")


def prompt_value(key: str, label: str, *, secret: bool = False, normalize=None) -> None:
    current = get_env_value(key)
    hint = "уже задано, Enter — оставить" if current else "Enter — пропустить"
    prompt = f"  {label} ({hint}): "
    answer = ask_secret(prompt) if secret else ask(prompt)
    if not answer:
        return
    if normalize:
        answer = normalize(answer)
    if secret:
        say(f"    принято, символов: {len(answer)}")
    set_env_value(key, answer)


def configure_keys() -> None:
    say("")
    say("Подключение Twitch")
    say("------------------")
    say(f"  Нужно приложение на {TWITCH_CONSOLE_URL} (подробно: README.md, раздел «Подключение Twitch»).")
    say("  Redirect URL у приложения: http://localhost:8080/auth/twitch/callback")
    say("  Можно пропустить всё нажатием Enter: демо работает и без ключей.")
    say("")
    prompt_value("TWITCH_CHANNEL_LOGIN", "Логин вашего Twitch-канала", normalize=normalize_channel_login)
    prompt_value("TWITCH_CLIENT_ID", "Client ID")
    prompt_value("TWITCH_CLIENT_SECRET", "Client Secret (ввод не виден; можно вставить правой кнопкой)", secret=True)

    say("")
    say("OBS (автоостановка стрима в конце сцены)")
    say("------------------------------------------")
    say("  В OBS: Инструменты (Tools) → WebSocket Server Settings → включить сервер и аутентификацию, задать пароль.")
    say("")
    prompt_value("OBS_WS_PASSWORD", "Пароль WebSocket-сервера OBS", secret=True)

    say("")
    if ask_yes_no("Настроить DonationAlerts (блок донатов в титрах)?", default=False):
        say(f"  Приложение создаётся на {DONATIONALERTS_CONSOLE_URL}")
        say("  Redirect URL: http://localhost:8080/auth/donationalerts/callback")
        say("")
        prompt_value("DONATIONALERTS_CLIENT_ID", "Client ID")
        prompt_value("DONATIONALERTS_CLIENT_SECRET", "Client Secret", secret=True)

    say("")
    say("Ключи сохранены в .env. Остался один шаг: запустить Ending и открыть")
    say(f"  http://localhost:{DEFAULT_PORT}/auth/twitch/login  (и /auth/donationalerts/login),")
    say("чтобы разрешить доступ к каналу. Это делается один раз.")


# ───────────────────────────── проверка запуска ─────────────────────────────


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def http_get(url: str, timeout: float = 3.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.getcode(), response.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0, ""


def kill_process_tree(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    if IS_WINDOWS:
        # venv\Scripts\python.exe на Windows запускает настоящий интерпретатор дочерним процессом.
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True,
        )
    else:
        proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def smoke_test() -> bool:
    """Поднимает сервер на свободном порту без ключей и временной базой, проверяет основные адреса."""
    port = find_free_port()
    scratch = Path(tempfile.mkdtemp(prefix="ending-smoke-"))
    log_path = scratch / "server.log"

    # Пустые значения побеждают .env (load_dotenv_file использует setdefault): проверка не ходит в Twitch
    # и не пишет в настоящую базу эфиров.
    env = dict(os.environ)
    for key in (
        "TWITCH_CLIENT_ID",
        "TWITCH_CLIENT_SECRET",
        "TWITCH_USER_ACCESS_TOKEN",
        "TWITCH_REFRESH_TOKEN",
        "DONATIONALERTS_ACCESS_TOKEN",
        "DONATIONALERTS_REFRESH_TOKEN",
    ):
        env[key] = ""
    env["SESSION_STORE_PATH"] = str(scratch / "smoke.sqlite3")

    ok = False
    problems: list[str] = []
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [
                str(VENV_PYTHON),
                "-m",
                "uvicorn",
                "backend.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            base = f"http://127.0.0.1:{port}"
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if proc.poll() is not None:
                    problems.append("сервер завершился сразу после запуска")
                    break
                status, body = http_get(f"{base}/api/health")
                if status == 200 and '"ok":true' in body.replace(" ", ""):
                    break
                time.sleep(0.5)
            else:
                problems.append("сервер не ответил за 40 секунд")

            if not problems:
                status, body = http_get(f"{base}/ending.html")
                if status != 200 or "<html" not in body.lower():
                    problems.append(f"/ending.html вернул HTTP {status}")
                status, body = http_get(f"{base}/ending.js")
                if status != 200:
                    problems.append(f"/ending.js вернул HTTP {status}")
                # Секреты и данные эфиров не должны отдаваться по HTTP.
                for forbidden in ("/.env", "/data/session-store.sqlite3", "/backend/config.py"):
                    status, _ = http_get(f"{base}{forbidden}")
                    if status == 200:
                        problems.append(f"{forbidden} доступен по HTTP, это нельзя")
            ok = not problems
        finally:
            kill_process_tree(proc)

    if not ok:
        for problem in problems:
            say(f"  - {problem}")
        try:
            tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-15:]
        except OSError:
            tail = []
        if tail:
            say("  Последние строки журнала сервера:")
            for line in tail:
                say(f"    {line}")
    shutil.rmtree(scratch, ignore_errors=True)
    return ok


def start_demo() -> None:
    """Запускает фоновый сервер на штатном порту и открывает демо в браузере (только Windows)."""
    url_base = f"http://127.0.0.1:{DEFAULT_PORT}"
    launcher = ROOT / "start-ending-hidden.vbs"
    if not IS_WINDOWS or not launcher.exists():
        say(f"Запустите сервер командой:  {VENV_PYTHON} serve.py {DEFAULT_PORT}")
        say(f"и откройте {url_base}/ending.html?demo=1")
        return

    say("Запускаю Ending в фоне...")
    os.startfile(str(launcher))  # type: ignore[attr-defined]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status, _ = http_get(f"{url_base}/api/health", timeout=1.5)
        if status == 200:
            webbrowser.open(f"{url_base}/ending.html?demo=1")
            say(f"Демо открыто: {url_base}/ending.html?demo=1")
            say("Остановить фоновый сервер: stop-ending.bat")
            return
        time.sleep(0.5)
    say(f"Сервер не ответил на порту {DEFAULT_PORT}. Скорее всего, порт занят другой программой.")
    say("Подробности: data\\ending-backend.log, а также раздел «Если что-то не работает» в README.md.")


# ──────────────────────────────────── main ────────────────────────────────────


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Установщик Ending")
    parser.add_argument(
        "--no-prompt",
        action="store_true",
        help="ничего не спрашивать (для автоматической установки): без ввода ключей и без запуска демо",
    )
    parser.add_argument(
        "--configure",
        action="store_true",
        help="только спросить ключи и записать их в .env, без установки",
    )
    return parser.parse_args()


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    args = parse_args()
    os.chdir(ROOT)

    if args.configure:
        ensure_env_file()
        configure_keys()
        return

    say(f"Ending {read_version()}: установка")
    say("=" * 40)
    check_python()

    say("")
    say("[1/4] Python и окружение")
    ensure_venv()

    say("")
    say("[2/4] Зависимости")
    install_requirements()

    say("")
    say("[3/4] Настройки")
    ensure_env_file()

    say("")
    say("[4/4] Проверка запуска")
    if not smoke_test():
        fail("сервер не прошёл проверку. Текст выше подскажет причину; если не ясно, приложите его к обращению за помощью.")
    say("  Сервер запускается и отдаёт сцену: всё в порядке.")

    say("")
    say("Установка завершена.")

    if args.no_prompt:
        return

    say("")
    if ask_yes_no("Ввести ключи Twitch прямо сейчас? (можно позже: configure.bat)", default=False):
        configure_keys()

    say("")
    if ask_yes_no("Запустить демо сейчас? (ключи не нужны)", default=True):
        start_demo()

    say("")
    say("Дальше:")
    say("  - start-ending.bat            запустить Ending в окне консоли")
    say("  - start-ending-hidden.vbs     запустить в фоне, без окна (stop-ending.bat останавливает)")
    say("  - README.md                   подключение Twitch, OBS, музыка")


if __name__ == "__main__":
    main()
