"""Output buffering for WebSocket communication."""

import asyncio
import contextlib
import logging
from collections import deque

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Default ~120fps for responsive scrolling
DEFAULT_FLUSH_INTERVAL_MS = 8


class OutputBuffer:
    """Batch terminal output to reduce WebSocket messages."""

    def __init__(
        self,
        websocket: WebSocket,
        flush_interval_ms: int = DEFAULT_FLUSH_INTERVAL_MS,
    ) -> None:
        self.websocket = websocket
        self.buffer: deque[bytes] = deque()
        self.flush_interval = flush_interval_ms / 1000
        self._flush_task: asyncio.Task | None = None
        self._closed = False

    async def write(self, data: bytes) -> None:
        """Add data to the buffer."""
        if self._closed:
            return

        self.buffer.append(data)
        logger.debug(
            "OutputBuffer queued bytes=%d buffered_chunks=%d",
            len(data),
            len(self.buffer),
        )

        # Immediate flush for small interactive data (single chars, escape sequences)
        if len(data) <= 64:
            await self.flush()
            return

        if self._flush_task is None:
            self._flush_task = asyncio.create_task(self._delayed_flush())

    async def _delayed_flush(self) -> None:
        """Flush buffer after delay."""
        await asyncio.sleep(self.flush_interval)

        if self.buffer and not self._closed:
            combined = b"".join(self.buffer)
            self.buffer.clear()

            try:
                await self.websocket.send_bytes(combined)
                logger.debug("OutputBuffer flushed bytes=%d", len(combined))
            except (ConnectionError, RuntimeError) as e:
                logger.error(f"Failed to send buffered output: {e}")

        self._flush_task = None

    async def flush(self) -> None:
        """Immediately flush the buffer."""
        if self._flush_task:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task
            self._flush_task = None

        if self.buffer and not self._closed:
            combined = b"".join(self.buffer)
            self.buffer.clear()

            with contextlib.suppress(ConnectionError, RuntimeError):
                await self.websocket.send_bytes(combined)

    def close(self) -> None:
        """Close the buffer."""
        self._closed = True
        if self._flush_task:
            self._flush_task.cancel()
