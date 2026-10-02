from __future__ import annotations

import asyncio
import json
import logging
import re
import ssl
import time
from datetime import datetime, timezone

import websocket

from .config import Settings
from .session_store import SessionStore, utcnow
from .state_builder import cache_twitch_clip_rows
from .twitch_api import TwitchApiError, TwitchHelixClient


LOGGER = logging.getLogger("ending.collector")
PRIVMSG_RE = re.compile(r":(?P<login>[^!]+)!.* PRIVMSG #(?P<channel>[^ ]+) :")
EVENTSUB_WS_URL = "wss://eventsub.wss.twitch.tv/ws?keepalive_timeout_seconds=30"


def parse_twitch_datetime(raw: str) -> datetime:
    normalized = raw.strip().replace("Z", "+00:00")
    normalized = re.sub(r"\.(\d{6})\d+(?=[+-]\d{2}:\d{2}$)", r".\1", normalized)
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)


def decode_irc_tag_value(raw: str) -> str:
    return (
        str(raw or "")
        .replace(r"\s", " ")
        .replace(r"\:", ";")
        .replace(r"\\", "\\")
        .replace(r"\r", "\r")
        .replace(r"\n", "\n")
    )


def parse_irc_tags(line: str) -> tuple[dict[str, str], str]:
    if not line.startswith("@"):
        return {}, line
    if " " not in line:
        return {}, line
    raw_tags, remainder = line[1:].split(" ", 1)
    tags: dict[str, str] = {}
    for item in raw_tags.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
        else:
            key, value = item, ""
        tags[key] = decode_irc_tag_value(value)
    return tags, remainder


def parse_irc_timestamp(tags: dict[str, str]) -> datetime | None:
    raw = tags.get("tmi-sent-ts")
    if not raw:
        return None
    try:
        return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return None


def normalize_tier_label(tier: str) -> str:
    mapping = {
        "1000": "Tier 1",
        "2000": "Tier 2",
        "3000": "Tier 3",
    }
    return mapping.get(str(tier or "").strip(), str(tier or "").strip() or "подписка")


def humanize_automatic_reward(reward_type: str) -> str:
    mapping = {
        "single_message_bypass_sub_mode": "Сообщение без sub-only",
        "send_highlighted_message": "Выделенное сообщение",
        "random_sub_emote_unlock": "Случайный sub-эмоут",
        "chosen_sub_emote_unlock": "Выбранный sub-эмоут",
        "chosen_modified_sub_emote_unlock": "Модифицированный sub-эмоут",
        "message_effect": "Эффект сообщения",
        "gigantify_an_emote": "Увеличенный эмоут",
        "celebration": "Праздничная анимация",
    }
    key = str(reward_type or "").strip()
    return mapping.get(key, key or "Награда канала")


