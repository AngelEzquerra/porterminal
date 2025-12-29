"""Session registry with reconnection support."""

import asyncio
import contextlib
import logging
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .pty import SecurePTYManager, create_pty

if TYPE_CHECKING:
    from fastapi import WebSocket

    from .config import Config

logger = logging.getLogger(__name__)

# Type alias for PTY factory function
PTYFactory = Callable[[str | None, int, int], SecurePTYManager]

# Session limits
MAX_SESSIONS_PER_USER = 10  # Increased for multi-tab support
MAX_TOTAL_SESSIONS = 100
RECONNECT_WINDOW_SECONDS = 0  # 0 = no limit, session lives while PTY is alive
SESSION_MAX_DURATION_SECONDS = 0  # 0 = no limit, session lives while PTY is alive
OUTPUT_BUFFER_MAX_BYTES = 1_000_000  # 1MB buffer for reconnection


@dataclass
class TerminalSession:
    """Persistent terminal session that survives disconnects."""

    session_id: str
    user_id: str
    pty_manager: SecurePTYManager
    shell_id: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_activity: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Output buffer for reconnection (deque of bytes)
    output_buffer: deque[bytes] = field(default_factory=deque)
    output_buffer_size: int = 0

    # Connection state
    websocket: "WebSocket | None" = None
    is_connected: bool = False

    def add_to_buffer(self, data: bytes) -> None:
        """Add data to the output buffer for reconnection."""
        # Clear buffer when terminal clears screen (ED2 = Erase Display mode 2)
        # This prevents replaying stale cursor-positioning sequences from TUI apps
        # Common sequences: \x1b[2J (clear), \x1b[H\x1b[2J (home + clear)
        if b"\x1b[2J" in data:
            self.output_buffer.clear()
            self.output_buffer_size = 0

        self.output_buffer.append(data)
        self.output_buffer_size += len(data)

        # Trim buffer if too large
        while self.output_buffer_size > OUTPUT_BUFFER_MAX_BYTES and self.output_buffer:
            removed = self.output_buffer.popleft()
            self.output_buffer_size -= len(removed)

    def get_buffered_output(self) -> bytes:
        """Get all buffered output."""
        return b"".join(self.output_buffer)

    def clear_buffer(self) -> None:
        """Clear the output buffer."""
        self.output_buffer.clear()
        self.output_buffer_size = 0

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.now(UTC)


