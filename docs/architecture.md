# Architecture

Porterminal uses hexagonal (ports & adapters) architecture for clean separation of concerns.

## Project Structure

```
porterminal/
├── __init__.py           # Entry point: server + tunnel + QR code
├── app.py                # FastAPI application factory
├── config.py             # Configuration loading (Pydantic)
├── domain/               # Core business logic (no dependencies)
│   ├── entities/         # Session, OutputBuffer
│   ├── values/           # SessionId, TerminalDimensions, etc.
│   ├── services/         # RateLimiter, EnvironmentSanitizer
│   └── ports/            # Interfaces (PtyPort, SessionRepository)
├── application/          # Use cases
│   ├── services/         # TerminalService, SessionService
│   └── ports/            # ConfigPort, ConnectionPort
├── infrastructure/       # External adapters
│   ├── web/              # WebSocket adapter
│   ├── repositories/     # InMemorySessionRepository
│   ├── config/           # YAML loader, shell detection
│   ├── cloudflared.py    # Tunnel management
│   └── server.py         # Uvicorn wrapper
├── pty/                  # Platform-specific PTY
│   ├── windows.py        # pywinpty backend
│   └── unix.py           # pty module backend
├── cli/                  # CLI interface
│   ├── args.py           # Argument parsing
│   └── display.py        # Startup screen
└── static/               # Built frontend (TypeScript/Vite)

frontend/                 # Source frontend
├── src/
│   ├── services/         # ConnectionService, TabService
│   ├── input/            # KeyMapper, InputHandler
│   ├── gestures/         # Touch handling
│   └── ui/               # UI components
└── index.html
```

## Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│                         Client                               │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐      │
│  │   xterm.js  │◀──▶│  WebSocket  │◀──▶│  Frontend   │      │
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
│  │   FastAPI   │◀──▶│  Terminal   │◀──▶│     PTY     │      │
│  │  WebSocket  │    │   Service   │    │   Backend   │      │
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
- And more (see `domain/services/`)

### Session Isolation

With Cloudflare Access enabled:
- Users identified by email from CF-Access-JWT
- Each user sees only their own sessions
- Prevents cross-user session access