class LiveSessionCollector:
    def __init__(self, settings: Settings, store: SessionStore, twitch: TwitchHelixClient) -> None:
        self.settings = settings
        self.store = store
        self.twitch = twitch
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._eventsub_connected = False
        self._eventsub_last_error = ""
        self._clips_last_prefetch_at = ""
        self._clips_prefetch_count = 0
        self._clips_prefetch_last_error = ""

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._stream_monitor_loop(), name="stream-monitor"),
            asyncio.create_task(self._chatters_loop(), name="chatters-poll"),
            asyncio.create_task(self._clips_prefetch_loop(), name="clip-prefetch"),
            asyncio.create_task(self._irc_loop(), name="twitch-irc"),
            asyncio.create_task(self._eventsub_loop(), name="twitch-eventsub"),
        ]

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def status_payload(self) -> dict[str, object]:
        active = self.store.get_active_session()
        return {
            "running": self._running,
            "has_twitch_app_credentials": self.settings.has_twitch_app_credentials,
            "has_twitch_user_credentials": self.settings.has_twitch_user_credentials,
            "eventsub_connected": self._eventsub_connected,
            "eventsub_last_error": self._eventsub_last_error,
            "clips_prefetch": {
                "last_at": self._clips_last_prefetch_at,
                "last_count": self._clips_prefetch_count,
                "last_error": self._clips_prefetch_last_error,
                "interval_seconds": self.settings.clips_prefetch_seconds,
            },
            "active_session": {
                "stream_id": active.stream_id,
                "started_at": active.started_at.isoformat(),
                "status": active.status,
            }
            if active
            else None,
        }

    async def _stream_monitor_loop(self) -> None:
        while True:
            try:
                if not self.settings.has_twitch_app_credentials:
                    await asyncio.sleep(5)
                    continue

                stream = await self.twitch.get_live_stream()
                if stream:
                    self.store.open_or_resume_live_session(
                        channel_login=self.settings.twitch_channel_login,
                        stream_id=str(stream["id"]),
                        started_at=parse_twitch_datetime(str(stream["started_at"])),
                        session_title=str(stream.get("title") or ""),
                    )
                else:
                    self.store.close_active_session(ended_at=utcnow())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("stream monitor failed: %s", exc)

            await asyncio.sleep(max(15, self.settings.collector_stream_poll_seconds))

    async def _chatters_loop(self) -> None:
        while True:
            try:
                if not self.settings.has_twitch_user_credentials:
                    await asyncio.sleep(5)
                    continue

                active = self.store.get_active_session()
                if not active:
                    await asyncio.sleep(5)
                    continue

                names = await self.twitch.get_chatters()
                if names:
                    self.store.record_chatters(names)
            except asyncio.CancelledError:
                raise
            except TwitchApiError as exc:
                LOGGER.warning("chatters poll failed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("chatters poll crashed: %s", exc)

            await asyncio.sleep(max(20, self.settings.collector_chatters_poll_seconds))

    async def _clips_prefetch_loop(self) -> None:
        while True:
            try:
                if not self.settings.has_twitch_app_credentials:
                    await asyncio.sleep(5)
                    continue

                active = self.store.get_active_session()
                if not active:
                    await asyncio.sleep(10)
                    continue

                clips = await cache_twitch_clip_rows(
                    settings=self.settings,
                    twitch=self.twitch,
                    started_at=active.started_at,
                    ended_at=utcnow(),
                    limit=self.settings.clips_limit,
                )
                self._clips_last_prefetch_at = utcnow().isoformat()
                self._clips_prefetch_count = len(clips)
                self._clips_prefetch_last_error = ""
                if clips:
                    LOGGER.info("prefetched %s twitch clip(s)", len(clips))
            except asyncio.CancelledError:
                raise
            except TwitchApiError as exc:
                self._clips_prefetch_last_error = str(exc)
                LOGGER.warning("clip prefetch failed: %s", exc)
            except Exception as exc:  # noqa: BLE001
                self._clips_prefetch_last_error = str(exc)
                LOGGER.warning("clip prefetch crashed: %s", exc)

            await asyncio.sleep(max(60, self.settings.clips_prefetch_seconds))

    async def _irc_loop(self) -> None:
        while True:
            writer: asyncio.StreamWriter | None = None
            try:
                if not self.settings.has_twitch_user_credentials or not self.settings.twitch_channel_login:
                    await asyncio.sleep(5)
                    continue

                active = self.store.get_active_session()
                if not active:
                    await asyncio.sleep(5)
                    continue

                if not self.settings.twitch_auth_login:
                    identity = await self.twitch.validate_user_token()
                    self.settings.twitch_auth_login = identity["login"]
                    self.settings.twitch_auth_user_id = identity["user_id"]

                ssl_context = ssl.create_default_context()
                reader, writer = await asyncio.open_connection(
                    host="irc.chat.twitch.tv",
                    port=6697,
                    ssl=ssl_context,
                )

                await self._send_irc(writer, f"PASS oauth:{self.settings.twitch_user_access_token}")
                await self._send_irc(writer, f"NICK {self.settings.twitch_auth_login}")
                await self._send_irc(writer, "CAP REQ :twitch.tv/tags twitch.tv/commands")
                await self._send_irc(writer, f"JOIN #{self.settings.twitch_channel_login}")

                while True:
                    line = await reader.readline()
                    if not line:
                        raise ConnectionResetError("IRC connection closed")

                    decoded = line.decode("utf-8", errors="ignore").strip()
                    if decoded.startswith("PING"):
                        await self._send_irc(writer, decoded.replace("PING", "PONG", 1))
                        continue

                    tags, payload = parse_irc_tags(decoded)
                    match = PRIVMSG_RE.search(payload)
                    if match and match.group("channel").lower() == self.settings.twitch_channel_login:
                        login = match.group("login").lower()
                        seen_at = parse_irc_timestamp(tags)
                        self.store.record_chat_message(login, seen_at=seen_at)

                        if tags.get("first-msg") == "1":
                            display_name = tags.get("display-name") or login
                            event_key = tags.get("user-id") or login
                            self.store.record_session_event(
                                event_type="first_time_chatter",
                                event_key=event_key,
                                actor_login=login,
                                actor_display_name=display_name,
                                created_at=seen_at,
                            )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("irc collector reconnect: %s", exc)
            finally:
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:  # noqa: BLE001
                        pass

            await asyncio.sleep(5)

    async def _ensure_live_session(self) -> bool:
        active = self.store.get_active_session()
        if active:
            return True
        if not self.settings.has_twitch_app_credentials:
            return False
        try:
            stream = await self.twitch.get_live_stream()
            if not stream:
                return False
            self.store.open_or_resume_live_session(
                channel_login=self.settings.twitch_channel_login,
                stream_id=str(stream["id"]),
                started_at=parse_twitch_datetime(str(stream["started_at"])),
                session_title=str(stream.get("title") or ""),
            )
            return True
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("ensure live session failed: %s", exc)
            return False

    async def _eventsub_loop(self) -> None:
        reconnect_url = EVENTSUB_WS_URL
        should_subscribe = True

        while True:
            ws: websocket.WebSocket | None = None
            try:
                if not self.settings.has_twitch_user_credentials:
                    await asyncio.sleep(5)
                    continue

                ws = await asyncio.to_thread(websocket.create_connection, reconnect_url, timeout=35)
                welcome_raw = await asyncio.to_thread(ws.recv)
                payload = json.loads(str(welcome_raw))
                session = (payload.get("payload") or {}).get("session") or {}
                session_id = str(session.get("id") or "")
                keepalive_timeout = int(session.get("keepalive_timeout_seconds") or 30)
                if not session_id:
                    raise RuntimeError("EventSub session welcome missing id")
                ws.settimeout(max(keepalive_timeout + 5, 20))
                self._eventsub_connected = True
                self._eventsub_last_error = ""

                if should_subscribe:
                    await self._subscribe_eventsub_session(session_id)

                while True:
                    try:
                        raw = await asyncio.to_thread(ws.recv)
                    except websocket.WebSocketTimeoutException as exc:
                        raise ConnectionResetError("EventSub keepalive timeout") from exc

                    message = json.loads(str(raw))
                    metadata = message.get("metadata") or {}
                    message_type = str(metadata.get("message_type") or "")

                    if message_type == "session_keepalive":
                        continue

                    if message_type == "session_reconnect":
                        reconnect_url = str(((message.get("payload") or {}).get("session") or {}).get("reconnect_url") or EVENTSUB_WS_URL)
                        should_subscribe = False
                        break

                    if message_type == "revocation":
                        self._eventsub_last_error = f"revoked: {((message.get('payload') or {}).get('subscription') or {}).get('type', 'unknown')}"
                        LOGGER.warning("eventsub revoked: %s", self._eventsub_last_error)
                        continue

                    if message_type == "notification":
                        await self._handle_eventsub_notification(
                            subscription_type=str(metadata.get("subscription_type") or ""),
                            message_id=str(metadata.get("message_id") or ""),
                            event=(message.get("payload") or {}).get("event") or {},
                        )

                if ws is not None:
                    ws.close()
                    ws = None
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._eventsub_connected = False
                self._eventsub_last_error = str(exc)
                LOGGER.warning("eventsub reconnect: %s", exc)
            finally:
                self._eventsub_connected = False
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:  # noqa: BLE001
                        pass

            reconnect_url = EVENTSUB_WS_URL
            should_subscribe = True
            await asyncio.sleep(5)

    async def _subscribe_eventsub_session(self, session_id: str) -> None:
        broadcaster = await self.twitch.resolve_broadcaster()
        moderator_id = self.settings.twitch_auth_user_id or broadcaster.user_id
        subscriptions = [
            ("channel.follow", "2", {"broadcaster_user_id": broadcaster.user_id, "moderator_user_id": moderator_id}),
            ("channel.subscribe", "1", {"broadcaster_user_id": broadcaster.user_id}),
            ("channel.subscription.gift", "1", {"broadcaster_user_id": broadcaster.user_id}),
            ("channel.subscription.message", "1", {"broadcaster_user_id": broadcaster.user_id}),
            ("channel.cheer", "1", {"broadcaster_user_id": broadcaster.user_id}),
            ("channel.raid", "1", {"to_broadcaster_user_id": broadcaster.user_id}),
            ("channel.channel_points_custom_reward_redemption.add", "1", {"broadcaster_user_id": broadcaster.user_id}),
            ("channel.channel_points_automatic_reward_redemption.add", "2", {"broadcaster_user_id": broadcaster.user_id}),
        ]

        for subscription_type, version, condition in subscriptions:
            try:
                await self.twitch.create_eventsub_subscription(
                    subscription_type=subscription_type,
                    version=version,
                    condition=condition,
                    session_id=session_id,
                )
            except TwitchApiError as exc:
                LOGGER.warning("eventsub subscribe failed for %s: %s", subscription_type, exc)

    async def _handle_eventsub_notification(
        self,
        *,
        subscription_type: str,
        message_id: str,
        event: dict[str, object],
    ) -> None:
        if not message_id:
            return
        if not await self._ensure_live_session():
            return

        event_time_raw = (
            event.get("followed_at")
            or event.get("redeemed_at")
            or event.get("started_at")
            or event.get("created_at")
        )
        created_at = parse_twitch_datetime(str(event_time_raw)) if event_time_raw else utcnow()

        if subscription_type == "channel.follow":
            self.store.record_session_event(
                event_type="follow",
                event_key=message_id,
                actor_login=str(event.get("user_login") or ""),
                actor_display_name=str(event.get("user_name") or event.get("user_login") or ""),
                created_at=created_at,
            )
            return

        if subscription_type == "channel.subscribe":
            if bool(event.get("is_gift")):
                return
            tier_label = normalize_tier_label(str(event.get("tier") or ""))
            self.store.record_session_event(
                event_type="subscription",
                event_key=message_id,
                actor_login=str(event.get("user_login") or ""),
                actor_display_name=str(event.get("user_name") or event.get("user_login") or ""),
                value_text=tier_label,
                metadata={"tier_label": tier_label},
                created_at=created_at,
            )
            return

        if subscription_type == "channel.subscription.message":
            tier_label = normalize_tier_label(str(event.get("tier") or ""))
            self.store.record_session_event(
                event_type="resubscription",
                event_key=message_id,
                actor_login=str(event.get("user_login") or ""),
                actor_display_name=str(event.get("user_name") or event.get("user_login") or ""),
                value_int=int(event.get("cumulative_months") or 0),
                value_text=tier_label,
                metadata={"tier_label": tier_label},
                created_at=created_at,
            )
            return

        if subscription_type == "channel.subscription.gift":
            is_anonymous = bool(event.get("is_anonymous"))
            actor_name = "Анонимный зритель" if is_anonymous else str(event.get("user_name") or event.get("user_login") or "")
            actor_login = "" if is_anonymous else str(event.get("user_login") or "")
            tier_label = normalize_tier_label(str(event.get("tier") or ""))
            self.store.record_session_event(
                event_type="gift_subscription",
                event_key=message_id,
                actor_login=actor_login,
                actor_display_name=actor_name,
                value_int=int(event.get("total") or 0),
                metadata={"tier_label": tier_label, "is_anonymous": is_anonymous},
                created_at=created_at,
            )
            return

        if subscription_type == "channel.cheer":
            is_anonymous = bool(event.get("is_anonymous"))
            actor_name = "Анонимный зритель" if is_anonymous else str(event.get("user_name") or event.get("user_login") or "")
            actor_login = "" if is_anonymous else str(event.get("user_login") or "")
            self.store.record_session_event(
                event_type="bits",
                event_key=message_id,
                actor_login=actor_login,
                actor_display_name=actor_name,
                value_int=int(event.get("bits") or 0),
                metadata={"message": str(event.get("message") or ""), "is_anonymous": is_anonymous},
                created_at=created_at,
            )
            return

        if subscription_type == "channel.raid":
            self.store.record_session_event(
                event_type="raid",
                event_key=message_id,
                actor_login=str(event.get("from_broadcaster_user_login") or ""),
                actor_display_name=str(event.get("from_broadcaster_user_name") or event.get("from_broadcaster_user_login") or ""),
                value_int=int(event.get("viewers") or 0),
                created_at=created_at,
            )
            return

        if subscription_type == "channel.channel_points_custom_reward_redemption.add":
            reward = event.get("reward") or {}
            reward_title = ""
            if isinstance(reward, dict):
                reward_title = str(reward.get("title") or "")
            self.store.record_session_event(
                event_type="reward_redemption",
                event_key=message_id,
                actor_login=str(event.get("user_login") or ""),
                actor_display_name=str(event.get("user_name") or event.get("user_login") or ""),
                value_text=reward_title or "Награда канала",
                metadata={"user_input": str(event.get("user_input") or "")},
                created_at=created_at,
            )
            return

        if subscription_type == "channel.channel_points_automatic_reward_redemption.add":
            reward = event.get("reward") or {}
            reward_type = ""
            if isinstance(reward, dict):
                reward_type = str(reward.get("type") or "")
            self.store.record_session_event(
                event_type="reward_redemption_automatic",
                event_key=message_id,
                actor_login=str(event.get("user_login") or ""),
                actor_display_name=str(event.get("user_name") or event.get("user_login") or ""),
                value_text=humanize_automatic_reward(reward_type),
                metadata={"reward_type": reward_type},
                created_at=created_at,
            )

    async def _send_irc(self, writer: asyncio.StreamWriter, line: str) -> None:
        writer.write((line + "\r\n").encode("utf-8"))
        await writer.drain()
