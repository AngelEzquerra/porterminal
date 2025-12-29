"""WebSocket session handler with output batching and flow control."""

import asyncio
import contextlib
import json
import logging
import os

from fastapi import WebSocket, WebSocketDisconnect

from ..session import SessionRegistry, TerminalSession
from .buffer import OutputBuffer
from .handlers import MessageContext, message_registry
from .rate_limiter import RateLimiter

logger = logging.getLogger(__name__)

# Constants
HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_TIMEOUT_SECONDS = 300  # 5 minutes - allow time for reading output
MAX_INPUT_SIZE = 4096
LOG_RAW_INPUT = os.environ.get("PORTERMINAL_LOG_RAW_INPUT", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


async def handle_terminal_session(
    websocket: WebSocket,
    session: TerminalSession,
    registry: SessionRegistry,
    *,
    skip_buffer: bool = False,
) -> None:
    """Handle WebSocket communication for a terminal session.

    Args:
        websocket: WebSocket connection.
        session: Terminal session.
        registry: Session registry for disconnect handling.
        skip_buffer: Skip replaying buffered output (for auto-reconnects where
            client terminal already has the content).
    """
    output_buffer = OutputBuffer(websocket)
    rate_limiter = RateLimiter()

    # Create message context for handlers
    ctx = MessageContext(
        websocket=websocket,
        session=session,
        rate_limiter=rate_limiter,
        max_input_size=MAX_INPUT_SIZE,
        log_raw_input=LOG_RAW_INPUT,
    )

    logger.info(
        "WebSocket session start session_id=%s user_id=%s shell_id=%s",
        session.session_id,
        session.user_id,
        session.shell_id,
    )

    # Tasks
    reader_task: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None

    async def read_pty_output() -> None:
        """Read from PTY and send to WebSocket."""
        while session.is_connected:
            try:
                if session.pty_manager.is_alive():
                    data = session.pty_manager.read(4096)
                    if data:
                        # Buffer for reconnection
                        session.add_to_buffer(data)
                        # Send to client
                        await output_buffer.write(data)
                        session.touch()

                await asyncio.sleep(0.008)  # 8ms polling (~120Hz, balances latency vs CPU)

            except OSError as e:
                logger.error(f"PTY read error: {e}")
                break

    async def heartbeat_loop() -> None:
        """Send periodic heartbeats."""
        while session.is_connected:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
                if session.is_connected:
                    await websocket.send_json({"type": "ping"})
            except (ConnectionError, RuntimeError):
                break

    try:
        # Send session info
        await websocket.send_json(
            {
                "type": "session_info",
                "session_id": session.session_id,
                "shell_id": session.shell_id,
            }
        )
        logger.debug("Sent session_info session_id=%s", session.session_id)

        # Send buffered output if reconnecting (unless client already has it)
        if not skip_buffer:
            buffered = session.get_buffered_output()
            if buffered:
                logger.info(
                    "Replaying buffered output session_id=%s bytes=%d",
                    session.session_id,
                    len(buffered),
                )
                # Clear screen before replay to avoid visual artifacts from stale
                # cursor-positioning sequences (failsafe for TUI apps)
                await websocket.send_bytes(b"\x1b[2J\x1b[H")
                await websocket.send_bytes(buffered)
        else:
            logger.info(
                "Skipping buffer replay session_id=%s (client requested skip)",
                session.session_id,
            )

        # Start background tasks
        reader_task = asyncio.create_task(read_pty_output())
        heartbeat_task = asyncio.create_task(heartbeat_loop())

        # Main message loop
        while True:
            try:
                # Receive with timeout
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=HEARTBEAT_TIMEOUT_SECONDS,
                )

                message_type = message.get("type")
                logger.debug(
                    "WebSocket message received session_id=%s type=%s keys=%s",
                    session.session_id,
                    message_type,
                    list(message.keys()),
                )
                if message_type == "websocket.disconnect":
                    logger.info(f"Session {session.session_id} client disconnected (message)")
                    break

                input_bytes = message.get("bytes")
                text_message = message.get("text")

                if input_bytes is not None:
                    # Terminal input (binary)
                    await _handle_binary_input(ctx, input_bytes)

                elif text_message is not None:
                    # JSON control message - dispatch to registry
                    try:
                        msg = json.loads(text_message)
                        logger.debug(
                            "Received JSON message session_id=%s type=%s",
                            session.session_id,
                            msg.get("type"),
                        )
                        await message_registry.dispatch(ctx, msg)
                    except json.JSONDecodeError as e:
                        logger.error(f"JSON decode error: {e}")

            except TimeoutError:
                # Heartbeat timeout
                logger.warning(f"Session {session.session_id} heartbeat timeout")
                break

            except WebSocketDisconnect:
                logger.info(f"Session {session.session_id} client disconnected")
                break

    except (ConnectionError, RuntimeError) as e:
        logger.exception(f"WebSocket error for session {session.session_id}: {e}")

    finally:
        logger.info("WebSocket session end session_id=%s", session.session_id)
        # Clean up
        output_buffer.close()

        for task in [reader_task, heartbeat_task]:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        # Mark session as disconnected (not destroyed - allow reconnection)
        await registry.disconnect_session(session.session_id)


async def _handle_binary_input(ctx: MessageContext, input_bytes: bytes) -> None:
    """Handle binary terminal input.

    Args:
        ctx: Message context.
        input_bytes: Raw terminal input bytes.
    """
    if ctx.log_raw_input:
        logger.info(
            "Received input bytes session_id=%s data=%r",
            ctx.session.session_id,
            input_bytes,
        )
    else:
        logger.debug(
            "Received input bytes session_id=%s bytes=%d",
            ctx.session.session_id,
            len(input_bytes),
        )

    # Size validation
    if len(input_bytes) > ctx.max_input_size:
        await ctx.websocket.send_json(
            {
                "type": "error",
                "message": "Input too large",
            }
        )
        logger.warning(
            "Input too large session_id=%s bytes=%d limit=%d",
            ctx.session.session_id,
            len(input_bytes),
            ctx.max_input_size,
        )
        return

    # Rate limiting
    if not await ctx.rate_limiter.acquire(len(input_bytes)):
        await ctx.websocket.send_json(
            {
                "type": "error",
                "message": "Rate limit exceeded",
            }
        )
        logger.warning(
            "Rate limit exceeded session_id=%s bytes=%d",
            ctx.session.session_id,
            len(input_bytes),
        )
        return

    # Write to PTY
    logger.debug(
        "Writing to PTY session_id=%s bytes=%d",
        ctx.session.session_id,
        len(input_bytes),
    )
    ctx.session.pty_manager.write(input_bytes)
    ctx.session.touch()
