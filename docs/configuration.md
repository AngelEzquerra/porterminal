# Configuration

Porterminal is configured via `config.yaml` in the working directory.

## Default Configuration

```yaml
server:
  host: "127.0.0.1"
  port: 8000

terminal:
  cols: 120
  rows: 30

buttons: []

cloudflare:
  team_domain: ""
  access_aud: ""
```

## Server Settings

| Option | Default | Description |
|--------|---------|-------------|
| `host` | `127.0.0.1` | Bind address. Use `0.0.0.0` only with tunnel |
| `port` | `8000` | Server port |

```yaml
server:
  host: "127.0.0.1"
  port: 8000
```

## Terminal Settings

| Option | Default | Range | Description |
|--------|---------|-------|-------------|
| `cols` | `120` | 40-500 | Terminal width in columns |
| `rows` | `30` | 10-200 | Terminal height in rows |
| `default_shell` | auto | - | Override auto-detected shell |
| `shells` | auto | - | Custom shell definitions |

### Shell Auto-Detection

Porterminal automatically detects available shells:

- **Windows**: PowerShell, CMD, WSL (if installed)
- **Unix**: bash, zsh, sh

### Custom Shells

```yaml
terminal:
  default_shell: "custom"
  shells:
    - name: "Custom Shell"
      id: "custom"
      command: "/path/to/shell"
      args: ["-l"]
```

## Custom Buttons

Add custom buttons to the virtual keyboard:

```yaml
buttons:
  - label: "git"
    send: "git status\r"
  - label: "ls"
    send: "ls -la\r"
  - label: "clear"
    send: "\x0c"  # Ctrl+L
```

| Property | Description |
|----------|-------------|
| `label` | Button text (keep short for mobile) |
| `send` | String to send. Use `\r` for Enter, `\x__` for control chars |

### Control Character Reference

| Sequence | Key | Description |
|----------|-----|-------------|
| `\r` | Enter | Carriage return |
| `\x03` | Ctrl+C | Interrupt |
| `\x04` | Ctrl+D | EOF |
| `\x0c` | Ctrl+L | Clear screen |
| `\x1a` | Ctrl+Z | Suspend |
| `\x1b` | Escape | Escape key |

## Cloudflare Access Integration

For team deployments with Cloudflare Access:

```yaml
cloudflare:
  team_domain: "yourteam.cloudflareaccess.com"
  access_aud: "your-application-audience-tag"
```

This enables:
- User identification via Cloudflare Access email
- Session isolation per user
- Maximum 10 sessions per user

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PORTERMINAL_LOG_LEVEL` | Log level (DEBUG, INFO, WARNING, ERROR) |
| `PORTERMINAL_CWD` | Override working directory |
