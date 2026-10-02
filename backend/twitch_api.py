from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Settings, write_env_values


class TwitchApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class BroadcasterIdentity:
    user_id: str
    login: str
    display_name: str


class TwitchHelixClient:
    AUTH_BASE = "https://id.twitch.tv/oauth2"
    API_BASE = "https://api.twitch.tv/helix"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._app_token: str = ""
        self._app_token_expires_at: float = 0.0
        self._broadcaster_identity: BroadcasterIdentity | None = None

    async def _request_json(
        self,
        *,
        url: str,
        method: str = "GET",
        query: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 20,
    ) -> Any:
        return await asyncio.to_thread(
            self._request_json_sync,
            url,
            method,
            query,
            data,
            json_body,
            headers,
            timeout,
        )

    def _request_json_sync(
        self,
        url: str,
        method: str,
        query: dict[str, Any] | None,
        data: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        headers: dict[str, str] | None,
        timeout: int,
    ) -> Any:
        if query:
            encoded = urllib.parse.urlencode(query, doseq=True)
            glue = "&" if "?" in url else "?"
            url = f"{url}{glue}{encoded}"

        body: bytes | None = None
        request_headers = {"User-Agent": "ending-scene-backend/1.0"}
        if headers:
            request_headers.update(headers)

        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        elif data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        request = urllib.request.Request(url=url, data=body, headers=request_headers, method=method.upper())

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="ignore")
            message = payload or exc.reason or "Twitch HTTP error"
            raise TwitchApiError(message, status_code=exc.code) from exc
        except urllib.error.URLError as exc:
            raise TwitchApiError(str(exc.reason)) from exc

        if not raw:
            return None

        decoded = raw.decode("utf-8", errors="replace")
        try:
            return json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise TwitchApiError(f"Не удалось разобрать ответ Twitch: {decoded[:500]}") from exc

    def build_auth_url(self, state: str | None = None) -> str:
        if not self.settings.has_twitch_app_credentials:
            raise TwitchApiError("В .env не настроены TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET / TWITCH_CHANNEL_LOGIN")

        query = urllib.parse.urlencode(
            {
                "client_id": self.settings.twitch_client_id,
                "redirect_uri": self.settings.twitch_redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.settings.twitch_scopes),
                "state": state or uuid.uuid4().hex,
            }
        )
        return f"{self.AUTH_BASE}/authorize?{query}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        payload = await self._request_json(
            url=f"{self.AUTH_BASE}/token",
            method="POST",
            data={
                "client_id": self.settings.twitch_client_id,
                "client_secret": self.settings.twitch_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": self.settings.twitch_redirect_uri,
            },
        )
        if not payload or "access_token" not in payload:
            raise TwitchApiError("Twitch не вернул access token")

        access_token = str(payload["access_token"])
        refresh_token = str(payload.get("refresh_token") or "")
        self.settings.twitch_user_access_token = access_token
        self.settings.twitch_refresh_token = refresh_token

        identity = await self.validate_user_token(access_token)
        self.settings.twitch_auth_login = identity["login"]
        self.settings.twitch_auth_user_id = identity["user_id"]
        write_env_values(
            self.settings.env_path,
            {
                "TWITCH_USER_ACCESS_TOKEN": access_token,
                "TWITCH_REFRESH_TOKEN": refresh_token,
                "TWITCH_AUTH_LOGIN": identity["login"],
                "TWITCH_AUTH_USER_ID": identity["user_id"],
            },
        )
        return payload

    async def validate_user_token(self, token: str | None = None) -> dict[str, Any]:
        access_token = token or self.settings.twitch_user_access_token
        if not access_token:
            raise TwitchApiError("Нет TWITCH_USER_ACCESS_TOKEN")

        payload = await self._request_json(
            url=f"{self.AUTH_BASE}/validate",
            method="GET",
            headers={"Authorization": f"OAuth {access_token}"},
        )
        if not payload or "user_id" not in payload:
            raise TwitchApiError("Не удалось провалидировать Twitch token")
        return {
            "user_id": str(payload["user_id"]),
            "login": str(payload["login"]).lower(),
            "scopes": list(payload.get("scopes") or []),
        }

    async def refresh_user_token(self) -> str:
        refresh_token = self.settings.twitch_refresh_token
        if not refresh_token:
            raise TwitchApiError("Нет TWITCH_REFRESH_TOKEN")

        payload = await self._request_json(
            url=f"{self.AUTH_BASE}/token",
            method="POST",
            data={
                "client_id": self.settings.twitch_client_id,
                "client_secret": self.settings.twitch_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        access_token = str(payload.get("access_token") or "")
        next_refresh = str(payload.get("refresh_token") or refresh_token)
        if not access_token:
            raise TwitchApiError("Не удалось обновить Twitch user token")

        identity = await self.validate_user_token(access_token)
        self.settings.twitch_user_access_token = access_token
        self.settings.twitch_refresh_token = next_refresh
        self.settings.twitch_auth_login = identity["login"]
        self.settings.twitch_auth_user_id = identity["user_id"]
        write_env_values(
            self.settings.env_path,
            {
                "TWITCH_USER_ACCESS_TOKEN": access_token,
                "TWITCH_REFRESH_TOKEN": next_refresh,
                "TWITCH_AUTH_LOGIN": identity["login"],
                "TWITCH_AUTH_USER_ID": identity["user_id"],
            },
        )
        return access_token

    async def get_app_token(self) -> str:
        if self._app_token and time.time() < self._app_token_expires_at - 30:
            return self._app_token

        payload = await self._request_json(
            url=f"{self.AUTH_BASE}/token",
            method="POST",
            data={
                "client_id": self.settings.twitch_client_id,
                "client_secret": self.settings.twitch_client_secret,
                "grant_type": "client_credentials",
            },
        )
        access_token = str(payload.get("access_token") or "")
        if not access_token:
            raise TwitchApiError("Не удалось получить Twitch app token")
        self._app_token = access_token
        self._app_token_expires_at = time.time() + int(payload.get("expires_in") or 0)
        return access_token

    async def _helix_request(
        self,
        path: str,
        *,
        method: str = "GET",
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        use_user_token: bool = False,
        retry_on_401: bool = True,
    ) -> Any:
        if not self.settings.twitch_client_id:
            raise TwitchApiError("Не задан TWITCH_CLIENT_ID")

        token = self.settings.twitch_user_access_token if use_user_token else await self.get_app_token()
        headers = {
            "Client-Id": self.settings.twitch_client_id,
            "Authorization": f"Bearer {token}",
        }

        try:
            return await self._request_json(
                url=f"{self.API_BASE}{path}",
                method=method,
                query=query,
                json_body=json_body,
                headers=headers,
            )
        except TwitchApiError as exc:
            if use_user_token and retry_on_401 and exc.status_code == 401:
                await self.refresh_user_token()
                return await self._helix_request(
                    path,
                    method=method,
                    query=query,
                    json_body=json_body,
                    use_user_token=True,
                    retry_on_401=False,
                )
            raise

    async def resolve_broadcaster(self) -> BroadcasterIdentity:
        if self._broadcaster_identity is not None:
            return self._broadcaster_identity
        if not self.settings.twitch_channel_login:
            raise TwitchApiError("Не задан TWITCH_CHANNEL_LOGIN")

        payload = await self._helix_request(
            "/users",
            query={"login": self.settings.twitch_channel_login},
            use_user_token=False,
        )
        users = payload.get("data") or []
        if not users:
            raise TwitchApiError("Не удалось найти Twitch channel по TWITCH_CHANNEL_LOGIN")

        user = users[0]
        self._broadcaster_identity = BroadcasterIdentity(
            user_id=str(user["id"]),
            login=str(user["login"]).lower(),
            display_name=str(user["display_name"]),
        )
        return self._broadcaster_identity

    async def get_live_stream(self) -> dict[str, Any] | None:
        broadcaster = await self.resolve_broadcaster()
        payload = await self._helix_request(
            "/streams",
            query={"user_id": broadcaster.user_id},
            use_user_token=False,
        )
        streams = payload.get("data") or []
        return streams[0] if streams else None

    async def get_latest_archive(self) -> dict[str, Any] | None:
        broadcaster = await self.resolve_broadcaster()
        payload = await self._helix_request(
            "/videos",
            query={
                "user_id": broadcaster.user_id,
                "type": "archive",
                "first": 1,
            },
            use_user_token=False,
        )
        videos = payload.get("data") or []
        return videos[0] if videos else None

    async def get_clips_for_window(
        self,
        *,
        started_at: datetime,
        ended_at: datetime | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        broadcaster = await self.resolve_broadcaster()
        items: list[dict[str, Any]] = []
        cursor: str | None = None

        while len(items) < limit:
            query = {
                "broadcaster_id": broadcaster.user_id,
                "started_at": started_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "first": min(100, limit - len(items)),
            }
            if ended_at is not None:
                query["ended_at"] = ended_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            if cursor:
                query["after"] = cursor

            payload = await self._helix_request("/clips", query=query, use_user_token=False)
            batch = payload.get("data") or []
            if not batch:
                break
            items.extend(batch)
            cursor = ((payload.get("pagination") or {}).get("cursor")) or None
            if not cursor:
                break

        return items[:limit]

    async def get_fallback_clips(self, *, limit: int, days: int) -> list[dict[str, Any]]:
        started_at = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        return await self.get_clips_for_window(started_at=started_at, ended_at=datetime.now(timezone.utc), limit=limit)

    async def get_chatters(self) -> list[str]:
        broadcaster = await self.resolve_broadcaster()
        moderator_id = self.settings.twitch_auth_user_id or broadcaster.user_id
        names: list[str] = []
        seen: set[str] = set()
        cursor: str | None = None
        seen_cursors: set[str] = set()

        while True:
            query = {
                "broadcaster_id": broadcaster.user_id,
                "moderator_id": moderator_id,
                "first": 1000,
            }
            if cursor:
                query["after"] = cursor

            payload = await self._helix_request(
                "/chat/chatters",
                query=query,
                use_user_token=True,
            )
            data = payload.get("data") or []
            for row in data:
                login = str(row.get("user_login") or "").strip().lower()
                if login and login not in seen:
                    seen.add(login)
                    names.append(login)

            cursor = str((payload.get("pagination") or {}).get("cursor") or "")
            if not cursor or cursor in seen_cursors:
                break
            seen_cursors.add(cursor)

        return names

    async def create_eventsub_subscription(
        self,
        *,
        subscription_type: str,
        version: str,
        condition: dict[str, str],
        session_id: str,
    ) -> dict[str, Any]:
        payload = await self._helix_request(
            "/eventsub/subscriptions",
            method="POST",
            json_body={
                "type": subscription_type,
                "version": version,
                "condition": condition,
                "transport": {
                    "method": "websocket",
                    "session_id": session_id,
                },
            },
            use_user_token=True,
        )
        data = payload.get("data") or []
        return data[0] if data else {}
