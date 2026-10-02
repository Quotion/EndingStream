#!/usr/bin/env python3
"""
Собирает дистрибутив dist/Ending-<версия>.zip.

Берёт только файлы из белого списка ниже. Всё остальное (.env, data/, venv/, кэш клипов, музыка,
скриншоты) в архив попасть не может, даже если лежит рядом. Перед упаковкой сверяет файлы со значениями
из локального .env и отказывается собирать архив, если находит совпадение.

Запуск:  venv\\Scripts\\python.exe make_release.py
Сам скрипт в архив не входит: он нужен только автору.
"""

from __future__ import annotations

import hashlib
import sys
import time
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist"
ARCHIVE_ROOT = "Ending"

INCLUDE_FILES = [
    # документация и служебное
    "README.md",
    "VERSION",
    ".gitignore",
    ".env.example",
    "requirements.txt",
    "docs/TECHNICAL.md",
    "docs/images/scene-stats.png",
    "docs/images/scene-newcomers.png",
    # установка и запуск
    "install.bat",
    "configure.bat",
    "setup_ending.py",
    "start-ending.bat",
    "stop-ending.bat",
    "start-ending-hidden.vbs",
    "status-ending.vbs",
    "serve.py",
    "serve_hidden.pyw",
    "install-ending-autostart.ps1",
    "uninstall-ending-autostart.ps1",
    "start-obs-with-ending.vbs",
    "obs-start-ending.lua",
    # сцена
    "ending.html",
    "ending.css",
    "ending.js",
    "ending-status.html",
    "ending-status.url",
    "mock-ending-state.json",
    "mock-ending-state-sparse.json",
    # ресурсы: музыки здесь нет намеренно
    "assets/README.txt",
    "assets/clips/local-fallback-01.mp4",
    "assets/clips/local-fallback-02.mp4",
    "assets/clips/local-fallback-03.mp4",
]

# Каталог целиком, но только файлы с этими расширениями.
INCLUDE_TREES = {
    "backend": {".py"},
}

# Что не должно оказаться в архиве ни при каких условиях (проверка на случай ошибки в списках выше).
FORBIDDEN_NAMES = {".env", "session-store.sqlite3"}
FORBIDDEN_SUFFIXES = {".mp3", ".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".log", ".pid", ".pyc"}
FORBIDDEN_PARTS = {"venv", "data", "__pycache__", "clip-cache", "test-results"}

CRLF_SUFFIXES = {".bat", ".vbs", ".ps1"}


def collect_files() -> list[Path]:
    files: list[Path] = []
    missing: list[str] = []
    for rel in INCLUDE_FILES:
        path = ROOT / rel
        if path.is_file():
            files.append(path)
        else:
            missing.append(rel)
    for tree, suffixes in INCLUDE_TREES.items():
        base = ROOT / tree
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts:
                files.append(path)
    if missing:
        raise SystemExit("Нет файлов из белого списка: " + ", ".join(missing))
    return files


def check_forbidden(files: list[Path]) -> None:
    for path in files:
        rel = path.relative_to(ROOT)
        if (
            path.name in FORBIDDEN_NAMES
            or path.suffix.lower() in FORBIDDEN_SUFFIXES
            or FORBIDDEN_PARTS & set(rel.parts)
        ):
            raise SystemExit(f"Запрещённый файл в списке сборки: {rel}")


def load_private_values() -> set[str]:
    """Значения из .env, которых нет в шаблоне .env.example: токены, секреты, пароли, логин канала."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return set()
    example = (ROOT / ".env.example").read_text(encoding="utf-8-sig")
    values: set[str] = set()
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        value = line.split("=", 1)[1].strip().strip('"').strip("'")
        if len(value) >= 5 and value not in example:
            values.add(value)
    return values


def scan_for_private_values(files: list[Path], private_values: set[str]) -> None:
    if not private_values:
        print("  .env не найден, сверка с секретами пропущена.")
        return
    needles = [value.encode("utf-8") for value in private_values]
    for path in files:
        data = path.read_bytes()
        for needle in needles:
            if needle in data:
                # Само значение в сообщение не выводим.
                raise SystemExit(f"В {path.relative_to(ROOT)} найдено значение из .env. Сборка остановлена.")
    print(f"  Сверка с .env: {len(needles)} значений, совпадений нет.")


def archive_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() in CRLF_SUFFIXES:
        data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    return data


def main() -> None:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    print(f"Сборка Ending {version}")

    files = collect_files()
    check_forbidden(files)
    scan_for_private_values(files, load_private_values())

    DIST_DIR.mkdir(exist_ok=True)
    archive_path = DIST_DIR / f"Ending-{version}.zip"
    if archive_path.exists():
        archive_path.unlink()

    stamp = time.localtime()[:6]
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            rel = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{rel}", date_time=stamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16  # без этого на Linux/macOS файлы распакуются с правами rw-------
            archive.writestr(info, archive_bytes(path))

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    size_kb = archive_path.stat().st_size / 1024
    print(f"  Файлов в архиве: {len(files)}")
    print(f"  Готово: {archive_path}  ({size_kb:.0f} КБ)")
    print(f"  SHA-256: {digest}")


if __name__ == "__main__":
    sys.exit(main())
