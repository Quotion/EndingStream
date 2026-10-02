from __future__ import annotations

import logging
from urllib.parse import urlparse

from .config import Settings


class OBSControlError(RuntimeError):
    pass


class OBSController:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def stop_stream(self) -> None:
        if not self.settings.has_obs_config:
            raise OBSControlError("OBS websocket не настроен")

        parsed = urlparse(self.settings.obs_ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 4455

        try:
            from obsws_python import ReqClient
        except ImportError as exc:  # pragma: no cover - зависит от окружения пользователя
            raise OBSControlError(
                "Не установлен пакет obsws-python. Установите зависимости из requirements.txt"
            ) from exc

        logging.getLogger("obsws_python").setLevel(logging.CRITICAL)
        logging.getLogger("obsws_python.baseclient").setLevel(logging.CRITICAL)

        try:
            client = ReqClient(
                host=host,
                port=port,
                password=self.settings.obs_ws_password or "",
                timeout=3,
            )
            client.stop_stream()
        except Exception as exc:  # noqa: BLE001
            raise OBSControlError(f"Не удалось остановить OBS stream: {exc}") from exc
