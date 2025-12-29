"""WebSocket message handler registry."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import WebSocket

from ..session import TerminalSession
from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Type alias for message handlers
MessageHandler = Callable[["MessageContext", dict[str, Any]], Awaitable[None]]


@dataclass
class MessageContext:
    """Context passed to message handlers."""

    websocket: WebSocket
    session: TerminalSession
    rate_limiter: RateLimiter
    max_input_size: int
    log_raw_input: bool


class MessageRegistry:
    """Registry for WebSocket message handlers.

    Follows Open/Closed Principle - open for extension (new handlers),
    closed for modification (no if/elif chains).
    """

    def __init__(self) -> None:
        self._handlers: dict[str, MessageHandler] = {}

    def register(self, msg_type: str) -> Callable[[MessageHandler], MessageHandler]:
        """Decorator to register a handler for a message type.

        Args:
            msg_type: The message type to handle (e.g., "resize", "input").

        Returns:
            Decorator function.
        """

        def decorator(handler: MessageHandler) -> MessageHandler:
            self._handlers[msg_type] = handler
            return handler

        return decorator

    async def dispatch(self, ctx: MessageContext, msg: dict[str, Any]) -> None:
        """Dispatch a message to its handler.

        Args:
            ctx: Message context with websocket, session, etc.
            msg: The JSON message dict.
        """
        msg_type = msg.get("type")
        handler = self._handlers.get(msg_type)

        if handler:
            await handler(ctx, msg)
        else:
            logger.warning(
                "Unknown message type session_id=%s type=%s",
                ctx.session.session_id,
                msg_type,
            )


# Global registry instance
message_registry = MessageRegistry()


@message_registry.register("resize")
async def handle_resize(ctx: MessageContext, msg: dict[str, Any]) -> None:
    """Handle terminal resize message."""
    cols = int(msg.get("cols", 120))
    rows = int(msg.get("rows", 30))

    # Deduplicate: skip if same as current dimensions
    if ctx.session.pty_manager.cols == cols and ctx.session.pty_manager.rows == rows:
        logger.debug(
            "Resize skipped (no change) session_id=%s cols=%d rows=%d",
            ctx.session.session_id,
            cols,
            rows,
        )
    else:
        logger.info(
            "Resize session_id=%s cols=%d rows=%d",
            ctx.session.session_id,
            cols,
            rows,
        )
        ctx.session.pty_manager.resize(cols, rows)

    ctx.session.touch()


@message_registry.register("pong")
async def handle_pong(ctx: MessageContext, msg: dict[str, Any]) -> None:
    """Handle pong heartbeat response."""
    ctx.session.touch()


@message_registry.register("input")
async def handle_input(ctx: MessageContext, msg: dict[str, Any]) -> None:
    """Handle JSON-encoded terminal input."""
    data = msg.get("data", "")

    # Size validation for JSON input
    if len(data) > ctx.max_input_size:
        await ctx.websocket.send_json(
            {
                "type": "error",
                "message": "Input too large",
            }
        )
        logger.warning(
            "JSON input too large session_id=%s chars=%d limit=%d",
            ctx.session.session_id,
            len(data),
            ctx.max_input_size,
        )
        return

    if ctx.log_raw_input:
        logger.info(
            "Received input JSON session_id=%s data=%r",
            ctx.session.session_id,
            data,
        )
    else:
        logger.debug(
            "Received input JSON session_id=%s chars=%d",
            ctx.session.session_id,
            len(data),
        )

    if data:
        input_bytes = data.encode("utf-8")
        if await ctx.rate_limiter.acquire(len(input_bytes)):
            logger.debug(
                "Writing to PTY session_id=%s bytes=%d",
                ctx.session.session_id,
                len(input_bytes),
            )
            ctx.session.pty_manager.write(input_bytes)
            ctx.session.touch()
        else:
            logger.warning(
                "Rate limit blocked input session_id=%s bytes=%d",
                ctx.session.session_id,
                len(input_bytes),
            )
