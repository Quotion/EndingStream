from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clip_media import resolve_clip_media
from .config import Settings
from .donation_alerts import DonationAlertsApiError, DonationAlertsClient
from .session_store import SessionStore
from .twitch_api import TwitchApiError, TwitchHelixClient


LOCAL_CLIP_EXTENSIONS = {".mp4", ".webm"}


def _fallback_base_state() -> dict[str, Any]:
    return {
        "session_title": "Люди этого эфира",
        "audio": {"url": "assets/ending-track.mp3", "volume": 0.62, "mix_gain": 0.55},
        "final_message": "Спасибо вам\nза этот невероятный стрим!",
        "show": {
            "intro_seconds": 6,
            "outro_seconds": 12,
            "outro_early_seconds": 4,
            "music_fade_out_seconds": 2,
            "final_message_lead_seconds": 14,
            "final_message_prep_seconds": 2.5,
            "main_padding_seconds": 1,
            "stats_change_transition_ms": 650,
            "clip_crossfade_ms": 500,
            "clip_video_volume": 1,
            "duck_music_during_video_clips": True,
            "music_duck_level": 0.12,
            "auto_stop_stream": True,
            "stop_stream_at": "after_final_message",
            "stop_stream_lead_seconds": 0.5,
            "stop_stream_delay_seconds": 10,
        },
        "stats": {
            "total_seen_label": "Свой след сегодня оставили",
            "total_seen_users": 0,
        },
        "top_chatters": [],
        "top_streaks": [],
        "first_time_chatters": [],
        "follows": [],
        "subscription_events": [],
        "bits_leaders": [],
        "raids": [],
        "reward_redemptions": [],
        "donors": [],
        "credits": [],
        "clips": [],
        "theme": {
            "bg_color": "#0f0e0b",
            "panel_color": "rgba(28,22,18,0.92)",
            "accent_color": "#c6a876",
            "text_color": "#f5efe6",
            "muted_text_color": "rgba(245,239,230,0.62)",
            "success_color": "#dcc9a4",
            "danger_color": "#b8957a",
        },
    }


def load_base_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return _fallback_base_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except json.JSONDecodeError:
        pass
    return _fallback_base_state()


def _to_static_url(settings: Settings, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(settings.static_dir.resolve())
        return rel.as_posix()
    except ValueError:
        return path.resolve().as_uri()


def _title_from_clip_path(path: Path) -> str:
    title = path.stem.replace("_", " ").replace("-", " ").strip()
    return title or "Локальный клип"


def load_local_clip_fallbacks(settings: Settings) -> list[dict[str, Any]]:
    clips_dir = settings.local_clips_dir
    if not clips_dir.exists():
        return []

    clips: list[dict[str, Any]] = []
    for path in sorted(clips_dir.iterdir(), key=lambda item: item.name.casefold()):
        if not path.is_file() or path.suffix.lower() not in LOCAL_CLIP_EXTENSIONS:
            continue
        clips.append(
            {
                "type": "video",
                "url": _to_static_url(settings, path),
                "title": _title_from_clip_path(path),
                "preferred_duration_sec": 12,
                "duration_sec": 12,
                "source": "local-fallback",
            }
        )
    return clips


def _clip_cache_manifest_path(settings: Settings) -> Path:
    return settings.data_dir / "clip-cache-manifest.json"


def load_cached_clip_manifest(settings: Settings) -> list[dict[str, Any]]:
    path = _clip_cache_manifest_path(settings)
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []

    clips: list[dict[str, Any]] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "")
        if not url:
            continue
        candidate = (settings.static_dir / url).resolve()
        if not candidate.exists() or candidate.suffix.lower() not in LOCAL_CLIP_EXTENSIONS:
            continue
        item = dict(row)
        item["source"] = item.get("source") or "twitch-cache"
        item["cached"] = True
        clips.append(item)
    return clips


