"""
Porterminal - Web-based terminal accessible via Cloudflare Tunnel.

This package provides:
- FastAPI server with WebSocket terminal endpoint
- PTY management with cross-platform support (Windows/Unix)
- Session management with reconnection support
- Configuration system with shell auto-detection
"""

__version__ = "0.1.0"

import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread

from rich.console import Console

from porterminal.cli import display_startup_screen, parse_args
from porterminal.infrastructure import (
    CloudflaredInstaller,
    drain_process_output,
    find_available_port,
    get_local_ip,
    is_port_available,
    start_cloudflared,
    start_server,
    wait_for_server,
)

console = Console()


def main() -> int:
    """Main entry point."""
    args = parse_args()
    verbose = args.verbose

    # Set log level based on verbose flag
    if verbose:
        os.environ["PORTERMINAL_LOG_LEVEL"] = "DEBUG"

    # Validate and set working directory
    cwd_str = None
    if args.path:
        cwd = Path(args.path).resolve()
        if not cwd.exists():
            console.print(f"[red]Error:[/red] Path does not exist: {cwd}")
            return 1
        if not cwd.is_dir():
            console.print(f"[red]Error:[/red] Path is not a directory: {cwd}")
            return 1
        cwd_str = str(cwd)
        os.environ["PORTERMINAL_CWD"] = cwd_str

    from porterminal.config import get_config

    config = get_config()
    bind_host = config.server.host
    preferred_port = config.server.port
    port = preferred_port
    # Use 127.0.0.1 for health checks (can't connect to 0.0.0.0)
    check_host = "127.0.0.1" if bind_host == "0.0.0.0" else bind_host

    # Check cloudflared (skip if --no-tunnel)
    if not args.no_tunnel and not CloudflaredInstaller.is_installed():
        console.print("[yellow]cloudflared not found[/yellow]")
        if not CloudflaredInstaller.install():
            console.print("[red]Error:[/red] Failed to install cloudflared")
            console.print()
            console.print("Install manually: [cyan]winget install cloudflare.cloudflared[/cyan]")
            return 1
        # Verify installation
        if not CloudflaredInstaller.is_installed():
            console.print("[red]Error:[/red] cloudflared still not found after install")
            return 1

    # Show startup status
    with console.status("[cyan]Starting...[/cyan]", spinner="dots") as status:
        # Start or reuse server
        if wait_for_server(check_host, port, timeout=1):
            if verbose:
                console.print(f"[dim]Reusing server on {bind_host}:{port}[/dim]")
            server_process = None
        else:
            if not is_port_available(bind_host, port):
                port = find_available_port(bind_host, preferred_port)
                if verbose:
                    console.print(f"[dim]Using port {port}[/dim]")

            status.update("[cyan]Starting server...[/cyan]")
            server_process = start_server(bind_host, port, verbose=verbose)

            if not wait_for_server(check_host, port, timeout=30):
                console.print("[red]Error:[/red] Server failed to start")
                if server_process and server_process.poll() is None:
                    server_process.terminate()
                return 1

        tunnel_process = None
        tunnel_url = None

        if not args.no_tunnel:
            status.update("[cyan]Establishing tunnel...[/cyan]")
            tunnel_process, tunnel_url = start_cloudflared(port)

            if not tunnel_url:
                console.print("[red]Error:[/red] Failed to establish tunnel")
                if server_process:
                    server_process.terminate()
                if tunnel_process:
                    tunnel_process.terminate()
                return 1

    # Display final screen
    local_ip = get_local_ip()
    local_url = f"http://{local_ip}:{port}"
    display_cwd = cwd_str or os.getcwd()

    if args.no_tunnel:
        display_url = f"http://{check_host}:{port}"
        display_startup_screen(display_url, is_tunnel=False, cwd=display_cwd, local_url=local_url)
    else:
        display_startup_screen(tunnel_url, is_tunnel=True, cwd=display_cwd, local_url=local_url)

    # Drain process output silently in background (only when not verbose)
    if server_process is not None and not verbose:
        Thread(target=drain_process_output, args=(server_process,), daemon=True).start()
    if tunnel_process is not None:
        Thread(target=drain_process_output, args=(tunnel_process,), daemon=True).start()

    # Wait for Ctrl+C or process exit
    try:
        while True:
            if server_process is not None and server_process.poll() is not None:
                console.print("\n[red]Server stopped unexpectedly[/red]")
                break
            if tunnel_process is not None and tunnel_process.poll() is not None:
                console.print("\n[red]Tunnel stopped unexpectedly[/red]")
                break
            time.sleep(1)

    except KeyboardInterrupt:
        console.print("\n[dim]Shutting down...[/dim]")

    # Cleanup
    if server_process is not None:
        server_process.terminate()
    if tunnel_process is not None:
        tunnel_process.terminate()

    try:
        if server_process is not None:
            server_process.wait(timeout=5)
        if tunnel_process is not None:
            tunnel_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if server_process is not None:
            server_process.kill()
        if tunnel_process is not None:
            tunnel_process.kill()

    return 0


if __name__ == "__main__":
    sys.exit(main())
