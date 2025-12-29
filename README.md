```
    ____             __                      _             __
   / __ \____  _____/ /____  _________ ___  (_)___  ____ _/ /
  / /_/ / __ \/ ___/ __/ _ \/ ___/ __ `__ \/ / __ \/ __ `/ /
 / ____/ /_/ / /  / /_/  __/ /  / / / / / / / / / / /_/ / /
/_/    \____/_/   \__/\___/_/  /_/ /_/ /_/_/_/ /_/\__,_/_/
```

Web-based terminal accessible from your phone via Cloudflare Quick Tunnel. Code from anywhere with a touch-friendly interface.

## Features

- **Mobile-optimized UI** - Touch-friendly virtual keyboard with modifier keys (Ctrl, Alt)
- **Multi-tab support** - Run multiple terminal sessions simultaneously
- **Session persistence** - Reconnect to running sessions after disconnect
- **Secure by default** - Environment variables sanitized, API keys blocked
- **Zero configuration tunnel** - Cloudflare Quick Tunnel with QR code for instant access
- **Cross-platform** - Windows (pywinpty), Linux/macOS (pty)
- **Auto shell detection** - Finds PowerShell, CMD, WSL, Bash automatically

## Quick Start

**One-liner install (installs uv automatically if needed):**

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/lyehe/porterminal/main/install.ps1 | iex"
```

```bash
# macOS/Linux
curl -LsSf https://raw.githubusercontent.com/lyehe/porterminal/main/install.sh | sh
```

Then run:
```bash
porterminal
```

Scan the QR code with your phone to access the terminal.

**Or install with pip/uv:**

```bash
pip install porterminal
# or
uv tool install porterminal
```

**Or run without installing:**

```bash
uvx porterminal
```

## Installation

### Prerequisites

- Python 3.12+
- [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) (auto-installed if missing)

### Install from source

```bash
git clone https://github.com/lyehe/porterminal.git
cd porterminal
uv sync
uv run porterminal
```

## Usage

### Start with Cloudflare tunnel (recommended)

```bash
porterminal
```

This will:
1. Start the FastAPI server on localhost
2. Create a Cloudflare Quick Tunnel
3. Display a QR code for mobile access

### Start without tunnel (local network only)

```bash
porterminal --no-tunnel
```

Or run uvicorn directly:

```bash
uv run uvicorn porterminal.app:app --host 0.0.0.0 --port 8000
```

### Command-line options

```
porterminal [path] [options]

Arguments:
  path              Starting directory for the shell (default: current directory)

Options:
  --no-tunnel       Start server only, without Cloudflare tunnel
  -v, --verbose     Show detailed startup logs
  -U, --update      Update to the latest version
  --check-update    Check if a newer version is available
  -V, --version     Show version number
```

### Updating

```bash
# Check for updates
porterminal --check-update

# Update to latest version
porterminal --update
```

## Configuration

Edit `config.yaml` to customize:

```yaml
server:
  host: "127.0.0.1"
  port: 8000

terminal:
  cols: 120
  rows: 30
  default_shell: powershell  # or cmd, wsl, bash

# Custom buttons for the virtual keyboard
buttons:
  - label: "git"
    send: "git status\r"
```

## Architecture

```
porterminal/
  __init__.py      Entry point: server + tunnel + QR code
  app.py           FastAPI application with WebSocket endpoint
  session.py       Session registry with reconnection support
  config.py        Configuration loading
  websocket/       WebSocket handler with output batching
  pty/             PTY management (Windows/Unix)
  cli/             Command-line interface
  infrastructure/  Server and tunnel management
  static/
    index.html     Mobile-optimized terminal UI
    app.js         Terminal client with xterm.js
    style.css      VSCode-inspired styling
```

## Security

- Environment variables are sanitized before passing to shell
- API keys and secrets (AWS, GitHub, OpenAI, etc.) are blocked
- Sessions are isolated per user (via Cloudflare Access email)
- Rate limiting on WebSocket input
- Admin privilege warning on Windows

## License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.
