from __future__ import annotations

import html
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse

from .collector import LiveSessionCollector
from .config import Settings, load_settings
from .donation_alerts import DonationAlertsApiError, DonationAlertsClient
from .obs_control import OBSControlError, OBSController
from .session_store import SessionStore
from .state_builder import build_ending_state
from .twitch_api import TwitchApiError, TwitchHelixClient


ALLOWED_STATIC_EXTENSIONS = {
    ".html",
    ".css",
    ".js",
    ".json",
    ".mp3",
    ".mp4",
    ".webm",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".txt",
}

LOGGER = logging.getLogger("ending.backend")


def _is_allowed_static_path(root_dir: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root_dir)
    except ValueError:
        return False

    forbidden_parts = {"backend", "data", "__pycache__"}
    if any(part in forbidden_parts or part.startswith(".") for part in candidate.relative_to(root_dir).parts):
        return False
    if candidate.suffix.lower() not in ALLOWED_STATIC_EXTENSIONS:
        return False
    return candidate.is_file()


def resolve_static_file(settings: Settings, relative_path: str) -> Path:
    requested = (relative_path or "ending.html").lstrip("/\\")
    candidate = (settings.static_dir / requested).resolve()
    if not _is_allowed_static_path(settings.static_dir.resolve(), candidate):
        raise HTTPException(status_code=404, detail="Not found")
    return candidate


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings(Path(__file__).resolve().parent.parent)
    store = SessionStore(settings.session_store_path)
    twitch = TwitchHelixClient(settings)
    donation_alerts = DonationAlertsClient(settings)
    obs = OBSController(settings)
    collector = LiveSessionCollector(settings, store, twitch)

    app.state.settings = settings
    app.state.store = store
    app.state.twitch = twitch
    app.state.donationalerts = donation_alerts
    app.state.obs = obs
    app.state.collector = collector

    await collector.start()
    try:
        yield
    finally:
        await collector.stop()
        store.close()


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(name)s: %(message)s")
    app = FastAPI(title="OBS Ending Scene Backend", lifespan=lifespan)

    @app.get("/", include_in_schema=False)
    async def root_redirect() -> RedirectResponse:
        return RedirectResponse(url="/ending.html", status_code=307)

    @app.get("/api/health")
    async def health(request: Request) -> dict[str, object]:
        settings: Settings = request.app.state.settings
        return {
            "ok": True,
            "twitch_app_configured": settings.has_twitch_app_credentials,
            "twitch_user_configured": settings.has_twitch_user_credentials,
            "donationalerts_app_configured": settings.has_donationalerts_app_credentials,
            "donationalerts_user_configured": settings.has_donationalerts_user_credentials,
        }

    @app.get("/api/ending-state")
    async def ending_state(request: Request, demo: bool = False) -> JSONResponse:
        settings: Settings = request.app.state.settings
        store: SessionStore = request.app.state.store
        twitch: TwitchHelixClient = request.app.state.twitch
        donation_alerts: DonationAlertsClient = request.app.state.donationalerts
        payload = await build_ending_state(
            settings=settings,
            store=store,
            twitch=twitch,
            donation_alerts=donation_alerts,
            demo_mode=demo,
        )
        return JSONResponse(payload)

    @app.post("/api/obs/stop-stream")
    async def stop_stream(request: Request) -> JSONResponse:
        obs: OBSController = request.app.state.obs
        try:
            obs.stop_stream()
        except OBSControlError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return JSONResponse({"ok": True})

    @app.get("/api/twitch/status")
    async def twitch_status(request: Request) -> dict[str, object]:
        settings: Settings = request.app.state.settings
        collector: LiveSessionCollector = request.app.state.collector
        twitch: TwitchHelixClient = request.app.state.twitch

        broadcaster = None
        token_scopes = None
        if settings.has_twitch_app_credentials:
            try:
                identity = await twitch.resolve_broadcaster()
                broadcaster = {
                    "user_id": identity.user_id,
                    "login": identity.login,
                    "display_name": identity.display_name,
                }
            except Exception as exc:  # noqa: BLE001
                broadcaster = {"error": str(exc)}

        if settings.has_twitch_user_credentials:
            try:
                token_scopes = list((await twitch.validate_user_token()).get("scopes") or [])
            except Exception as exc:  # noqa: BLE001
                token_scopes = [f"error: {exc}"]

        return {
            "ok": True,
            "collector": collector.status_payload(),
            "broadcaster": broadcaster,
            "required_scopes": list(settings.twitch_scopes),
            "token_scopes": token_scopes,
        }

    @app.get("/api/donationalerts/status")
    async def donationalerts_status(request: Request) -> dict[str, object]:
        settings: Settings = request.app.state.settings
        donation_alerts: DonationAlertsClient = request.app.state.donationalerts

        identity = None
        if settings.has_donationalerts_user_credentials:
            try:
                profile = await donation_alerts.get_user_profile()
                identity = {
                    "user_id": profile.user_id,
                    "code": profile.code,
                    "name": profile.name,
                }
            except Exception as exc:  # noqa: BLE001
                identity = {"error": str(exc)}

        return {
            "ok": True,
            "app_configured": settings.has_donationalerts_app_credentials,
            "user_configured": settings.has_donationalerts_user_credentials,
            "identity": identity,
        }

    @app.get("/api/twitch/auth-url")
    async def twitch_auth_url(request: Request) -> dict[str, str]:
        twitch: TwitchHelixClient = request.app.state.twitch
        try:
            url = twitch.build_auth_url()
        except TwitchApiError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"url": url}

    @app.get("/auth/twitch/login", include_in_schema=False)
    async def twitch_login(request: Request) -> RedirectResponse:
        twitch: TwitchHelixClient = request.app.state.twitch
        try:
            url = twitch.build_auth_url()
        except TwitchApiError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=url, status_code=307)

    @app.get("/auth/twitch/callback", include_in_schema=False)
    async def twitch_callback(request: Request, code: str | None = None, error: str | None = None) -> HTMLResponse:
        twitch: TwitchHelixClient = request.app.state.twitch

        if error:
            return HTMLResponse(
                f"<h1>Twitch auth error</h1><p>{html.escape(error)}</p>",
                status_code=400,
            )
        if not code:
            return HTMLResponse("<h1>Missing code</h1>", status_code=400)

        try:
            await twitch.exchange_code(code)
        except TwitchApiError as exc:
            LOGGER.warning("twitch oauth callback failed: %s", exc)
            return HTMLResponse(
                f"<h1>Не удалось сохранить Twitch token</h1><pre>{html.escape(str(exc))}</pre>",
                status_code=500,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("twitch oauth callback crashed")
            return HTMLResponse(
                f"<h1>Twitch callback crashed</h1><pre>{html.escape(str(exc))}</pre>",
                status_code=500,
            )

        return HTMLResponse(
            """
            <!doctype html>
            <html lang="ru">
            <head><meta charset="utf-8"><title>Twitch connected</title></head>
            <body style="background:#07070b;color:#fff;font-family:Segoe UI,Arial,sans-serif;padding:32px">
              <h1>Готово</h1>
              <p>Twitch авторизация сохранена. Можно закрыть эту вкладку и вернуться к OBS.</p>
            </body>
            </html>
            """.strip()
        )

    @app.get("/api/donationalerts/auth-url")
    async def donationalerts_auth_url(request: Request) -> dict[str, str]:
        donation_alerts: DonationAlertsClient = request.app.state.donationalerts
        try:
            url = donation_alerts.build_auth_url()
        except DonationAlertsApiError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"url": url}

    @app.get("/auth/donationalerts/login", include_in_schema=False)
    async def donationalerts_login(request: Request) -> RedirectResponse:
        donation_alerts: DonationAlertsClient = request.app.state.donationalerts
        try:
            url = donation_alerts.build_auth_url()
        except DonationAlertsApiError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return RedirectResponse(url=url, status_code=307)

    @app.get("/auth/donationalerts/callback", include_in_schema=False)
    async def donationalerts_callback(
        request: Request,
        code: str | None = None,
        error: str | None = None,
    ) -> HTMLResponse:
        donation_alerts: DonationAlertsClient = request.app.state.donationalerts

        if error:
            return HTMLResponse(
                f"<h1>DonationAlerts auth error</h1><p>{html.escape(error)}</p>",
                status_code=400,
            )
        if not code:
            return HTMLResponse("<h1>Missing code</h1>", status_code=400)

        try:
            await donation_alerts.exchange_code(code)
        except DonationAlertsApiError as exc:
            LOGGER.warning("donationalerts oauth callback failed: %s", exc)
            return HTMLResponse(
                f"<h1>Не удалось сохранить DonationAlerts token</h1><pre>{html.escape(str(exc))}</pre>",
                status_code=500,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("donationalerts oauth callback crashed")
            return HTMLResponse(
                f"<h1>DonationAlerts callback crashed</h1><pre>{html.escape(str(exc))}</pre>",
                status_code=500,
            )

        return HTMLResponse(
            """
            <!doctype html>
            <html lang="ru">
            <head><meta charset="utf-8"><title>DonationAlerts connected</title></head>
            <body style="background:#07070b;color:#fff;font-family:Segoe UI,Arial,sans-serif;padding:32px">
              <h1>Готово</h1>
              <p>DonationAlerts авторизация сохранена. Финальная сцена сможет подтягивать донаты за эфир.</p>
            </body>
            </html>
            """.strip()
        )

    @app.get("/{asset_path:path}", include_in_schema=False)
    async def static_files(request: Request, asset_path: str) -> FileResponse:
        settings: Settings = request.app.state.settings
        file_path = resolve_static_file(settings, asset_path)
        return FileResponse(
            file_path,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    return app
