"""
ws.py — WebSocket менеджер для Nexus.

Каждый подключённый клиент регистрируется по user_id.
Сообщения рассылаются всем участникам нужного чата.
"""

import json
import logging
from typing import Dict, Set
from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # user_id -> set of WebSocket (один юзер может открыть несколько вкладок)
        self._connections: Dict[int, Set[WebSocket]] = {}

    # ── подключение ──────────────────────────────────────────────────────────
    async def connect(self, ws: WebSocket, user_id: int):
        await ws.accept()
        self._connections.setdefault(user_id, set()).add(ws)
        logger.info(f"WS connected: user_id={user_id}  total={self.total}")

    def disconnect(self, ws: WebSocket, user_id: int):
        conns = self._connections.get(user_id, set())
        conns.discard(ws)
        if not conns:
            self._connections.pop(user_id, None)
        logger.info(f"WS disconnected: user_id={user_id}  total={self.total}")

    @property
    def total(self) -> int:
        return sum(len(v) for v in self._connections.values())

    def online_ids(self) -> Set[int]:
        return set(self._connections.keys())

    # ── отправка ─────────────────────────────────────────────────────────────
    async def send(self, user_id: int, payload: dict):
        """Отправить JSON одному пользователю (все его вкладки)."""
        for ws in list(self._connections.get(user_id, [])):
            try:
                await ws.send_text(json.dumps(payload, ensure_ascii=False))
            except Exception:
                self.disconnect(ws, user_id)

    async def broadcast_chat(self, member_ids: list[int], payload: dict, exclude: int | None = None):
        """Разослать всем участникам чата, кроме exclude (обычно отправителя)."""
        for uid in member_ids:
            if uid == exclude:
                continue
            await self.send(uid, payload)

    async def broadcast_all(self, payload: dict):
        """Разослать всем подключённым."""
        for uid in list(self._connections.keys()):
            await self.send(uid, payload)


# Глобальный синглтон — импортируется везде
manager = ConnectionManager()