def save_cached_clip_manifest(settings: Settings, clips: list[dict[str, Any]]) -> None:
    cached_clips: list[dict[str, Any]] = []
    for clip in clips:
        url = str(clip.get("url") or "")
        if not url.startswith("assets/clip-cache/"):
            continue
        cached_clips.append(
            {
                "type": "video",
                "url": url,
                "title": str(clip.get("title") or "Twitch clip"),
                "preferred_duration_sec": clip.get("preferred_duration_sec") or 12,
                "duration_sec": clip.get("duration_sec") or clip.get("preferred_duration_sec") or 12,
                "view_count": clip.get("view_count") or 0,
                "poster": clip.get("poster") or clip.get("thumbnail_url"),
                "thumbnail_url": clip.get("thumbnail_url"),
                "fallback_embed_url": clip.get("fallback_embed_url") or clip.get("embed_url"),
                "source": "twitch-cache",
                "cached": True,
            }
        )

    if not cached_clips:
        return

    path = _clip_cache_manifest_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(cached_clips, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _clip_row_identity(row: dict[str, Any]) -> str:
    for key in ("id", "url", "embed_url", "title"):
        value = str(row.get(key) or "").strip().casefold()
        if value:
            return f"{key}:{value}"
    return repr(sorted(row.items()))


def _merge_clip_rows(primary: list[dict[str, Any]], extra: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in [*primary, *extra]:
        if not isinstance(row, dict):
            continue
        key = _clip_row_identity(row)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
        if len(result) >= limit:
            break
    return result


async def _get_clip_rows_with_topup(
    *,
    settings: Settings,
    twitch: TwitchHelixClient,
    started_at: datetime | None,
    ended_at: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    primary: list[dict[str, Any]] = []
    if started_at:
        primary = await twitch.get_clips_for_window(
            started_at=started_at,
            ended_at=ended_at,
            limit=limit,
        )

    if len(primary) >= limit:
        return primary[:limit]

    merged = list(primary)
    fallback_windows = [settings.clips_fallback_days, 90, 365]
    for days in fallback_windows:
        if len(merged) >= limit:
            break
        extra = await twitch.get_fallback_clips(
            limit=limit,
            days=days,
        )
        merged = _merge_clip_rows(merged, extra, limit=limit)
    return merged


def _with_local_clip_fallbacks(
    clips: list[dict[str, Any]],
    local_fallbacks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not clips or not local_fallbacks:
        return clips

    result: list[dict[str, Any]] = []
    for idx, clip in enumerate(clips):
        item = dict(clip)
        fallback = local_fallbacks[idx % len(local_fallbacks)]
        fallback_url = str(fallback.get("url") or "")
        if fallback_url:
            item.setdefault("fallback_local_url", fallback_url)
            item.setdefault("fallback_video_url", fallback_url)
            item.setdefault("fallback_local_title", fallback.get("title") or "Локальный клип")
        result.append(item)
    return result


def _format_donation_amount(amount: float, currency: str) -> str:
    rounded = round(float(amount or 0), 2)
    if currency == "RUB":
        whole = int(round(rounded))
        return f"{whole:,}".replace(",", " ") + " ₽"
    if currency == "USD":
        return f"${rounded:,.2f}".replace(",", " ")
    if currency == "EUR":
        return f"{rounded:,.2f} €".replace(",", " ")
    return f"{rounded:,.2f} {currency}".replace(",", " ")


def _pluralize_ru(count: int, form1: str, form2: str, form5: str) -> str:
    value = abs(int(count))
    mod100 = value % 100
    mod10 = value % 10
    if 11 <= mod100 <= 14:
        return form5
    if mod10 == 1:
        return form1
    if 2 <= mod10 <= 4:
        return form2
    return form5


def _normalize_donors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        raw_name = str(row.get("username") or row.get("name") or "Anonymous").strip()
        donor_name = raw_name or "Anonymous"
        currency = str(row.get("currency") or row.get("currency_code") or "RUB").upper()
        amount_raw = row.get("amount_main") or row.get("amount") or 0
        try:
            amount = float(amount_raw)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            continue

        key = (donor_name.casefold(), currency)
        message = str(row.get("message") or "").strip()
        item = grouped.setdefault(
            key,
            {
                "name": donor_name,
                "currency": currency,
                "amount": 0.0,
                "count": 0,
                "latest_message": "",
            },
        )
        item["amount"] = float(item["amount"]) + amount
        item["count"] = int(item["count"]) + 1
        if message:
            item["latest_message"] = message

    donors = sorted(
        grouped.values(),
        key=lambda row: (-float(row["amount"]), -int(row["count"]), str(row["name"]).casefold()),
    )

    result: list[dict[str, Any]] = []
    for row in donors[:100]:
        amount = float(row["amount"])
        currency = str(row["currency"])
        count = int(row["count"])
        result.append(
            {
                "name": str(row["name"]),
                "amount": amount,
                "currency": currency,
                "count": count,
                "formatted_amount": _format_donation_amount(amount, currency),
                "contribution_label": f"{count} {_pluralize_ru(count, 'донат', 'доната', 'донатов')}",
                "message": str(row["latest_message"] or ""),
            }
        )
    return result


async def _clip_from_twitch(row: dict[str, Any], *, settings: Settings, prefer_direct_media: bool) -> dict[str, Any]:
    duration = float(row.get("duration") or 0) or 15.0
    embed_url = str(row.get("embed_url") or row.get("url") or "")
    page_url = str(row.get("url") or embed_url or "")
    thumbnail_url = row.get("thumbnail_url")
    title = str(row.get("title") or "Twitch clip")
    view_count = int(row.get("view_count") or 0)
    cache_key = str(row.get("id") or page_url or embed_url or title)

    direct_media = None
    if prefer_direct_media:
        direct_media = await resolve_clip_media(
            cache_key=cache_key,
            source_url=page_url,
            cache_dir=settings.clip_cache_dir,
        )

    if direct_media and direct_media.get("url"):
        resolved_duration = float(direct_media.get("duration") or duration or 15.0)
        preferred = max(1, round(resolved_duration))
        media_url = str(direct_media["url"])
        local_path = direct_media.get("local_path")
        if local_path:
            media_url = _to_static_url(settings, Path(str(local_path)))
        return {
            "type": "video",
            "url": media_url,
            "title": title,
            "preferred_duration_sec": preferred,
            "duration_sec": resolved_duration,
            "view_count": view_count,
            "poster": direct_media.get("thumbnail") or thumbnail_url,
            "thumbnail_url": thumbnail_url,
            "embed_url": embed_url,
            "fallback_embed_url": embed_url,
            "source": "twitch",
            "cached": bool(direct_media.get("cached")),
            "remote_media_url": direct_media.get("remote_url"),
        }

    preferred = max(1, round(duration))
    return {
        "type": "embed",
        "url": embed_url,
        "title": title,
        "preferred_duration_sec": preferred,
        "duration_sec": duration,
        "view_count": view_count,
        "thumbnail_url": thumbnail_url,
        "embed_url": embed_url,
        "source": "twitch",
    }


async def cache_twitch_clip_rows(
    *,
    settings: Settings,
    twitch: TwitchHelixClient,
    started_at: datetime,
    ended_at: datetime | None,
    limit: int,
) -> list[dict[str, Any]]:
    rows = await _get_clip_rows_with_topup(
        settings=settings,
        twitch=twitch,
        started_at=started_at,
        ended_at=ended_at,
        limit=limit,
    )
    if not rows:
        return []

    clips = list(
        await asyncio.gather(
            *(_clip_from_twitch(row, settings=settings, prefer_direct_media=True) for row in rows)
        )
    )
    save_cached_clip_manifest(settings, clips)
    return clips


async def _resolve_dynamic_clips(
    *,
    settings: Settings,
    twitch: TwitchHelixClient,
    session_snapshot: dict[str, Any],
    base_state: dict[str, Any],
) -> list[dict[str, Any]]:
    local_fallbacks = load_local_clip_fallbacks(settings)
    cached_clips = load_cached_clip_manifest(settings)
    base_clips = list(base_state.get("clips") or [])
    if base_clips:
        base_clips = _with_local_clip_fallbacks(base_clips, local_fallbacks)
    if cached_clips:
        cached_clips = _with_local_clip_fallbacks(cached_clips, local_fallbacks)
    offline_clips = cached_clips or base_clips or local_fallbacks

    if not settings.has_twitch_app_credentials:
        return offline_clips

    try:
        session = session_snapshot.get("session") or {}
        clips: list[dict[str, Any]] = []

        started_at = session.get("started_at")
        ended_at = session.get("ended_at")
        if started_at:
            clips = await _get_clip_rows_with_topup(
                settings=settings,
                twitch=twitch,
                started_at=started_at,
                ended_at=ended_at or datetime.now(timezone.utc),
                limit=settings.clips_limit,
            )

        if not clips:
            clips = await twitch.get_fallback_clips(
                limit=settings.clips_limit,
                days=settings.clips_fallback_days,
            )

        if not clips:
            return offline_clips

        dynamic_clips = list(
            await asyncio.gather(
                *(_clip_from_twitch(row, settings=settings, prefer_direct_media=True) for row in clips)
            )
        )
        dynamic_clips = _with_local_clip_fallbacks(dynamic_clips, local_fallbacks)
        save_cached_clip_manifest(settings, dynamic_clips)
        return dynamic_clips
    except TwitchApiError:
        return offline_clips
    except Exception:
        return offline_clips


async def _resolve_dynamic_donors(
    *,
    settings: Settings,
    donation_alerts: DonationAlertsClient,
    session_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    if not (
        settings.has_donationalerts_app_credentials and settings.has_donationalerts_user_credentials
    ):
        return []

    session = session_snapshot.get("session") or {}
    started_at = session.get("started_at")
    if not started_at:
        return []

    ended_at = session.get("ended_at") or datetime.now(timezone.utc)

    try:
        rows = await donation_alerts.get_donations_for_window(
            started_at=started_at,
            ended_at=ended_at,
        )
        return _normalize_donors(rows)
    except DonationAlertsApiError:
        return []
    except Exception:
        return []


async def build_ending_state(
    *,
    settings: Settings,
    store: SessionStore,
    twitch: TwitchHelixClient,
    donation_alerts: DonationAlertsClient,
    demo_mode: bool = False,
) -> dict[str, Any]:
    base_state = load_base_state(settings.mock_state_path)
    snapshot = store.build_snapshot()
    session = snapshot.get("session") or {}

    clips = await _resolve_dynamic_clips(
        settings=settings,
        twitch=twitch,
        session_snapshot=snapshot,
        base_state=base_state,
    )
    donors = await _resolve_dynamic_donors(
        settings=settings,
        donation_alerts=donation_alerts,
        session_snapshot=snapshot,
    )

    result = copy.deepcopy(base_state)
    result["clips"] = clips

    base_stats = result.setdefault("stats", {})
    dynamic_total_seen = int(snapshot.get("total_seen_users") or 0)
    fallback_total_seen = int(base_stats.get("total_seen_users") or 0)
    if not demo_mode or not fallback_total_seen:
        base_stats["total_seen_users"] = dynamic_total_seen or fallback_total_seen

    if demo_mode:
        result["top_chatters"] = list(result.get("top_chatters") or snapshot.get("top_chatters") or [])
        result["top_streaks"] = list(result.get("top_streaks") or snapshot.get("top_streaks") or [])
        result["first_time_chatters"] = list(result.get("first_time_chatters") or snapshot.get("first_time_chatters") or [])
        result["follows"] = list(result.get("follows") or snapshot.get("follows") or [])
        result["subscription_events"] = list(result.get("subscription_events") or snapshot.get("subscription_events") or [])
        result["bits_leaders"] = list(result.get("bits_leaders") or snapshot.get("bits_leaders") or [])
        result["raids"] = list(result.get("raids") or snapshot.get("raids") or [])
        result["reward_redemptions"] = list(result.get("reward_redemptions") or snapshot.get("reward_redemptions") or [])
        result["donors"] = list(base_state.get("donors") or donors or [])
        result["credits"] = list(result.get("credits") or snapshot.get("credits") or [])
    else:
        result["top_chatters"] = snapshot.get("top_chatters") or list(result.get("top_chatters") or [])
        result["top_streaks"] = snapshot.get("top_streaks") or list(result.get("top_streaks") or [])
        result["first_time_chatters"] = snapshot.get("first_time_chatters") or []
        result["follows"] = snapshot.get("follows") or []
        result["subscription_events"] = snapshot.get("subscription_events") or []
        result["bits_leaders"] = snapshot.get("bits_leaders") or []
        result["raids"] = snapshot.get("raids") or []
        result["reward_redemptions"] = snapshot.get("reward_redemptions") or []
        result["donors"] = list(donors or [])
        result["credits"] = snapshot.get("credits") or list(result.get("credits") or [])

    session_title = str(result.get("session_title") or "").strip()
    stream_title = str(session.get("title") or "").strip()
    if not session_title:
        result["session_title"] = stream_title or "Люди этого эфира"

    result.setdefault("meta", {})
    result["meta"]["collector"] = {
        "active_session": bool(session),
        "stream_id": session.get("stream_id"),
        "status": session.get("status"),
        "credits_count": len(result.get("credits") or []),
        "first_time_chatters_count": len(result.get("first_time_chatters") or []),
        "follows_count": len(result.get("follows") or []),
        "subscription_events_count": len(result.get("subscription_events") or []),
        "bits_leaders_count": len(result.get("bits_leaders") or []),
        "raids_count": len(result.get("raids") or []),
        "reward_redemptions_count": len(result.get("reward_redemptions") or []),
        "donors_count": len(result.get("donors") or []),
        "demo_mode": demo_mode,
    }
    return result
