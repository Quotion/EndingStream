from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_BOT_LOGINS = frozenset(
    {
        "ankhbot",
        "botisimo",
        "coebot",
        "deepbot",
        "fossabot",
        "jeetbot",
        "moobot",
        "nightbot",
        "phantombot",
        "sery_bot",
        "soundalerts",
        "streamelements",
        "streamlabs",
        "streamlabsbot",
        "streamstickers",
        "supibot",
        "tangiabot",
        "tune2livebot",
        "wizebot",
        "xanbot",
    }
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_login_list(raw: str) -> set[str]:
    return {
        part.strip().lower().lstrip("@")
        for part in raw.replace(";", ",").split(",")
        if part.strip()
    }


def configured_bot_logins() -> set[str]:
    custom = _parse_login_list(os.getenv("ENDING_BOT_LOGINS", ""))
    channel_login = os.getenv("TWITCH_CHANNEL_LOGIN", "").strip().lower().lstrip("@")
    broadcaster = {channel_login} if channel_login else set()
    return set(DEFAULT_BOT_LOGINS) | custom | broadcaster


def is_bot_login(user_login: str) -> bool:
    login = user_login.strip().lower().lstrip("@")
    if not login:
        return False
    return login in configured_bot_logins()


@dataclass
class SessionRow:
    id: int
    channel_login: str
    stream_id: str
    session_title: str
    started_at: datetime
    ended_at: datetime | None
    status: str


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._ensure_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              channel_login TEXT NOT NULL,
              stream_id TEXT NOT NULL UNIQUE,
              session_title TEXT NOT NULL DEFAULT '',
              started_at TEXT NOT NULL,
              ended_at TEXT,
              status TEXT NOT NULL DEFAULT 'live'
            );

            CREATE TABLE IF NOT EXISTS session_users (
              session_id INTEGER NOT NULL,
              user_login TEXT NOT NULL,
              first_seen_at TEXT NOT NULL,
              last_seen_at TEXT NOT NULL,
              message_count INTEGER NOT NULL DEFAULT 0,
              seen_in_chatters INTEGER NOT NULL DEFAULT 0,
              PRIMARY KEY (session_id, user_login),
              FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS session_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              event_key TEXT NOT NULL,
              actor_login TEXT NOT NULL DEFAULT '',
              actor_display_name TEXT NOT NULL DEFAULT '',
              related_login TEXT NOT NULL DEFAULT '',
              related_display_name TEXT NOT NULL DEFAULT '',
              value_int INTEGER NOT NULL DEFAULT 0,
              value_text TEXT NOT NULL DEFAULT '',
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              UNIQUE(session_id, event_type, event_key),
              FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_session_events_lookup
              ON session_events(session_id, event_type, created_at DESC);
            """
        )
        self._conn.commit()

    def _row_to_session(self, row: sqlite3.Row | None) -> SessionRow | None:
        if row is None:
            return None
        return SessionRow(
            id=int(row["id"]),
            channel_login=str(row["channel_login"]),
            stream_id=str(row["stream_id"]),
            session_title=str(row["session_title"] or ""),
            started_at=datetime.fromisoformat(str(row["started_at"])),
            ended_at=datetime.fromisoformat(str(row["ended_at"])) if row["ended_at"] else None,
            status=str(row["status"]),
        )

    def get_active_session(self) -> SessionRow | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM sessions
                WHERE status = 'live'
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            return self._row_to_session(row)

    def get_latest_session(self) -> SessionRow | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT *
                FROM sessions
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
            return self._row_to_session(row)

    def open_or_resume_live_session(
        self,
        *,
        channel_login: str,
        stream_id: str,
        started_at: datetime,
        session_title: str,
    ) -> SessionRow:
        started_at = started_at.astimezone(timezone.utc)
        with self._lock:
            active = self.get_active_session()
            if active and active.stream_id == stream_id:
                self._conn.execute(
                    "UPDATE sessions SET session_title = ? WHERE id = ?",
                    (session_title, active.id),
                )
                self._conn.commit()
                return self.get_active_session() or active

            if active and active.stream_id != stream_id:
                self.close_active_session(ended_at=utcnow())

            existing = self._conn.execute(
                "SELECT * FROM sessions WHERE stream_id = ? LIMIT 1",
                (stream_id,),
            ).fetchone()
            if existing:
                self._conn.execute(
                    """
                    UPDATE sessions
                    SET status = 'live', ended_at = NULL, session_title = ?, started_at = ?, channel_login = ?
                    WHERE stream_id = ?
                    """,
                    (session_title, started_at.isoformat(), channel_login, stream_id),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO sessions (channel_login, stream_id, session_title, started_at, status)
                    VALUES (?, ?, ?, ?, 'live')
                    """,
                    (channel_login, stream_id, session_title, started_at.isoformat()),
                )
            self._conn.commit()
            return self.get_active_session() or self.get_latest_session()  # type: ignore[return-value]

    def close_active_session(self, ended_at: datetime | None = None) -> None:
        ended_at = (ended_at or utcnow()).astimezone(timezone.utc)
        with self._lock:
            active = self.get_active_session()
            if not active:
                return
            self._conn.execute(
                """
                UPDATE sessions
                SET status = 'ended', ended_at = ?
                WHERE id = ?
                """,
                (ended_at.isoformat(), active.id),
            )
            self._conn.commit()

    def _record_user_seen(
        self,
        *,
        session_id: int,
        user_login: str,
        message_increment: int = 0,
        seen_in_chatters: bool = False,
        seen_at: datetime | None = None,
    ) -> None:
        if not user_login:
            return
        seen_at = (seen_at or utcnow()).astimezone(timezone.utc)
        normalized = user_login.strip().lower()
        if not normalized:
            return

        self._conn.execute(
            """
            INSERT INTO session_users (
              session_id,
              user_login,
              first_seen_at,
              last_seen_at,
              message_count,
              seen_in_chatters
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, user_login) DO UPDATE SET
              last_seen_at = excluded.last_seen_at,
              message_count = session_users.message_count + ?,
              seen_in_chatters = CASE
                WHEN excluded.seen_in_chatters = 1 THEN 1
                ELSE session_users.seen_in_chatters
              END
            """,
            (
                session_id,
                normalized,
                seen_at.isoformat(),
                seen_at.isoformat(),
                message_increment,
                1 if seen_in_chatters else 0,
                message_increment,
            ),
        )

    def record_chat_message(self, user_login: str, seen_at: datetime | None = None) -> None:
        with self._lock:
            active = self.get_active_session()
            if not active:
                return
            self._record_user_seen(
                session_id=active.id,
                user_login=user_login,
                message_increment=1,
                seen_in_chatters=False,
                seen_at=seen_at,
            )
            self._conn.commit()

    def record_chatters(self, user_logins: list[str], seen_at: datetime | None = None) -> None:
        with self._lock:
            active = self.get_active_session()
            if not active:
                return
            for user_login in user_logins:
                self._record_user_seen(
                    session_id=active.id,
                    user_login=user_login,
                    message_increment=0,
                    seen_in_chatters=True,
                    seen_at=seen_at,
                )
            self._conn.commit()

    def record_session_event(
        self,
        *,
        event_type: str,
        event_key: str,
        actor_login: str = "",
        actor_display_name: str = "",
        related_login: str = "",
        related_display_name: str = "",
        value_int: int = 0,
        value_text: str = "",
        metadata: dict[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> bool:
        created_at = (created_at or utcnow()).astimezone(timezone.utc)
        with self._lock:
            active = self.get_active_session()
            if not active or not event_type or not event_key:
                return False

            cursor = self._conn.execute(
                """
                INSERT OR IGNORE INTO session_events (
                  session_id,
                  event_type,
                  event_key,
                  actor_login,
                  actor_display_name,
                  related_login,
                  related_display_name,
                  value_int,
                  value_text,
                  metadata_json,
                  created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    active.id,
                    event_type.strip(),
                    event_key.strip(),
                    actor_login.strip().lower(),
                    actor_display_name.strip(),
                    related_login.strip().lower(),
                    related_display_name.strip(),
                    int(value_int or 0),
                    value_text.strip(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    created_at.isoformat(),
                ),
            )
            self._conn.commit()
            return cursor.rowcount > 0

    def _get_reference_session(self) -> SessionRow | None:
        return self.get_active_session() or self.get_latest_session()

    def _get_session_users(self, session_id: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                """
                SELECT *
                FROM session_users
                WHERE session_id = ?
                ORDER BY user_login COLLATE NOCASE ASC
                """,
                (session_id,),
            ).fetchall()

    def _compute_top_chatters(self, session_id: int, limit: int = 3) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT user_login, message_count
                FROM session_users
                WHERE session_id = ? AND message_count > 0
                ORDER BY message_count DESC, user_login ASC
                """,
                (session_id,),
            ).fetchall()
            return [
                {
                    "name": str(row["user_login"]),
                    "messages": int(row["message_count"]),
                }
                for row in rows
                if not is_bot_login(str(row["user_login"]))
            ][:limit]

    def _compute_streaks(self, session_id: int, limit: int = 3) -> list[dict[str, Any]]:
        with self._lock:
            session = self._conn.execute(
                "SELECT id, channel_login, started_at FROM sessions WHERE id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
            if not session:
                return []

            channel_login = str(session["channel_login"])
            reference_started_at = str(session["started_at"])
            ordered_sessions = self._conn.execute(
                """
                SELECT id
                FROM sessions
                WHERE channel_login = ?
                  AND (started_at < ? OR (started_at = ? AND id <= ?))
                ORDER BY started_at DESC, id DESC
                """,
                (channel_login, reference_started_at, reference_started_at, int(session["id"])),
            ).fetchall()
            session_ids = [int(row["id"]) for row in ordered_sessions]
            if not session_ids:
                return []

            users = [
                str(row["user_login"])
                for row in self._conn.execute(
                    "SELECT user_login FROM session_users WHERE session_id = ?",
                    (session_id,),
                ).fetchall()
                if not is_bot_login(str(row["user_login"]))
            ]
            results: list[dict[str, Any]] = []

            for login in users:
                rows = self._conn.execute(
                    """
                    SELECT s.id
                    FROM sessions AS s
                    JOIN session_users AS su
                      ON su.session_id = s.id
                    WHERE s.channel_login = ?
                      AND su.user_login = ?
                      AND (s.started_at < ? OR (s.started_at = ? AND s.id <= ?))
                    """,
                    (channel_login, login, reference_started_at, reference_started_at, int(session["id"])),
                ).fetchall()
                present_session_ids = {int(row["id"]) for row in rows}
                streak = 0
                for previous_session_id in session_ids:
                    if previous_session_id not in present_session_ids:
                        break
                    streak += 1
                results.append({"name": login, "streak": streak})

            results.sort(key=lambda row: (-int(row["streak"]), str(row["name"])))
            return [row for row in results if int(row["streak"]) > 1][:limit]

    def _get_session_events(
        self,
        session_id: int,
        *,
        event_type: str,
        limit: int | None = None,
        order_desc: bool = True,
    ) -> list[sqlite3.Row]:
        direction = "DESC" if order_desc else "ASC"
        sql = f"""
            SELECT *
            FROM session_events
            WHERE session_id = ? AND event_type = ?
            ORDER BY created_at {direction}, id {direction}
        """
        params: list[Any] = [session_id, event_type]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            return self._conn.execute(sql, tuple(params)).fetchall()

    def _compute_first_time_chatters(self, session_id: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._get_session_events(session_id, event_type="first_time_chatter", order_desc=False)
        result = []
        for row in rows:
            name = str(row["actor_display_name"] or row["actor_login"] or "—")
            if is_bot_login(str(row["actor_login"] or name)):
                continue
            result.append({"name": name})
            if len(result) >= limit:
                break
        return result

    def _compute_follows(self, session_id: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._get_session_events(session_id, event_type="follow", order_desc=True)
        result = []
        for row in rows:
            name = str(row["actor_display_name"] or row["actor_login"] or "—")
            if is_bot_login(str(row["actor_login"] or name)):
                continue
            result.append({"name": name})
            if len(result) >= limit:
                break
        return result

    def _compute_subscription_events(self, session_id: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = []
        for event_type in ("subscription", "resubscription", "gift_subscription"):
            rows.extend(self._get_session_events(session_id, event_type=event_type, order_desc=True))

        rows.sort(key=lambda row: str(row["created_at"]), reverse=True)
        result: list[dict[str, Any]] = []
        for row in rows:
            display_name = str(row["actor_display_name"] or row["actor_login"] or "—")
            if is_bot_login(str(row["actor_login"] or display_name)):
                continue
            value_text = str(row["value_text"] or "")
            metadata = json.loads(str(row["metadata_json"] or "{}"))
            event_type = str(row["event_type"])

            if event_type == "gift_subscription":
                count = int(row["value_int"] or 0) or 1
                line = f"{count} {'подарок' if count == 1 else 'подарка' if count < 5 else 'подарков'}"
                if metadata.get("tier_label"):
                    line = f"{line} · {metadata['tier_label']}"
            elif event_type == "resubscription":
                months = int(row["value_int"] or 0)
                line = f"{months} мес. подряд" if months > 0 else "ресаб"
                if value_text:
                    line = f"{line} · {value_text}"
            else:
                line = value_text or "новая подписка"

            result.append(
                {
                    "name": display_name,
                    "detail": line,
                }
            )
            if len(result) >= limit:
                break
        return result

    def _compute_bits_leaders(self, session_id: int, limit: int = 3) -> list[dict[str, Any]]:
        rows = self._get_session_events(session_id, event_type="bits", order_desc=True)
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            key = str(row["actor_login"] or row["actor_display_name"] or "").strip().casefold()
            if not key or is_bot_login(key):
                continue
            item = grouped.setdefault(
                key,
                {
                    "name": str(row["actor_display_name"] or row["actor_login"] or "—"),
                    "bits": 0,
                },
            )
            item["bits"] = int(item["bits"]) + int(row["value_int"] or 0)

        result = sorted(grouped.values(), key=lambda row: (-int(row["bits"]), str(row["name"]).casefold()))
        return result[:limit]

    def _compute_raids(self, session_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._get_session_events(session_id, event_type="raid", order_desc=True)
        result = []
        for row in rows:
            name = str(row["actor_display_name"] or row["actor_login"] or "—")
            if is_bot_login(str(row["actor_login"] or name)):
                continue
            result.append({
                "name": name,
                "detail": f"{int(row['value_int'] or 0)} зрителей" if int(row["value_int"] or 0) > 0 else "рейд",
            })
            if len(result) >= limit:
                break
        return result

    def _compute_redemptions(self, session_id: int, limit: int = 100) -> list[dict[str, Any]]:
        rows = []
        for event_type in ("reward_redemption", "reward_redemption_automatic"):
            rows.extend(self._get_session_events(session_id, event_type=event_type, order_desc=True))

        user_rewards: dict[str, dict[str, Any]] = {}
        for row in rows:
            name = str(row["actor_display_name"] or row["actor_login"] or "—")
            key = str(row["actor_login"] or name).strip().casefold() or name
            if is_bot_login(key):
                continue
            reward = str(row["value_text"] or "Награда канала")
            if key not in user_rewards:
                user_rewards[key] = {"name": name, "rewards": {}, "total": 0}
            user_rewards[key]["rewards"][reward] = user_rewards[key]["rewards"].get(reward, 0) + 1
            user_rewards[key]["total"] += 1

        sorted_users = sorted(user_rewards.values(), key=lambda x: x["total"], reverse=True)
        result = []
        for u in sorted_users[:limit]:
            details = []
            reward_items = sorted(u["rewards"].items(), key=lambda x: (-x[1], str(x[0]).casefold()))
            visible_rewards = reward_items[:4]
            hidden_uses = sum(r_count for _, r_count in reward_items[4:])
            for r_name, r_count in visible_rewards:
                details.append(f"{r_name} ({r_count})" if r_count > 1 else r_name)
            if hidden_uses:
                details.append(f"ещё {hidden_uses}")
            result.append({
                "name": u["name"],
                "detail": ", ".join(details)
            })
        return result

    def build_snapshot(self) -> dict[str, Any]:
        session = self._get_reference_session()
        if not session:
            return {
                "session": None,
                "total_seen_users": 0,
                "top_chatters": [],
                "top_streaks": [],
                "first_time_chatters": [],
                "follows": [],
                "subscription_events": [],
                "bits_leaders": [],
                "raids": [],
                "reward_redemptions": [],
                "credits": [],
            }

        users = [row for row in self._get_session_users(session.id) if not is_bot_login(str(row["user_login"]))]
        credits = [str(row["user_login"]) for row in users]
        return {
            "session": {
                "id": session.id,
                "stream_id": session.stream_id,
                "title": session.session_title,
                "started_at": session.started_at,
                "ended_at": session.ended_at,
                "status": session.status,
            },
            "total_seen_users": len(users),
            "top_chatters": self._compute_top_chatters(session.id),
            "top_streaks": self._compute_streaks(session.id),
            "first_time_chatters": self._compute_first_time_chatters(session.id),
            "follows": self._compute_follows(session.id),
            "subscription_events": self._compute_subscription_events(session.id),
            "bits_leaders": self._compute_bits_leaders(session.id),
            "raids": self._compute_raids(session.id),
            "reward_redemptions": self._compute_redemptions(session.id),
            "credits": credits,
        }