class SessionRegistry:
    """Manage terminal sessions with reconnection support."""

    def __init__(
        self,
        config: "Config | None" = None,
        pty_factory: PTYFactory | None = None,
    ):
        """
        Initialize the session registry.

        Args:
            config: Application configuration. If None, uses global config.
            pty_factory: Factory function to create PTY managers. If None, uses default.
        """
        self._config = config
        self._pty_factory = pty_factory or create_pty
        self._sessions: dict[str, TerminalSession] = {}
        self._user_sessions: dict[str, set[str]] = {}  # user_id -> session_ids
        self._cleanup_task: asyncio.Task[None] | None = None
        self._running = False

    async def start(self) -> None:
        """Start the session registry background tasks."""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info("Session registry started")

    async def stop(self) -> None:
        """Stop the session registry and close all sessions."""
        self._running = False

        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task

        # Close all sessions
        for session_id in list(self._sessions.keys()):
            await self.destroy_session(session_id)

        logger.info("Session registry stopped")

    async def _cleanup_loop(self) -> None:
        """Periodically clean up stale sessions."""
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                await self._cleanup_stale_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup loop error: {e}")

    async def _cleanup_stale_sessions(self) -> None:
        """Clean up sessions that have exceeded their limits."""
        now = datetime.now(UTC)

        for session_id, session in list(self._sessions.items()):
            # Check if PTY is still alive (primary check - session dies with PTY)
            if not session.pty_manager.is_alive():
                logger.info(f"Session {session_id} PTY died")
                await self.destroy_session(session_id)
                continue

            # Check max session duration (0 = no limit)
            if SESSION_MAX_DURATION_SECONDS > 0:
                session_age = (now - session.created_at).total_seconds()
                if session_age > SESSION_MAX_DURATION_SECONDS:
                    logger.info(f"Session {session_id} exceeded max duration")
                    await self.destroy_session(session_id)
                    continue

            # Check disconnected sessions past reconnection window (0 = no limit)
            if RECONNECT_WINDOW_SECONDS > 0 and not session.is_connected:
                idle_time = (now - session.last_activity).total_seconds()
                if idle_time > RECONNECT_WINDOW_SECONDS:
                    logger.info(f"Session {session_id} reconnection window expired")
                    await self.destroy_session(session_id)
                    continue

    async def create_session(
        self,
        user_id: str,
        shell_id: str | None = None,
        cols: int = 120,
        rows: int = 30,
    ) -> TerminalSession:
        """Create a new terminal session."""
        # Check limits
        user_sessions = self._user_sessions.get(user_id, set())
        logger.debug(
            "Create session requested user_id=%s existing_user_sessions=%d total_sessions=%d",
            user_id,
            len(user_sessions),
            len(self._sessions),
        )
        if len(user_sessions) >= MAX_SESSIONS_PER_USER:
            raise ValueError(f"Maximum sessions ({MAX_SESSIONS_PER_USER}) reached for user")

        if len(self._sessions) >= MAX_TOTAL_SESSIONS:
            raise ValueError("Server session limit reached")

        # Create PTY using injected factory
        pty_manager = self._pty_factory(shell_id, cols, rows)

        # Create session
        session_id = str(uuid.uuid4())
        session = TerminalSession(
            session_id=session_id,
            user_id=user_id,
            pty_manager=pty_manager,
            shell_id=shell_id or "default",
        )

        # Register session
        self._sessions[session_id] = session
        self._user_sessions.setdefault(user_id, set()).add(session_id)

        logger.info(
            "Created session session_id=%s user_id=%s shell_id=%s total_sessions=%d",
            session_id,
            user_id,
            session.shell_id,
            len(self._sessions),
        )
        return session

    def get_session(self, session_id: str) -> TerminalSession | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    async def reconnect_session(
        self,
        session_id: str,
        user_id: str,
        websocket: "WebSocket",
    ) -> TerminalSession | None:
        """Reconnect to an existing session."""
        session = self._sessions.get(session_id)

        if not session:
            logger.warning(
                "Reconnect failed session not found session_id=%s user_id=%s", session_id, user_id
            )
            return None

        # Verify ownership
        if session.user_id != user_id:
            logger.warning(
                "Reconnect failed ownership mismatch session_id=%s expected_user_id=%s got_user_id=%s",
                session_id,
                session.user_id,
                user_id,
            )
            return None

        # Check if PTY is still alive
        if not session.pty_manager.is_alive():
            logger.warning(
                "Reconnect failed PTY dead session_id=%s user_id=%s", session_id, user_id
            )
            await self.destroy_session(session_id)
            return None

        # Close old WebSocket if still connected
        if session.websocket and session.is_connected:
            with contextlib.suppress(Exception):
                await session.websocket.close(code=4000, reason="Reconnected from another client")

        # Update session
        session.websocket = websocket
        session.is_connected = True
        session.touch()

        logger.info(
            "Reconnected session session_id=%s user_id=%s buffered_output_bytes=%d",
            session_id,
            user_id,
            session.output_buffer_size,
        )
        return session

    async def disconnect_session(self, session_id: str) -> None:
        """Mark a session as disconnected (but keep it alive for reconnection)."""
        session = self._sessions.get(session_id)
        if session:
            session.websocket = None
            session.is_connected = False
            session.touch()
            logger.info(
                "Disconnected session session_id=%s user_id=%s buffered_output_bytes=%d",
                session_id,
                session.user_id,
                session.output_buffer_size,
            )

    async def destroy_session(self, session_id: str) -> None:
        """Destroy a session and clean up resources."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return

        # Remove from user sessions
        user_sessions = self._user_sessions.get(session.user_id, set())
        user_sessions.discard(session_id)
        if not user_sessions:
            self._user_sessions.pop(session.user_id, None)

        # Close WebSocket
        if session.websocket:
            with contextlib.suppress(Exception):
                await session.websocket.close()

        # Close PTY
        session.pty_manager.close()

        logger.info(
            "Destroyed session session_id=%s user_id=%s remaining_total_sessions=%d",
            session_id,
            session.user_id,
            len(self._sessions),
        )

    def get_user_sessions(self, user_id: str) -> list[TerminalSession]:
        """Get all sessions for a user."""
        session_ids = self._user_sessions.get(user_id, set())
        return [self._sessions[sid] for sid in session_ids if sid in self._sessions]

    @property
    def session_count(self) -> int:
        """Get the total number of active sessions."""
        return len(self._sessions)


# Global session registry instance
_registry: SessionRegistry | None = None


def get_session_registry() -> SessionRegistry:
    """Get the global session registry."""
    global _registry
    if _registry is None:
        _registry = SessionRegistry()
    return _registry
