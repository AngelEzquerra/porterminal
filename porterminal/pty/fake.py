"""Fake PTY backend for testing - no real process spawned."""

import logging
from collections import deque

logger = logging.getLogger(__name__)


class FakePTYBackend:
    """Fake PTY for testing - no real process spawned.

    This backend simulates PTY behavior for unit testing:
    - Records all input written to the PTY
    - Allows injecting output to be read
    - Tracks spawn/resize/close calls
    """

    def __init__(self) -> None:
        self._input_buffer: bytes = b""
        self._output_queue: deque[bytes] = deque()
        self._alive: bool = False
        self._rows: int = 30
        self._cols: int = 120
        self._spawn_count: int = 0
        self._resize_count: int = 0
        self._last_cmd: list[str] = []
        self._last_env: dict[str, str] = {}
        self._last_cwd: str | None = None

    @property
    def rows(self) -> int:
        """Current number of rows."""
        return self._rows

    @property
    def cols(self) -> int:
        """Current number of columns."""
        return self._cols

    def spawn(
        self,
        cmd: list[str],
        env: dict[str, str],
        cwd: str | None,
        rows: int,
        cols: int,
    ) -> None:
        """Simulate spawning a PTY process."""
        if self._alive:
            raise RuntimeError("PTY already spawned")

        self._rows = rows
        self._cols = cols
        self._alive = True
        self._spawn_count += 1
        self._last_cmd = cmd
        self._last_env = env
        self._last_cwd = cwd
        logger.debug("Fake PTY spawned cmd=%s", cmd)

    def read(self, size: int = 4096) -> bytes:
        """Read from the fake PTY output queue."""
        if not self._alive or not self._output_queue:
            return b""

        data = self._output_queue.popleft()
        if len(data) > size:
            # Put remainder back
            self._output_queue.appendleft(data[size:])
            data = data[:size]

        return data

    def write(self, data: bytes) -> None:
        """Record input written to the fake PTY."""
        if not self._alive:
            return
        self._input_buffer += data

    def resize(self, rows: int, cols: int) -> None:
        """Simulate resizing the PTY."""
        self._rows = rows
        self._cols = cols
        self._resize_count += 1

    def is_alive(self) -> bool:
        """Check if the fake PTY is 'alive'."""
        return self._alive

    def close(self) -> None:
        """Close the fake PTY."""
        self._alive = False
        logger.debug("Fake PTY closed")

    # Test helper methods

    def inject_output(self, data: bytes) -> None:
        """Test helper: inject output data to be read.

        Args:
            data: Bytes to add to the output queue.
        """
        self._output_queue.append(data)

    def get_input(self) -> bytes:
        """Test helper: get all input written to PTY.

        Returns:
            All bytes written since last call.
        """
        result = self._input_buffer
        self._input_buffer = b""
        return result

    def kill(self) -> None:
        """Test helper: simulate process death."""
        self._alive = False
