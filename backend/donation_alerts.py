from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import Settings, write_env_values


class DonationAlertsApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class DonationAlertsIdentity:
    user_id: str
    code: str
    name: str


def _parse_created_at(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class DonationAlertsClient:
    AUTH_BASE = "https://www.donationalerts.com/oauth"
    API_BASE = "https://www.donationalerts.com/api/v1"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._identity: DonationAlertsIdentity | None = None

    async def _request_json(
        self,
        *,
        url: str,
        method: str = "GET",
        query: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        timeout: int = 20,
    ) -> Any:
        return await asyncio.to_thread(
            self._request_json_sync,
            url,
            method,
            query,
            data,
            headers,
            timeout,
        )

    def _request_json_sync(
        self,
        url: str,
        method: str,
        query: dict[str, Any] | None,
        data: dict[str, Any] | None,
        headers: dict[str, str] | None,
        timeout: int,
    ) -> Any:
        if query:
            encoded = urllib.parse.urlencode(query, doseq=True)
            glue = "&" if "?" in url else "?"
            url = f"{url}{glue}{encoded}"

        request_headers = {
            "User-Agent": "ending-scene-backend/1.0",
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)

        body: bytes | None = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

        request = urllib.request.Request(url=url, data=body, headers=request_headers, method=method.upper())

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            payload = exc.read().decode("utf-8", errors="ignore")
            message = payload or exc.reason or "DonationAlerts HTTP error"
            raise DonationAlertsApiError(message, status_code=exc.code) from exc
        except urllib.error.URLError as exc:
            raise DonationAlertsApiError(str(exc.reason)) from exc

        if not raw:
            return None
        return json.loads(raw.decode("utf-8"))

    def build_auth_url(self, state: str | None = None) -> str:
        if not self.settings.has_donationalerts_app_credentials:
            raise DonationAlertsApiError(
                "В .env не настроены DONATIONALERTS_CLIENT_ID / DONATIONALERTS_CLIENT_SECRET"
            )

        query = urllib.parse.urlencode(
            {
                "client_id": self.settings.donationalerts_client_id,
                "redirect_uri": self.settings.donationalerts_redirect_uri,
                "response_type": "code",
                "scope": " ".join(self.settings.donationalerts_scopes),
                "state": state or uuid.uuid4().hex,
            }
        )
        return f"{self.AUTH_BASE}/authorize?{query}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        payload = await self._request_json(
            url=f"{self.AUTH_BASE}/token",
            method="POST",
            data={
                "client_id": self.settings.donationalerts_client_id,
                "client_secret": self.settings.donationalerts_client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.donationalerts_redirect_uri,
                "scope": " ".join(self.settings.donationalerts_scopes),
            },
        )
        access_token = str(payload.get("access_token") or "")
        refresh_token = str(payload.get("refresh_token") or "")
        if not access_token:
            raise DonationAlertsApiError("DonationAlerts не вернул access token")

        self.settings.donationalerts_access_token = access_token
        self.settings.donationalerts_refresh_token = refresh_token

        identity = await self.get_user_profile(access_token)
        self._identity = identity
        self.settings.donationalerts_auth_user_id = identity.user_id
        self.settings.donationalerts_auth_code = identity.code
        self.settings.donationalerts_auth_name = identity.name

        write_env_values(
            self.settings.env_path,
            {
                "DONATIONALERTS_ACCESS_TOKEN": access_token,
                "DONATIONALERTS_REFRESH_TOKEN": refresh_token,
                "DONATIONALERTS_AUTH_USER_ID": identity.user_id,
                "DONATIONALERTS_AUTH_CODE": identity.code,
                "DONATIONALERTS_AUTH_NAME": identity.name,
            },
        )
        return payload

    async def refresh_user_token(self) -> str:
        refresh_token = self.settings.donationalerts_refresh_token
        if not refresh_token:
            raise DonationAlertsApiError("Нет DONATIONALERTS_REFRESH_TOKEN")

        payload = await self._request_json(
            url=f"{self.AUTH_BASE}/token",
            method="POST",
            data={
                "client_id": self.settings.donationalerts_client_id,
                "client_secret": self.settings.donationalerts_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "redirect_uri": self.settings.donationalerts_redirect_uri,
                "scope": " ".join(self.settings.donationalerts_scopes),
            },
        )
        access_token = str(payload.get("access_token") or "")
        next_refresh = str(payload.get("refresh_token") or refresh_token)
        if not access_token:
            raise DonationAlertsApiError("Не удалось обновить DonationAlerts token")

        self.settings.donationalerts_access_token = access_token
        self.settings.donationalerts_refresh_token = next_refresh

        identity = await self.get_user_profile(access_token)
        self._identity = identity
        self.settings.donationalerts_auth_user_id = identity.user_id
        self.settings.donationalerts_auth_code = identity.code
        self.settings.donationalerts_auth_name = identity.name

        write_env_values(
            self.settings.env_path,
            {
                "DONATIONALERTS_ACCESS_TOKEN": access_token,
                "DONATIONALERTS_REFRESH_TOKEN": next_refresh,
                "DONATIONALERTS_AUTH_USER_ID": identity.user_id,
                "DONATIONALERTS_AUTH_CODE": identity.code,
                "DONATIONALERTS_AUTH_NAME": identity.name,
            },
        )
        return access_token

    async def _api_request(
        self,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        retry_on_401: bool = True,
    ) -> Any:
        access_token = self.settings.donationalerts_access_token
        if not access_token:
            raise DonationAlertsApiError("Нет DONATIONALERTS_ACCESS_TOKEN")

        try:
            return await self._request_json(
                url=f"{self.API_BASE}{path}",
                method="GET",
                query=query,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        except DonationAlertsApiError as exc:
            if retry_on_401 and exc.status_code == 401:
                await self.refresh_user_token()
                return await self._api_request(path, query=query, retry_on_401=False)
            raise

    async def get_user_profile(self, access_token: str | None = None) -> DonationAlertsIdentity:
        if self._identity is not None and access_token is None:
            return self._identity

        token = access_token or self.settings.donationalerts_access_token
        if not token:
            raise DonationAlertsApiError("Нет DONATIONALERTS_ACCESS_TOKEN")

        payload = await self._request_json(
            url=f"{self.API_BASE}/user/oauth",
            method="GET",
            headers={"Authorization": f"Bearer {token}"},
        )
        data = payload.get("data") or {}
        if not data or "id" not in data:
            raise DonationAlertsApiError("Не удалось получить профиль DonationAlerts")

        identity = DonationAlertsIdentity(
            user_id=str(data["id"]),
            code=str(data.get("code") or ""),
            name=str(data.get("name") or data.get("code") or ""),
        )
        if access_token is None:
            self._identity = identity
        return identity

    async def get_donations_for_window(
        self,
        *,
        started_at: datetime,
        ended_at: datetime | None,
        limit: int = 100,
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        profile = await self.get_user_profile()
        started_at = started_at.astimezone(timezone.utc)
        ended_at = (ended_at or datetime.now(timezone.utc)).astimezone(timezone.utc)

        items: list[dict[str, Any]] = []

        for page in range(1, max(1, max_pages) + 1):
            payload = await self._api_request(
                f"/alerts/donations",
                query={
                    "user_id": profile.user_id,
                    "page": page,
                },
            )
            batch = payload.get("data") or []
            if not batch:
                break

            stop_paging = False
            for row in batch:
                created_at = _parse_created_at(str(row.get("created_at") or ""))
                if created_at is None:
                    continue
                if created_at > ended_at:
                    continue
                if created_at < started_at:
                    stop_paging = True
                    continue
                items.append(dict(row))
                if len(items) >= limit:
                    return items[:limit]

            if stop_paging:
                break

        return items[:limit]
