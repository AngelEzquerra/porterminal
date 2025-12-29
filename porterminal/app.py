"""FastAPI application with security checks and WebSocket endpoint."""

import asyncio
import ctypes
import logging
import os
import signal
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import RequestResponseEndpoint

from .config import Config, get_config, reload_config
from .logging_setup import setup_logging_from_env
from .session import SessionRegistry
from .websocket import handle_terminal_session

logger = logging.getLogger(__name__)

# Path to static files (inside package)
STATIC_DIR = Path(__file__).parent / "static"


def is_admin() -> bool:
    """Check if running as administrator (Windows)."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def security_preflight_checks() -> None:
    """Run security checks before starting the application."""
    # Check not running as admin
    if is_admin():
        logger.warning(
            "SECURITY WARNING: Running as Administrator is not recommended. "
            "This exposes excessive privileges to remote users."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    setup_logging_from_env()
    security_preflight_checks()

    # Wire dependencies explicitly and store in app.state
    config = get_config()
    registry = SessionRegistry(config=config)

    app.state.config = config
    app.state.registry = registry

    await registry.start()

    logger.info("Porterminal server started")

    yield

    # Shutdown
    await registry.stop()
    logger.info("Porterminal server stopped")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Porterminal",
        description="Web-based terminal accessible from phone via Cloudflare Tunnel",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def no_cache_static_assets(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Disable caching for static assets to ensure live updates during development."""
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

    # Mount static files
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the main page."""
        index_path = STATIC_DIR / "index.html"
        if index_path.exists():
            content = index_path.read_text(encoding="utf-8")
            return HTMLResponse(
                content=content,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return JSONResponse(
            {"error": "index.html not found"},
            status_code=404,
        )

    @app.get("/health")
    async def health():
        """Health check endpoint."""
        registry: SessionRegistry = app.state.registry
        return {
            "status": "healthy",
            "sessions": registry.session_count,
        }

    @app.get("/api/config")
    async def get_client_config():
        """Get client configuration (shells and buttons)."""
        config: Config = app.state.config
        return {
            "shells": [{"id": s.id, "name": s.name} for s in config.terminal.shells],
            "buttons": [{"label": b.label, "send": b.send} for b in config.buttons],
            "default_shell": config.terminal.default_shell,
        }

    @app.post("/api/config/reload")
    async def reload_configuration():
        """Reload configuration from file."""
        try:
            new_config = reload_config()
            app.state.config = new_config  # Update app.state to match global
            return {"status": "ok", "message": "Configuration reloaded"}
        except Exception as e:
            return JSONResponse(
                {"status": "error", "message": str(e)},
                status_code=500,
            )

    @app.post("/api/shutdown")
    async def shutdown_server(request: Request):
        """Shutdown the server and tunnel.

        Only allowed from localhost or authenticated Cloudflare Access users.
        """
        # Check if request is from localhost
        client_host = request.client.host if request.client else None
        is_localhost = client_host in ("127.0.0.1", "::1", "localhost")

        # Check for Cloudflare Access authentication
        cf_user = request.headers.get("cf-access-authenticated-user-email")

        if not is_localhost and not cf_user:
            logger.warning(
                "Unauthorized shutdown attempt from %s",
                client_host,
            )
            return JSONResponse(
                {
                    "error": "Unauthorized - must be localhost or authenticated via Cloudflare Access"
                },
                status_code=403,
            )

        logger.info("Shutdown requested via API by %s", cf_user or client_host)

        # Send response before shutting down
        asyncio.get_running_loop().call_later(0.5, lambda: os.kill(os.getpid(), signal.SIGTERM))

        return {"status": "ok", "message": "Server shutting down..."}

    @app.websocket("/ws")
    async def websocket_terminal(
        websocket: WebSocket,
        session_id: str | None = Query(None),
        shell: str | None = Query(None),
        skip_buffer: str | None = Query(None),
    ):
        """WebSocket endpoint for terminal communication."""
        logger.info(
            "WebSocket connect attempt client=%s session_id=%s shell=%s skip_buffer=%s",
            getattr(websocket.client, "host", None),
            session_id,
            shell,
            skip_buffer,
        )
        # Accept the connection
        await websocket.accept()

        # Get dependencies from app.state
        registry: SessionRegistry = app.state.registry
        config: Config = app.state.config

        # Get user ID from headers (Cloudflare Access)
        # For local testing, use a default user
        user_id = websocket.headers.get("cf-access-authenticated-user-email", "local-user")
        logger.info(
            "WebSocket accepted client=%s user_id=%s session_id=%s shell=%s",
            getattr(websocket.client, "host", None),
            user_id,
            session_id,
            shell,
        )

        try:
            if session_id:
                # Reconnect to existing session
                logger.info(
                    "WebSocket reconnect requested user_id=%s session_id=%s", user_id, session_id
                )
                session = await registry.reconnect_session(session_id, user_id, websocket)
                if not session:
                    await websocket.send_json(
                        {
                            "type": "error",
                            "message": "Session not found or unauthorized",
                        }
                    )
                    await websocket.close(code=4004)
                    logger.warning(
                        "WebSocket reconnect denied user_id=%s session_id=%s", user_id, session_id
                    )
                    return
            else:
                # Create new session using config from app.state
                cols = config.terminal.cols
                rows = config.terminal.rows

                logger.info(
                    "WebSocket creating session user_id=%s shell=%s cols=%d rows=%d",
                    user_id,
                    shell,
                    cols,
                    rows,
                )
                session = await registry.create_session(
                    user_id=user_id,
                    shell_id=shell,
                    cols=cols,
                    rows=rows,
                )
                session.websocket = websocket
                session.is_connected = True
                logger.info(
                    "WebSocket created session user_id=%s session_id=%s shell_id=%s",
                    user_id,
                    session.session_id,
                    session.shell_id,
                )

            # Handle the session
            await handle_terminal_session(
                websocket, session, registry, skip_buffer=bool(skip_buffer)
            )

        except ValueError as e:
            # Session limit exceeded
            await websocket.send_json(
                {
                    "type": "error",
                    "message": str(e),
                }
            )
            await websocket.close(code=4005)
            logger.warning("WebSocket session limit user_id=%s error=%s", user_id, e)

        except WebSocketDisconnect:
            logger.info(f"Client disconnected: {user_id}")

        except Exception as e:
            logger.exception(f"WebSocket error: {e}")
            with suppress(Exception):
                await websocket.close(code=1011)
        finally:
            logger.info(
                "WebSocket handler finished user_id=%s session_id=%s",
                user_id,
                session_id or getattr(locals().get("session", None), "session_id", None),
            )

    return app


# Create the app instance
app = create_app()
