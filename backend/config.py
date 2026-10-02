from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ENV_PREFIX_KEYS = {
    "TWITCH_CLIENT_ID",
    "TWITCH_CLIENT_SECRET",
    "TWITCH_CHANNEL_LOGIN",
    "TWITCH_REDIRECT_URI",
    "TWITCH_USER_ACCESS_TOKEN",
    "TWITCH_REFRESH_TOKEN",
    "TWITCH_AUTH_LOGIN",
    "TWITCH_AUTH_USER_ID",
    "DONATIONALERTS_CLIENT_ID",
    "DONATIONALERTS_CLIENT_SECRET",
    "DONATIONALERTS_REDIRECT_URI",
    "DONATIONALERTS_ACCESS_TOKEN",
    "DONATIONALERTS_REFRESH_TOKEN",
    "DONATIONALERTS_AUTH_USER_ID",
    "DONATIONALERTS_AUTH_CODE",
    "DONATIONALERTS_AUTH_NAME",
    "OBS_WS_URL",
    "OBS_WS_PASSWORD",
    "SESSION_STORE_PATH",
    "LOCAL_CLIPS_DIR",
    "CLIP_CACHE_DIR",
    "COLLECTOR_STREAM_POLL_SECONDS",
    "COLLECTOR_CHATTERS_POLL_SECONDS",
    "CLIPS_FALLBACK_DAYS",
    "CLIPS_LIMIT",
    "CLIPS_PREFETCH_SECONDS",
    "ENDING_BOT_LOGINS",
}


def load_dotenv_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    # utf-8-sig: Блокнот Windows сохраняет UTF-8 с BOM, и без этого первый ключ файла молча терялся бы.
    for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in ENV_PREFIX_KEYS:
            continue
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def write_env_values(env_path: Path, updates: dict[str, str]) -> None:
    current: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()

    for key, value in updates.items():
        if value is None:
            continue
        current[key] = value

    ordered_keys = sorted(current.keys())
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text(
        "\n".join(f"{key}={current[key]}" for key in ordered_keys) + "\n",
        encoding="utf-8",
    )


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    root_dir: Path
    env_path: Path
    static_dir: Path
    data_dir: Path
    local_clips_dir: Path
    clip_cache_dir: Path
    mock_state_path: Path
    session_store_path: Path
    twitch_client_id: str
    twitch_client_secret: str
    twitch_channel_login: str
    twitch_redirect_uri: str
    twitch_user_access_token: str
    twitch_refresh_token: str
    twitch_auth_login: str
    twitch_auth_user_id: str
    donationalerts_client_id: str
    donationalerts_client_secret: str
    donationalerts_redirect_uri: str
    donationalerts_access_token: str
    donationalerts_refresh_token: str
    donationalerts_auth_user_id: str
    donationalerts_auth_code: str
    donationalerts_auth_name: str
    obs_ws_url: str
    obs_ws_password: str
    collector_stream_poll_seconds: int
    collector_chatters_poll_seconds: int
    clips_fallback_days: int
    clips_limit: int
    clips_prefetch_seconds: int

    @property
    def twitch_scopes(self) -> tuple[str, ...]:
        return (
            "chat:read",
            "moderator:read:chatters",
            "moderator:read:followers",
            "channel:read:subscriptions",
            "bits:read",
            "channel:read:redemptions",
        )

    @property
    def donationalerts_scopes(self) -> tuple[str, ...]:
        return (
            "oauth-user-show",
            "oauth-donation-index",
        )

    @property
    def has_twitch_app_credentials(self) -> bool:
        return bool(self.twitch_client_id and self.twitch_client_secret and self.twitch_channel_login)

    @property
    def has_twitch_user_credentials(self) -> bool:
        return bool(self.twitch_user_access_token and self.twitch_refresh_token)

    @property
    def has_donationalerts_app_credentials(self) -> bool:
        return bool(self.donationalerts_client_id and self.donationalerts_client_secret)

    @property
    def has_donationalerts_user_credentials(self) -> bool:
        return bool(self.donationalerts_access_token and self.donationalerts_refresh_token)

    @property
    def has_obs_config(self) -> bool:
        return bool(self.obs_ws_url)


