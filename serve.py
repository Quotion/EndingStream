#!/usr/bin/env python3
"""
Локальный launcher для backend финальной сцены.

Поднимает same-origin FastAPI-приложение:
- отдача ending.html / ending.css / ending.js / assets
- GET /api/ending-state
- POST /api/obs/stop-stream
- Twitch auth endpoints
- live collector для session snapshot
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from backend.app import create_app
from backend.config import load_settings
from backend.donation_alerts import DonationAlertsApiError, DonationAlertsClient
from backend.twitch_api import TwitchApiError, TwitchHelixClient


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OBS ending scene backend")
    parser.add_argument("port", nargs="?", type=int, default=8080, help="Локальный порт backend-а")
    parser.add_argument("--host", default="127.0.0.1", help="Хост для привязки сервера")
    parser.add_argument(
        "--auth-url",
        action="store_true",
        help="Не запускать сервер, а вывести Twitch OAuth URL",
    )
    parser.add_argument(
        "--donationalerts-auth-url",
        action="store_true",
        help="Не запускать сервер, а вывести DonationAlerts OAuth URL",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root_dir = Path(__file__).resolve().parent
    settings = load_settings(root_dir)

    if args.auth_url:
        twitch = TwitchHelixClient(settings)
        try:
            print(twitch.build_auth_url())
        except TwitchApiError as exc:
            raise SystemExit(str(exc)) from exc
        return

    if args.donationalerts_auth_url:
        donation_alerts = DonationAlertsClient(settings)
        try:
            print(donation_alerts.build_auth_url())
        except DonationAlertsApiError as exc:
            raise SystemExit(str(exc)) from exc
        return

    print(f"Serving http://{args.host}:{args.port}/")
    print(f"  Scene:  http://{args.host}:{args.port}/ending.html")
    print(f"  Demo:   http://{args.host}:{args.port}/ending.html?demo=1")
    print(f"  Twitch: http://{args.host}:{args.port}/auth/twitch/login")
    print(f"  DA:     http://{args.host}:{args.port}/auth/donationalerts/login")

    uvicorn.run(
        create_app(),
        host=args.host,
        port=args.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
