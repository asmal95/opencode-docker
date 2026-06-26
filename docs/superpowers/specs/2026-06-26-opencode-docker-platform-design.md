# OpenCode Docker Platform - Architecture Design

**Date**: 2026-06-26
**Status**: Draft
**Author**: Design session

## Overview

Universal Docker-based platform for running OpenCode AI coding agent in controlled, reproducible environments. Supports multiple scenarios (dev, code-review, telegram bot, autonomous agent) through a composable architecture with compose overrides and extensible Docker image layers.

## Goals

1. Fully controlled execution environment for OpenCode in Docker
2. Config injection (RO by default, RW optional) per scenario
3. Session persistence across container restarts
4. Built-in Python MCP server support
5. Extensible to sidecar containers (telegram bot, etc.)
6. Security hardening at Docker and OpenCode levels

## Non-Goals

- Multi-tenant hosting (single user/organization per deployment)
- Kubernetes orchestration (Docker Compose only for now)
- Custom MCP transport protocols (use OpenCode's native local/remote MCP)
- Desktop app or IDE integration inside container

## Architecture

```
opencode-docker/
├── Dockerfile                        # Base: Debian slim + Node + opencode-ai + Python/uv
├── Dockerfile.full                   # Extended: base + Bun + ast-grep + tmux + OpenSpec + Docker CLI
├── docker-compose.yaml               # Base compose (opencode service + volumes)
├── docker-compose.code-review.yaml   # Override: read-only agent, GitLab MCP
├── docker-compose.bot.yaml           # Override: serve mode + telegram-bot sidecar
├── docker-compose.autonomous.yaml    # Override: expanded permissions
├── .env.example                      # Template for secrets and config
├── entrypoint.sh                     # UID/GID mapping + privilege drop
├── configs/
│   ├── base/
│   │   └── opencode.jsonc            # Default config: providers, models
│   ├── code-review/
│   │   └── opencode.jsonc            # Read-only agent, deny write/edit/bash
│   ├── bot/
│   │   └── opencode.jsonc            # Headless mode, GitHub MCP, controlled bash
│   └── autonomous/
│       └── opencode.jsonc            # Expanded permissions, all tools
├── mcp-servers/
│   ├── gitlab-mcp/                   # Python MCP for GitLab (local stdio)
│   │   ├── pyproject.toml
│   │   └── src/
│   └── github-mcp/                  # Python MCP for GitHub (local stdio)
│       ├── pyproject.toml
│       └── src/
├── sidecars/
│   └── telegram-bot/                 # Python bot (aiogram)
│       ├── Dockerfile
│       ├── requirements.txt
│       ├── bot.py
│       └── handlers/
└── scripts/
    └── setup.sh                      # First-time setup (dirs, env, auth)
```

## Component Design

### 1. Base Layer (Docker Image)

**Multi-stage build**:

| Stage | FROM | Contents | Target |
|-------|------|----------|--------|
| `opencode-base` | debian:bookworm-slim | git, curl, bash, ca-certificates, ripgrep, jq, Node.js LTS, opencode-ai, Python 3 + uv | Minimal headless |
| `opencode-full` | opencode-base | + Bun, ast-grep, tmux, lsof, OpenSpec, Docker CLI | Full dev environment |

**Key decisions**:
- Debian over Alpine: glibc compatibility for Node native modules, Python manylinux wheels
- UID/GID mapping via entrypoint.sh (from opencode-dockerized reference): container user matches host user for correct file permissions
- opencode-ai installed at build-time with `ARG OPENCODE_BUILD_TIME` for cache busting on updates
- Python + uv in base image: ready for local MCP servers without additional layers
- Disabled env vars: `OPENCODE_DISABLE_AUTOUPDATE=true`, `OPENCODE_DISABLE_MODELS_FETCH=true`, `OPENCODE_DISABLE_SHARE=true`

**Build targets**:
```bash
docker build --target opencode-base -t opencode-base:latest .
docker build --target opencode-full -t opencode-full:latest .
```

### 2. Config System

**Leverages OpenCode's native config precedence**:
- `OPENCODE_CONFIG` env var: points to mounted config file
- `OPENCODE_CONFIG_CONTENT` env var: inline JSON overrides at runtime
- `{env:VAR_NAME}` substitution in opencode.jsonc for secrets

**Config directory layout**:
```
configs/
├── base/opencode.jsonc          # Shared: providers, models, basic permissions
├── code-review/opencode.jsonc    # Override: deny write/edit/bash, GitLab MCP
├── bot/opencode.jsonc            # Override: GitHub MCP, controlled permissions
└── autonomous/opencode.jsonc     # Override: expanded permissions
```

**Mount modes**:
- **RO (default)**: `./configs/${SCENARIO}:/opt/opencode-config:ro` - admin controls config, agent cannot modify
- **RW (optional)**: `./configs/${SCENARIO}:/opt/opencode-config:rw` - agent can change settings interactively

**Secrets**: API keys via env vars in `.env`, referenced as `{env:ANTHROPIC_API_KEY}` in opencode.jsonc. Never committed to repo.

**SCENARIO env var**: Selects which config directory to mount. Used in compose command:
```bash
SCENARIO=code-review docker compose -f docker-compose.yaml -f docker-compose.code-review.yaml up
```

### 3. Session Persistence

**Named Docker volumes** (survive `docker compose down`, deleted only with `-v`):

| Volume | Container Path | Content |
|--------|---------------|---------|
| `opencode-data` | `/home/coder/.local/share/opencode` | Auth, sessions DB, storage, mcp-auth.json |
| `opencode-cache` | `/home/coder/.cache/opencode` | Provider package cache |

**Workspace**: Bind mount `${PROJECT_DIR:-.}:/workspace` (RW - agent reads and writes code).

**Why named volumes**: No permission issues across OS, portable, Docker manages ownership. Bind mount option available for production scenarios needing host visibility.

### 4. MCP Integration Layer

**Python MCP servers as local (stdio)** inside the opencode container:

```jsonc
// configs/code-review/opencode.jsonc
{
  "mcp": {
    "gitlab-mcp": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/opt/mcp-servers/gitlab-mcp", "mcp-gitlab"],
      "environment": {
        "GITLAB_TOKEN": "{env:GITLAB_TOKEN}"
      }
    },
    "context7": {
      "type": "remote",
      "url": "https://mcp.context7.com/mcp"
    }
  }
}
```

**MCP server layout in image**:
```
/opt/mcp-servers/
├── gitlab-mcp/
│   ├── pyproject.toml     # uv project with mcp-server-gitlab dep
│   └── src/
└── github-mcp/
    ├── pyproject.toml
    └── src/
```

**Installation in Dockerfile**:
```dockerfile
COPY mcp-servers/ /opt/mcp-servers/
RUN cd /opt/mcp-servers/gitlab-mcp && uv sync && \
    cd /opt/mcp-servers/github-mcp && uv sync
```

**Adding new MCP**: Drop directory into `mcp-servers/`, add `uv sync` to Dockerfile, add config entry in opencode.jsonc.

**Future**: Sidecar containers for MCP when servers need isolation, separate resource limits, or HTTP transport. Not in initial scope.

### 5. Compose Architecture

**Base compose** (`docker-compose.yaml`):
```yaml
services:
  opencode:
    build:
      context: .
      dockerfile: Dockerfile
      args:
        OPENCODE_BUILD_TIME: "${OPENCODE_BUILD_TIME:-0}"
    image: opencode-platform:latest
    environment:
      OPENCODE_CONFIG: /opt/opencode-config/opencode.jsonc
      OPENCODE_DISABLE_AUTOUPDATE: "true"
      OPENCODE_DISABLE_MODELS_FETCH: "true"
      OPENCODE_DISABLE_SHARE: "true"
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY:-}
    volumes:
      - opencode-data:/home/coder/.local/share/opencode
      - opencode-cache:/home/coder/.cache/opencode
      - ./configs/${SCENARIO:-base}:/opt/opencode-config/:ro
      - ${PROJECT_DIR:-.}:/workspace
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL
    extra_hosts:
      - "app.opencode.ai:0.0.0.0"
      - "api.opencode.ai:0.0.0.0"
      - "opncd.ai:0.0.0.0"
    networks:
      - opencode-net

volumes:
  opencode-data:
  opencode-cache:

networks:
  opencode-net:
    driver: bridge
```

**Override examples**:

`docker-compose.code-review.yaml`:
```yaml
services:
  opencode:
    environment:
      GITLAB_TOKEN: ${GITLAB_TOKEN:-}
      GITLAB_PROJECT: ${GITLAB_PROJECT:-}
      GITLAB_MR_IID: ${GITLAB_MR_IID:-}
    volumes:
      - ${CI_PROJECT_DIR:-.}:/workspace
```

`docker-compose.bot.yaml`:
```yaml
services:
  opencode:
    command: ["opencode", "serve", "--hostname", "0.0.0.0", "--port", "4096"]
    ports:
      - "4096:4096"

  telegram-bot:
    build: ./sidecars/telegram-bot
    depends_on:
      - opencode
    environment:
      OPENCODE_API_URL: http://opencode:4096
      TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN}
    networks:
      - opencode-net
```

`docker-compose.autonomous.yaml`:
```yaml
services:
  opencode:
    volumes:
      - ./configs/autonomous:/opt/opencode-config/:ro
    # May need Docker socket for CI/CD tasks
    # volumes:
    #   - /var/run/docker.sock:/var/run/docker.sock
```

**Usage**:
```bash
# Dev (base only):
docker compose up

# Code-review:
SCENARIO=code-review docker compose -f docker-compose.yaml -f docker-compose.code-review.yaml up

# Telegram bot:
SCENARIO=bot docker compose -f docker-compose.yaml -f docker-compose.bot.yaml up

# Autonomous:
SCENARIO=autonomous docker compose -f docker-compose.yaml -f docker-compose.autonomous.yaml up
```

### 6. Sidecars

**Telegram Bot** (scenario: bot):

- Python 3.12 + aiogram
- Communicates with opencode via `opencode serve` HTTP API (port 4096)
- Own Dockerfile in `sidecars/telegram-bot/`
- Env vars: `TELEGRAM_BOT_TOKEN`, `OPENCODE_API_URL`, `GITHUB_TOKEN`
- Flow: user message -> bot -> opencode API -> response -> Telegram

**Future sidecars**:
- Slack bot
- Web UI
- GitLab webhook receiver
- Monitoring/dashboard

### 7. Security Model

**Level 1 - Docker**:
```yaml
security_opt:
  - no-new-privileges:true
cap_drop:
  - ALL
read_only: true
tmpfs:
  - /tmp
extra_hosts:                    # Block phone-home
  - "app.opencode.ai:0.0.0.0"
  - "api.opencode.ai:0.0.0.0"
  - "opncd.ai:0.0.0.0"
```

**Level 2 - OpenCode permissions** (per scenario in opencode.jsonc):

| Scenario | write | edit | bash | Notes |
|----------|-------|------|------|-------|
| code-review | deny | deny | deny | Read-only review |
| bot | allow | allow | ask | Controlled execution |
| autonomous | allow | allow | allow | Full access, Docker-level control |
| dev | allow | allow | ask | Interactive with confirmation |

**Level 3 - Network**:
- Open internet access (needed for LLM APIs, MCP remote servers)
- Phone-home blocked via extra_hosts
- No Docker socket mount by default (optional in autonomous profile)

**Level 4 - Config**:
- Config files mounted RO by default
- API keys via env vars, not in config files
- `.env` excluded from git

## Scenarios Detail

### Scenario 1: Code Management via Telegram Bot

**Stack**: opencode (serve mode) + telegram-bot sidecar
**Config**: configs/bot/opencode.jsonc
**MCP**: github-mcp (local)
**Flow**: Telegram -> bot -> opencode API -> code changes -> PR -> Telegram notification
**Override**: docker-compose.bot.yaml

### Scenario 2: Code Review with GitLab

**Stack**: opencode (headless run)
**Config**: configs/code-review/opencode.jsonc
**MCP**: gitlab-mcp (local)
**Env vars**: GITLAB_TOKEN, GITLAB_PROJECT, GITLAB_MR_IID
**Flow**: GitLab CI runner -> opencode run "review MR {iid}" -> output review
**Override**: docker-compose.code-review.yaml

### Scenario 3: Autonomous Agent

**Stack**: opencode (headless run)
**Config**: configs/autonomous/opencode.jsonc
**MCP**: per-task configuration
**Flow**: Scheduled/task-driven execution with expanded permissions
**Override**: docker-compose.autonomous.yaml

## Open Questions

1. **opencode serve API maturity**: The server mode API is marked experimental. Need to verify it supports session creation, status polling, and result retrieval for the telegram bot scenario. Fallback: use `opencode run` (CLI) with subprocess wrapper.

2. **Python MCP stdio transport**: Not all Python MCP servers support clean stdio transport. Some only support SSE or streamable-http. Need to verify or add a stdio wrapper.

3. **models.dev blocking**: Current compose blocks `models.dev:0.0.0.0`. This may break provider functionality. Decision: keep blocked for now, re-enable if needed per scenario.

4. **read_only filesystem**: Docker `read_only: true` with `tmpfs: /tmp` may break opencode internals (npm cache, temp files). Need testing. Fallback: remove read_only, rely on cap_drop + no-new-privileges.

5. **GitLab CI integration**: How does the runner mount the project? Need to test with GitLab CI's `DOCKER_AUTH_CONFIG` and volume mounting patterns.

## Implementation Phases

### Phase 1: Foundation (MVP)
- Base Dockerfile (debian + node + opencode + python/uv)
- entrypoint.sh (UID/GID mapping)
- Base docker-compose.yaml
- Base config (configs/base/opencode.jsonc)
- Session persistence (named volumes)
- .env.example
- Security hardening (cap_drop, no-new-privileges, extra_hosts)

### Phase 2: MCP + Scenarios
- mcp-servers/ directory with gitlab-mcp and github-mcp
- Dockerfile with MCP server installation
- Scenario configs (code-review, bot, autonomous)
- Compose override files

### Phase 3: Sidecars
- Telegram bot sidecar (Python + aiogram)
- docker-compose.bot.yaml override
- Bot-opencode integration via serve API

### Phase 4: Polish
- setup.sh script (first-time setup)
- Extended Dockerfile (Bun, ast-grep, tmux, Docker CLI)
- Documentation
- CI/CD testing

## References

- [opencode-dockerized](https://github.com/glennvdv/opencode-dockerized) - Reference implementation
- [OpenCode Docs](https://opencode.ai/docs) - Configuration, MCP, Server API
- [OpenCode MCP Servers](https://opencode.ai/docs/mcp-servers/) - MCP configuration
- [OpenCode Config](https://opencode.ai/docs/config/) - Config precedence and schema
