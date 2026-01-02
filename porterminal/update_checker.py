"""Auto-update checker for ptn."""

import json
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path

# Check once per day
CHECK_INTERVAL = 86400
CACHE_FILE = Path.home() / ".ptn" / "update_check.json"
PYPI_URL = "https://pypi.org/pypi/ptn/json"


def _get_current_version() -> str:
    """Get currently installed version."""
    try:
        from porterminal._version import __version__

        return __version__
    except ImportError:
        return "0.0.0"


def _get_latest_version() -> str | None:
    """Fetch latest version from PyPI."""
    try:
        req = urllib.request.Request(PYPI_URL, headers={"User-Agent": "ptn"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            return data["info"]["version"]
    except Exception:
        return None


def _should_check() -> bool:
    """Check if enough time has passed since last check."""
    if not CACHE_FILE.exists():
        return True
    try:
        data = json.loads(CACHE_FILE.read_text())
        return time.time() - data.get("last_check", 0) > CHECK_INTERVAL
    except Exception:
        return True


def _save_check_time() -> None:
    """Save current time as last check."""
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"last_check": time.time()}))
    except Exception:
        pass


def _compare_versions(current: str, latest: str) -> bool:
    """Return True if latest > current."""
    try:
        from packaging.version import Version

        return Version(latest) > Version(current)
    except Exception:
        # Fallback: simple string comparison
        return latest != current and latest > current


def _is_uvx() -> bool:
    """Check if running via uvx."""
    # uvx sets this, or we can check if uvx is available
    return shutil.which("uvx") is not None


def check_and_update() -> None:
    """Check for updates and auto-update if available.

    Call this at the very beginning of main(), before any other setup.
    If an update is found, this function will not return - it replaces
    the current process with an updated version.
    """
    if not _should_check():
        return

    current = _get_current_version()
    latest = _get_latest_version()
    _save_check_time()

    if not latest:
        return

    if not _compare_versions(current, latest):
        return

    # Update available
    if not _is_uvx():
        # Not using uvx, just notify
        print(f"\n📦 Update available: ptn {current} → {latest}")
        print("   Run: pip install -U ptn\n")
        return

    # Auto-update via uvx
    print(f"🔄 Updating ptn {current} → {latest}...")

    # Build new command
    args = ["uvx", "--refresh", "ptn"] + sys.argv[1:]

    if sys.platform == "win32":
        # Windows: can't use execvp, use subprocess and exit
        import subprocess

        result = subprocess.call(args)
        sys.exit(result)
    else:
        # Unix: replace current process
        os.execvp("uvx", args)
