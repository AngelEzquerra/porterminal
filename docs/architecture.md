# Architecture

## Project Structure

```
porterminal/
├── __init__.py           # Entry point: server + tunnel + QR code
├── app.py                # FastAPI application with WebSocket endpoint
├── session.py            # Session registry with reconnection support
├── config.py             # Configuration loading (Pydantic)
├── logging_setup.py      # Logging configuration
├── updater.py            # Version update checking
├── websocket/
│   ├── handler.py        # Main WebSocket handler with heartbeat
│   ├── handlers.py       # Message type handlers (resize, input, ping)
│   ├── buffer.py         # Output batching for efficient transfer
│   └── rate_limiter.py   # Token bucket rate limiting
├── pty/
│   ├── manager.py        # Secure PTY manager
│   ├── windows.py        # Windows backend (pywinpty)
│   ├── unix.py           # Unix backend (pty module)
│   ├── env.py            # Environment sanitization
│   └── protocol.py       # PTY backend protocol
├── cli/
│   ├── args.py           # Argument parsing
│   └── display.py        # Startup screen display
├── infrastructure/
│   ├── server.py         # Uvicorn server management
│   ├── network.py        # Port/IP utilities
│   └── cloudflared.py    # Tunnel management
└── static/
    ├── index.html        # Mobile-optimized terminal UI
    ├── app.js            # Terminal client (xterm.js)
    ├── style.css         # VSCode-inspired styling
    └── sw.js             # Service worker for offline caching
```

## Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│                         Client                               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐      │
│  │   xterm.js  │◀──▶│  WebSocket  │◀──▶│   app.js    │      │
│  └─────────────┘    └─────────────┘    └─────────────┘      │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                    Cloudflare Tunnel                         │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                         Server                               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐      │
│  │   FastAPI   │◀──▶│   Session   │◀──▶│     PTY     │      │
│  │  WebSocket  │    │   Manager   │    │   Backend   │      │
│  └─────────────┘    └─────────────┘    └─────────────┘      │
└──────────────────────────────────────────────────────────────┘
```

## WebSocket Protocol

### Connection
```
GET /ws?session_id=<id>&shell=<id>&skip_buffer=<bool>
```

| Parameter | Description |
|-----------|-------------|
| `session_id` | Unique session identifier |
| `shell` | Shell type (powershell, bash, etc.) |
| `skip_buffer` | Skip buffered output on reconnect |

### Message Types

**Binary messages**: Raw terminal I/O (input/output bytes)

**JSON messages**:
```json
{"type": "resize", "cols": 120, "rows": 30}
{"type": "ping"}
{"type": "pong"}
{"type": "session_info", "session_id": "...", "shell": "..."}
```

## Output Batching

The buffer system optimizes data transfer:

| Data Size | Behavior |
|-----------|----------|
| < 64 bytes | Immediate flush (interactive) |
| >= 64 bytes | Delayed batching (bulk output) |

## Rate Limiting

Token bucket algorithm:
- **Rate**: 100 messages/second
- **Burst**: 500 messages

## Session Management

- Sessions persist as long as PTY is alive (no timeout)
- Unlimited reconnection window
- Maximum 10 sessions per user (with Cloudflare Access)
- Session output buffered for reconnection

## Security Model

### Environment Sanitization

Blocked patterns:
- `*_KEY`, `*_SECRET`, `*_TOKEN`, `*_PASSWORD`
- `AWS_*`, `AZURE_*`, `GCP_*`
- `GITHUB_*`, `OPENAI_*`, `ANTHROPIC_*`
- And more (see `pty/env.py`)

### Session Isolation

With Cloudflare Access enabled:
- Users identified by email from CF-Access-JWT
- Each user sees only their own sessions
- Prevents cross-user session access