def load_settings(root_dir: Path) -> Settings:
    root_dir = root_dir.resolve()
    env_path = root_dir / ".env"
    load_dotenv_file(env_path)

    data_dir = root_dir / "data"
    session_store_path = Path(os.getenv("SESSION_STORE_PATH") or data_dir / "session-store.sqlite3")
    if not session_store_path.is_absolute():
        session_store_path = (root_dir / session_store_path).resolve()

    local_clips_dir = Path(os.getenv("LOCAL_CLIPS_DIR") or root_dir / "assets" / "clips")
    if not local_clips_dir.is_absolute():
        local_clips_dir = (root_dir / local_clips_dir).resolve()

    clip_cache_dir = Path(os.getenv("CLIP_CACHE_DIR") or root_dir / "assets" / "clip-cache")
    if not clip_cache_dir.is_absolute():
        clip_cache_dir = (root_dir / clip_cache_dir).resolve()

    settings = Settings(
        root_dir=root_dir,
        env_path=env_path,
        static_dir=root_dir,
        data_dir=data_dir,
        local_clips_dir=local_clips_dir,
        clip_cache_dir=clip_cache_dir,
        mock_state_path=root_dir / "mock-ending-state.json",
        session_store_path=session_store_path,
        twitch_client_id=os.getenv("TWITCH_CLIENT_ID", "").strip(),
        twitch_client_secret=os.getenv("TWITCH_CLIENT_SECRET", "").strip(),
        twitch_channel_login=os.getenv("TWITCH_CHANNEL_LOGIN", "").strip().lower(),
        twitch_redirect_uri=os.getenv("TWITCH_REDIRECT_URI", "http://localhost:8080/auth/twitch/callback").strip(),
        twitch_user_access_token=os.getenv("TWITCH_USER_ACCESS_TOKEN", "").strip(),
        twitch_refresh_token=os.getenv("TWITCH_REFRESH_TOKEN", "").strip(),
        twitch_auth_login=os.getenv("TWITCH_AUTH_LOGIN", "").strip().lower(),
        twitch_auth_user_id=os.getenv("TWITCH_AUTH_USER_ID", "").strip(),
        donationalerts_client_id=os.getenv("DONATIONALERTS_CLIENT_ID", "").strip(),
        donationalerts_client_secret=os.getenv("DONATIONALERTS_CLIENT_SECRET", "").strip(),
        donationalerts_redirect_uri=os.getenv(
            "DONATIONALERTS_REDIRECT_URI",
            "http://localhost:8080/auth/donationalerts/callback",
        ).strip(),
        donationalerts_access_token=os.getenv("DONATIONALERTS_ACCESS_TOKEN", "").strip(),
        donationalerts_refresh_token=os.getenv("DONATIONALERTS_REFRESH_TOKEN", "").strip(),
        donationalerts_auth_user_id=os.getenv("DONATIONALERTS_AUTH_USER_ID", "").strip(),
        donationalerts_auth_code=os.getenv("DONATIONALERTS_AUTH_CODE", "").strip(),
        donationalerts_auth_name=os.getenv("DONATIONALERTS_AUTH_NAME", "").strip(),
        obs_ws_url=os.getenv("OBS_WS_URL", "ws://127.0.0.1:4455").strip(),
        obs_ws_password=os.getenv("OBS_WS_PASSWORD", "").strip(),
        collector_stream_poll_seconds=_env_int("COLLECTOR_STREAM_POLL_SECONDS", 45),
        collector_chatters_poll_seconds=_env_int("COLLECTOR_CHATTERS_POLL_SECONDS", 60),
        clips_fallback_days=_env_int("CLIPS_FALLBACK_DAYS", 30),
        clips_limit=_env_int("CLIPS_LIMIT", 12),
        clips_prefetch_seconds=_env_int("CLIPS_PREFETCH_SECONDS", 180),
    )
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.session_store_path.parent.mkdir(parents=True, exist_ok=True)
    settings.local_clips_dir.mkdir(parents=True, exist_ok=True)
    settings.clip_cache_dir.mkdir(parents=True, exist_ok=True)
    return settings
