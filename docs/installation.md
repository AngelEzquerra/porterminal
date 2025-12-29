# Installation

## Quick Install

**One-liner (installs uv automatically if needed):**

```powershell
# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/lyehe/porterminal/main/install.ps1 | iex"
```

```bash
# macOS/Linux
curl -LsSf https://raw.githubusercontent.com/lyehe/porterminal/main/install.sh | sh
```

## Package Managers

**pip:**
```bash
pip install porterminal
```

**uv:**
```bash
uv tool install porterminal
```

**Run without installing:**
```bash
uvx porterminal
```

## From Source

```bash
git clone https://github.com/lyehe/porterminal.git
cd porterminal
uv sync
uv run porterminal
```

## Prerequisites

### Python
Python 3.12 or higher is required.

### cloudflared
The Cloudflare tunnel CLI is required for remote access. Porterminal will attempt to install it automatically on first run.

**Manual installation:**

```powershell
# Windows
winget install cloudflare.cloudflared
```

```bash
# macOS
brew install cloudflared
```

```bash
# Linux (Debian/Ubuntu)
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
```

## Verify Installation

```bash
porterminal --help
```

Expected output:
```
usage: porterminal [path] [options]

Arguments:
  path              Starting directory for the shell

Options:
  --no-tunnel       Start server only, without Cloudflare tunnel
  -v, --verbose     Show detailed startup logs
```
