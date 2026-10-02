from __future__ import annotations

import asyncio
import hashlib
import logging
import shutil
import urllib.request
from pathlib import Path
from typing import Any

try:
    from yt_dlp import YoutubeDL
except ImportError:  # pragma: no cover - graceful fallback when dependency is absent
    YoutubeDL = None


LOGGER = logging.getLogger("ending.backend.clip_media")
_MEDIA_CACHE: dict[str, dict[str, Any] | None] = {}
DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    )
}


def _cache_path_for(cache_dir: Path, cache_key: str) -> Path:
    digest = hashlib.sha1(cache_key.encode("utf-8", errors="ignore")).hexdigest()[:20]
    return cache_dir / f"{digest}.mp4"


def _download_media_sync(url: str, target_path: Path) -> bool:
    if not url:
        return False

    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(target_path.suffix + ".part")
    request = urllib.request.Request(url, headers=DOWNLOAD_HEADERS)

    try:
        with urllib.request.urlopen(request, timeout=30) as response, tmp_path.open("wb") as out:
            shutil.copyfileobj(response, out, length=1024 * 1024)
        if tmp_path.stat().st_size <= 0:
            tmp_path.unlink(missing_ok=True)
            return False
        tmp_path.replace(target_path)
        return True
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _extract_media_sync(source_url: str) -> dict[str, Any] | None:
    if YoutubeDL is None or not source_url:
        return None

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "extract_flat": False,
        "format": "best[ext=mp4][protocol!=m3u8_native]/best[ext=mp4]/best",
        "http_headers": DOWNLOAD_HEADERS,
    }

    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(source_url, download=False)

    if not isinstance(info, dict):
        return None

    if info.get("entries"):
        first_entry = next((entry for entry in info["entries"] if entry), None)
        if isinstance(first_entry, dict):
            info = first_entry

    media_url = str(info.get("url") or "").strip()
    ext = str(info.get("ext") or "").strip().lower()
    if not media_url:
        requested_formats = info.get("requested_formats") or []
        for fmt in requested_formats:
            if not isinstance(fmt, dict):
                continue
            candidate = str(fmt.get("url") or "").strip()
            if not candidate:
                continue
            media_url = candidate
            if not ext:
                ext = str(fmt.get("ext") or "").strip().lower()
            break

    if not media_url:
        return None

    is_probably_mp4 = ext == "mp4" or ".mp4" in media_url.lower()
    if not is_probably_mp4:
        return None

    duration = info.get("duration")
    return {
        "url": media_url,
        "duration": float(duration) if duration else None,
        "thumbnail": info.get("thumbnail"),
    }


async def resolve_clip_media(
    *,
    cache_key: str,
    source_url: str,
    cache_dir: Path | None = None,
) -> dict[str, Any] | None:
    if cache_key in _MEDIA_CACHE:
        return _MEDIA_CACHE[cache_key]

    cache_path = _cache_path_for(cache_dir, cache_key) if cache_dir else None
    if cache_path and cache_path.exists() and cache_path.stat().st_size > 0:
        payload = {"url": str(cache_path), "local_path": str(cache_path), "cached": True}
        _MEDIA_CACHE[cache_key] = payload
        return payload

    try:
        payload = await asyncio.wait_for(asyncio.to_thread(_extract_media_sync, source_url), timeout=18)
    except TimeoutError:
        LOGGER.warning("Timed out resolving direct clip media for %s", cache_key)
        payload = None
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("Failed to resolve direct clip media for %s: %s", cache_key, exc)
        payload = None

    if payload and payload.get("url") and cache_path:
        try:
            downloaded = await asyncio.wait_for(
                asyncio.to_thread(_download_media_sync, str(payload["url"]), cache_path),
                timeout=45,
            )
            if downloaded:
                payload = {
                    **payload,
                    "url": str(cache_path),
                    "remote_url": str(payload["url"]),
                    "local_path": str(cache_path),
                    "cached": True,
                }
        except TimeoutError:
            LOGGER.warning("Timed out caching clip media for %s", cache_key)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to cache clip media for %s: %s", cache_key, exc)

    _MEDIA_CACHE[cache_key] = payload
    return payload
