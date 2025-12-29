"""
PTY management with cross-platform support.

This package provides:
- PTYBackend Protocol for platform-specific implementations
- SecurePTYManager for secure PTY management with env sanitization
- Platform-specific backends (Windows, Unix)
- FakePTYBackend for testing
"""

import os
import sys
from typing import TYPE_CHECKING

from .env import BLOCKED_ENV_VARS, SAFE_ENV_VARS, build_safe_environment
from .fake import FakePTYBackend
from .manager import SecurePTYManager
from .protocol import PTYBackend

if TYPE_CHECKING:
    from ..config import Config

__all__ = [
    # Protocol
    "PTYBackend",
    # Manager
    "SecurePTYManager",
    # Backends
    "FakePTYBackend",
    "create_backend",
    # Factory
    "create_pty",
    # Environment
    "SAFE_ENV_VARS",
    "BLOCKED_ENV_VARS",
    "build_safe_environment",
]


def create_backend() -> PTYBackend:
    """Create platform-appropriate PTY backend.

    Returns:
        PTYBackend instance for the current platform.

    Raises:
        RuntimeError: If no suitable backend is available.
    """
    if sys.platform == "win32":
        from .windows import WindowsPTYBackend

        return WindowsPTYBackend()
    else:
        from .unix import UnixPTYBackend

        return UnixPTYBackend()


def create_pty(
    shell_id: str | None = None,
    cols: int = 120,
    rows: int = 30,
    config: "Config | None" = None,
) -> SecurePTYManager:
    """Create a new PTY with the specified shell.

    Args:
        shell_id: Shell identifier, or None for default shell.
        cols: Initial terminal columns.
        rows: Initial terminal rows.
        config: Application configuration. If None, uses global config.

    Returns:
        Spawned SecurePTYManager instance.

    Raises:
        ValueError: If shell configuration is not found.
        FileNotFoundError: If shell command is not found.
    """
    # Import here to avoid circular imports
    from ..config import get_config

    cfg = config or get_config()

    # Get shell config
    if shell_id is None:
        shell_id = cfg.terminal.default_shell

    shell_config = cfg.terminal.get_shell(shell_id)
    if shell_config is None:
        # Try to find first available shell
        if cfg.terminal.shells:
            shell_config = cfg.terminal.shells[0]
        else:
            raise ValueError(f"No shell configuration found for: {shell_id}")

    # Get working directory from environment
    cwd = os.environ.get("PORTERMINAL_CWD")

    # Create backend and manager
    backend = create_backend()
    pty_manager = SecurePTYManager(backend, shell_config, cols, rows, cwd=cwd)
    pty_manager.spawn()

    return pty_manager
