# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Porterminal is a web-based terminal accessible from mobile devices via Cloudflare Quick Tunnel. It provides a touch-friendly interface with virtual buttons for special keys and modifiers.

## Commands

**Install from PyPI:**
```bash
pip install porterminal
# or
uv tool install porterminal
```

**Run (after install):**
```bash
porterminal
```

**Run without installing:**
```bash
uvx porterminal
```

**Development:**
```bash
uv sync
uv run porterminal
```

**Build for PyPI:**
```bash
uv build
uv publish
```

## Architecture

### Backend (Python/FastAPI)
- `porterminal/__init__.py` - Entry point: starts uvicorn server, establishes Cloudflare tunnel, displays QR code
- `porterminal/app.py` - FastAPI app with WebSocket endpoint at `/ws`, serves static files, exposes `/api/config`
- `porterminal/session.py` - Session registry with unlimited reconnection window, max 10 sessions per user
- `porterminal/config.py` - Pydantic config loaded from `config.yaml`
- `porterminal/websocket/` - WebSocket handling package:
  - `handler.py` - Main WebSocket handler with heartbeat
  - `handlers.py` - Message type handlers (resize, input, ping)
  - `buffer.py` - Output batching (immediate for <64 bytes, delayed for larger)
  - `rate_limiter.py` - Token bucket rate limiting (100/sec, 500 burst)
- `porterminal/pty/` - PTY management package:
  - `manager.py` - Secure PTY manager with environment sanitization
  - `windows.py` - Windows backend using pywinpty
  - `unix.py` - Unix backend using pty module
  - `env.py` - Environment sanitization (blocks API keys, secrets)
  - `protocol.py` - PTY backend protocol definition
- `porterminal/cli/` - CLI display utilities
- `porterminal/infrastructure/` - Server and Cloudflare tunnel management

### Frontend (porterminal/static/)
- `index.html` - Mobile-optimized layout with xterm.js (CDN with SRI), virtual keyboard rows
- `app.js` - Terminal client with WebSocket reconnection, modifier key state machine (off/sticky/locked via double-tap)
- `style.css` - Mobile-first styling
- `sw.js` - Service worker for offline caching

### Data Flow
1. Client connects via WebSocket to `/ws?session_id=<id>&shell=<id>&skip_buffer=<bool>`
2. Server spawns PTY with sanitized environment
3. Binary data (terminal I/O) and JSON messages (resize, ping/pong, session_info) flow over WebSocket
4. Output batched: immediate flush for interactive data (<64 bytes), delayed for bulk output

### Configuration
`config.yaml` defines:
- Server host/port (default: 127.0.0.1:8000)
- Available shells (auto-detected, or manually configured)
- Custom buttons with send sequences
- Terminal dimensions (cols: 40-500, rows: 10-200)
- Cloudflare Access integration (team_domain, access_aud)

## Key Constraints
- Cross-platform: Windows (pywinpty), Linux/macOS (pty module)
- Requires `cloudflared` CLI for tunnel functionality (auto-installed if missing)
- Sessions persist as long as PTY is alive (no timeout)
