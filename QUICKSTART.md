# Docker Quickstart

Get the Personal Agent system running end-to-end with Docker in under 5 commands.

---

## 1. Prerequisites

- **Docker** (with Compose v2): [install](https://docs.docker.com/get-docker/)
- **An LLM provider key** — pick one:
  - Anthropic: [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
  - OpenAI: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
  - Google Gemini: [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
  - Claude Code CLI (no key; uses your Claude Max / Pro subscription): `npm install -g @anthropic-ai/claude-code` then `claude login`
- **Ollama** (included automatically): The Docker Compose stack includes an Ollama sidecar for zero-cost local intent parsing. No setup needed — the model is pulled automatically on first `docker compose up`.
- **Composio account** (for tool integrations): [app.composio.dev](https://app.composio.dev/) — sign up and create an MCP server to get your server URL and API key
- **curl** (or any HTTP client)

---

## 2. Configure

```bash
cp .env.example .env
```

Open `.env` and configure your LLM provider. The app uses a provider-agnostic
factory — pick **one** provider and set the matching variables. The default is
Anthropic.

```
# ---- Anthropic (default) ----
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
LLM_TIMEOUT_S=60
PLANNER_PRIMARY_MODEL=claude-sonnet-4-5-20250929
PLANNER_FALLBACK_MODEL=claude-sonnet-4-5-20250929
INTAKE_PARSER_MODEL=claude-sonnet-4-5-20250929
```

To use a different provider, swap the block above for one of these:

```
# ---- OpenAI ----
LLM_PROVIDER=openai
LLM_API_KEY=sk-...
LLM_TIMEOUT_S=60
PLANNER_PRIMARY_MODEL=gpt-4o
PLANNER_FALLBACK_MODEL=gpt-4o-mini
INTAKE_PARSER_MODEL=gpt-4o-mini
```

```
# ---- Google Gemini ----
LLM_PROVIDER=gemini
LLM_API_KEY=AIza...
LLM_TIMEOUT_S=60
PLANNER_PRIMARY_MODEL=gemini-2.5-flash
PLANNER_FALLBACK_MODEL=gemini-2.5-flash
INTAKE_PARSER_MODEL=gemini-2.5-flash
```

```
# ---- Claude Code CLI (no API key, uses host OAuth subscription) ----
# Requires `claude` installed on the host AND logged in. In Docker you
# must also mount ~/.claude into the container so the CLI finds credentials.
LLM_PROVIDER=claude_code
# LLM_API_KEY intentionally omitted
PLANNER_PRIMARY_MODEL=claude-sonnet-4-5-20250929
PLANNER_FALLBACK_MODEL=claude-sonnet-4-5-20250929
INTAKE_PARSER_MODEL=claude-sonnet-4-5-20250929
# PLANNER_CLAUDE_CODE_TIMEOUT_S=120    # optional, default 120s
# CLAUDE_CODE_BIN=/opt/homebrew/bin/claude   # optional, default `claude` on PATH
```

> **Note:** Model names must match your provider. Leaving
> `PLANNER_PRIMARY_MODEL=claude-*` set while switching `LLM_PROVIDER=openai`
> will fail at the first LLM call. The factory logs a warning
> (`llm_model_provider_mismatch`) on startup when it spots an obvious
> mismatch, but it does not rewrite the model name for you.

### Composio (MCP tool integrations)

Aexor uses [Composio](https://app.composio.dev/) to manage user OAuth connections (Gmail, Google Calendar, Slack, etc.) and to expose those tools to the Planner / ExecuteOrchestrator. There are **two variable groups** and you need **both** for the full experience:

```
# 1. IntegrationManager OAuth flow (Integrations page "Connect" buttons)
COMPOSIO_API_KEY=your-composio-api-key
COMPOSIO_MCP_CONFIG_ID=your-mcp-config-id

# 2. Tool catalog + execution (Planner tool discovery, ExecuteOrchestrator dispatch)
MCP_SERVER_COMPOSIO_URL=https://backend.composio.dev/v3/mcp/YOUR_SERVER_ID
MCP_SERVER_COMPOSIO_API_KEY=your-composio-api-key
```

To get these values:

1. Sign up at [app.composio.dev](https://app.composio.dev/)
2. **`COMPOSIO_API_KEY`** / **`MCP_SERVER_COMPOSIO_API_KEY`** — Go to **Settings > API Keys** and copy your API key (the same key is used for both variables)
3. Create an MCP server (or use the default one) from the **MCP Servers** page
4. **`COMPOSIO_MCP_CONFIG_ID`** — copy the config ID from the MCP server detail page
5. **`MCP_SERVER_COMPOSIO_URL`** — copy the full server URL from the same page

What each pair enables:

| Variable pair | Enables | Failure mode if missing |
|---|---|---|
| `COMPOSIO_API_KEY` + `COMPOSIO_MCP_CONFIG_ID` | Integrations page OAuth flow, per-user MCP URL generation | `POST /api/integrations/connect` → `501 NotImplementedError("Composio OAuth flow requires COMPOSIO_API_KEY to be set.")` — users cannot connect providers from the UI |
| `MCP_SERVER_COMPOSIO_URL` + `MCP_SERVER_COMPOSIO_API_KEY` | Tool catalog refresh, ExecuteOrchestrator tool dispatch | Planner sees zero tools; execution fails with "server not configured" |

Without either pair the app still starts and Intake/Planner/chat flows work on intents that do not require tools — only Integrations and tool execution are blocked.

> **Restart required.** Environment variables are read at process start. After editing `.env`, rerun `docker compose up -d --force-recreate app` — a hot `docker cp` will not pick up new env vars.

Optionally, restrict which tools are exposed to the planner:

```
TOOL_ALLOWLIST=GOOGLECALENDAR_CREATE_EVENT,GOOGLECALENDAR_FIND_FREE_SLOTS,GMAIL_SEND_EMAIL
```

### Local LLM (Ollama — automatic)

The Compose stack includes an Ollama sidecar that runs Llama 3.2 3B locally for
intent parsing. Intake tries the local model first (~1s, free) and falls back to
your remote LLM (Claude/OpenAI/Gemini) if Ollama is unavailable.

This is **enabled by default** — no configuration needed. The model is pulled
automatically on first startup. To disable:

```
INTAKE_USE_LOCAL_LLM=false
```

To use a different local model:

```
INTAKE_LOCAL_MODEL=mistral:7b    # any model Ollama supports
INTAKE_LOCAL_TIMEOUT_S=60        # increase for larger models
```

All other values have safe dev defaults. For production, regenerate `ENCRYPTION_KEY`, `JWT_SECRET`, `APPROVAL_TOKEN_SECRET`, and `CREDENTIAL_MASTER_KEY` using the commands in `.env.example`.

---

## 3. Start

```bash
docker compose up --build -d
```

This builds the app image (downloads the ONNX embedding model, installs deps), starts PostgreSQL 16 (pgvector), Redis 7, an Ollama sidecar (auto-pulls Llama 3.2 3B), and the FastAPI application.

---

## 4. Verify

Wait for all three containers to be healthy:

```bash
docker compose ps
```

Expected output shows `db`, `redis`, `ollama`, and `app` all with status `healthy`.
The `ollama-pull` container will show as exited (it pulls the model then stops — this is normal).

Check app logs for successful initialization:

```bash
docker compose logs app --tail 30
```

Look for:
- `Ollama adapter created for Intake local LLM`
- `VectorIndex` initialized (or graceful degradation message)
- `Planner` initialized
- `Uvicorn running on http://0.0.0.0:8000`

Health check:

```bash
curl -s http://localhost:8000/health | python -m json.tool
```

```json
{
    "status": "ok",
    "service": "personal-agent"
}
```

---

## 5. Register and Login

### Register a new user

```bash
curl -s -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "email": "demo@example.com",
    "password": "SecurePass123!",
    "full_name": "Demo User"
  }' | python -m json.tool
```

Response:

```json
{
    "user_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
    "email": "demo@example.com",
    "full_name": "Demo User",
    "context_tier": 1
}
```

### Login (get JWT token)

```bash
curl -s -X POST http://localhost:8000/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=demo@example.com&password=SecurePass123!" | python -m json.tool
```

Response:

```json
{
    "access_token": "eyJhbGciOi...",
    "token_type": "bearer",
    "expires_in": 3600
}
```

Save the token for subsequent requests:

```bash
export TOKEN="eyJhbGciOi..."
```

---

## 6. Send a Message

Submit a user message to the Intake pipeline:

```bash
curl -s -X POST http://localhost:8000/intake/message \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "message": "Schedule a meeting with Alice tomorrow at 2pm"
  }' | python -m json.tool
```

Response (intent collection in progress):

```json
{
    "status": "collecting",
    "session_id": "01JXXXXXXXXXXXXXXXXXXXXX",
    "detected_intent": "schedule_meeting",
    "collected_entities": {
        "attendee": "Alice",
        "time": "tomorrow at 2pm"
    },
    "missing_fields": ["duration", "location"],
    "follow_up": "How long should the meeting be, and where?",
    "turn_count": 1,
    "intent": null
}
```

When `status` is `"collecting"`, the system needs more information. Reply using the same `session_id`:

```bash
curl -s -X POST http://localhost:8000/intake/message \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{
    "message": "30 minutes, virtual",
    "session_id": "01JXXXXXXXXXXXXXXXXXXXXX"
  }' | python -m json.tool
```

When all fields are collected, the response `status` becomes `"ready"` and the `intent` field contains the structured intent.

---

## 7. Full Pipeline Walkthrough

The full pipeline is: **Intake** (collect intent) -> **Planner** (generate plan) -> **ApprovalGate** (preview & approve) -> **ExecuteOrchestrator** (run plan).

The Planner, ApprovalGate, and ExecuteOrchestrator are triggered automatically when the Intake session reaches `"ready"` status. In the current architecture:

1. **Intake** collects a structured intent from conversational turns.
2. **Planner** uses Claude to generate an execution plan.
3. The plan is presented to the user for **preview/approval**.
4. After approval, **ExecuteOrchestrator** runs the plan steps.

### Direct Plan Execution (if you have a plan_id and approval_token)

```bash
curl -s -X POST http://localhost:8000/api/v1/execute \
  -H "Content-Type: application/json" \
  -d '{
    "plan_id": "<plan-id>",
    "approval_token": "<token>"
  }' | python -m json.tool
```

### Query Audit Trail

See what happened during execution:

```bash
curl -s "http://localhost:8000/audit/events?limit=10" \
  -H "Authorization: Bearer $TOKEN" | python -m json.tool
```

---

## 8. Run Tests

Run the full test suite inside the container:

```bash
docker compose exec app pytest --tb=short -q
```

Run a specific component's tests:

```bash
docker compose exec app pytest components/Intake/tests/ -v
docker compose exec app pytest components/Planner/tests/ -v
```

---

## 9. Troubleshooting

### `LLM_API_KEY must be set for provider=<name>`

You selected a provider that requires an API key but didn't set `LLM_API_KEY`.
Edit `.env` and set both variables:

```
LLM_PROVIDER=anthropic     # or openai / gemini
LLM_API_KEY=sk-ant-...
```

If you meant to use the Claude Code CLI (no key), set `LLM_PROVIDER=claude_code`
and make sure the `claude` binary is installed **and logged in** on the host
(`claude login`). When running in Docker, mount `~/.claude` into the container.

### `llm_model_provider_mismatch` warning at startup

You changed `LLM_PROVIDER` but left `PLANNER_PRIMARY_MODEL` /
`PLANNER_FALLBACK_MODEL` / `INTAKE_PARSER_MODEL` pointing at the old provider's
model name. Update the model names to match your new provider (see the table in
section 10 and the per-provider blocks in section 2).

### `Claude Code CLI not found: claude`

The `claude_code` provider shells out to the `claude` binary. Install it:

```bash
npm install -g @anthropic-ai/claude-code
claude login
```

If the binary lives somewhere outside `$PATH`, set `CLAUDE_CODE_BIN` to its
absolute path in `.env`.

### Ollama model pull is slow or fails

The `ollama-pull` sidecar downloads the model on first startup (~2GB for
`llama3.2:3b`). If it fails, restart it:

```bash
docker compose restart ollama-pull
```

If the model is already pulled, the container exits immediately. If Ollama is
unavailable at runtime, Intake falls back to the remote LLM seamlessly — no
user-facing errors.

### Container `app` exits with import errors

Check logs:

```bash
docker compose logs app
```

Common fix: rebuild the image to pick up dependency changes:

```bash
docker compose build --no-cache app
```

### Database migrations fail / table not found

The `migrations/` directory is mounted into PostgreSQL's init directory. It only runs on **first** database creation. To re-run migrations:

```bash
docker compose down -v          # WARNING: deletes all data
docker compose up --build -d
```

### `VectorIndex` / ONNX model warnings

If VectorIndex logs a warning about the ONNX model, the model download may have failed during build. Rebuild:

```bash
docker compose build --no-cache app
```

### Port 8000 already in use

```bash
# Find what's using the port
lsof -i :8000

# Or change the host port in docker-compose.yml:
# ports:
#   - "9000:8000"
```

### Redis connection refused

Ensure the Redis container is healthy:

```bash
docker compose ps redis
docker compose logs redis
```

---

## 10. Environment Variable Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_PROVIDER` | No | `anthropic` | Which LLM adapter to build. One of `anthropic`, `openai`, `gemini`, `claude_code` |
| `LLM_API_KEY` | Conditional | — | API key for the selected provider. **Required** for `anthropic`, `openai`, `gemini`; **ignored** for `claude_code` |
| `LLM_TIMEOUT_S` | No | `60` | Per-call timeout (seconds) for API-key-based providers |
| `PLANNER_PRIMARY_MODEL` | No | `claude-sonnet-4-5-20250929` | Primary LLM model name — must be valid for `LLM_PROVIDER` |
| `PLANNER_FALLBACK_MODEL` | No | `claude-sonnet-4-5-20250929` | Fallback LLM model name — must be valid for `LLM_PROVIDER` |
| `INTAKE_PARSER_MODEL` | No | `claude-sonnet-4-5-20250929` | Model used by Intake entity parser — must be valid for `LLM_PROVIDER` |
| `CLAUDE_CODE_BIN` | No | `claude` | Path to the `claude` binary. Only read when `LLM_PROVIDER=claude_code` |
| `PLANNER_CLAUDE_CODE_TIMEOUT_S` | No | `120` | Timeout for Claude Code CLI calls. Only read when `LLM_PROVIDER=claude_code` |
| `DATABASE_URL` | No | `postgresql+asyncpg://agent:agent@db:5432/personal_agent` | PostgreSQL connection (set by compose) |
| `REDIS_URL` | No | `redis://redis:6379` | Redis connection (set by compose) |
| `ENCRYPTION_KEY` | Yes | dev default in compose | AES-256 base64 key for ProfileStore encryption |
| `JWT_SECRET` | Yes | dev default in compose | JWT signing secret (min 32 chars) |
| `APPROVAL_TOKEN_SECRET` | Yes | dev default in compose | ApprovalGate JWT secret |
| `CREDENTIAL_MASTER_KEY` | No | zero-filled dev default | AES-256-GCM hex key for credential vault |
| `ONNX_MODEL_PATH` | No | `/app/models/model.onnx` | Path to all-MiniLM-L6-v2 ONNX model |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `ENVIRONMENT` | No | `development` | Runtime environment identifier |
| `JWT_EXPIRE_MINUTES` | No | `60` | JWT token expiration in minutes |
| `COMPOSIO_API_KEY` | Conditional | — | Composio API key. **Required** to enable the Integrations page OAuth flow and per-user MCP URL generation. Omit to disable Composio entirely. |
| `COMPOSIO_MCP_CONFIG_ID` | Conditional | — | Composio MCP server config ID. **Required whenever `COMPOSIO_API_KEY` is set** — missing it disables Composio mode with a warning. |
| `COMPOSIO_AUTH_CONFIGS` | No | `{}` | JSON map of provider → `auth_config_id` to pre-populate known OAuth configs |
| `COMPOSIO_SYSTEM_USER_ID` | No | `__system__` | Entity ID used when refreshing the shared tool catalog |
| `COMPOSIO_URL_CACHE_TTL` | No | `3600` | TTL (seconds) for cached per-user Composio MCP URLs |
| `MCP_SERVER_COMPOSIO_URL` | No | — | Composio MCP server URL (enables tool execution — needed alongside the Composio vars above for full tool dispatch) |
| `MCP_SERVER_COMPOSIO_API_KEY` | No | — | Composio API key for the generic MCP client (reuse the same value as `COMPOSIO_API_KEY`) |
| `MCP_SERVERS` | No | — | JSON blob alternative for multiple MCP servers |
| `TOOL_ALLOWLIST` | No | — | Comma-separated tool names to expose (filters `tools/list`) |
| `TOOL_BLOCKLIST` | No | — | Comma-separated tool names to hide |
| `INTAKE_USE_LOCAL_LLM` | No | `true` | Enable local Ollama for Intake intent parsing (`true`/`false`) |
| `OLLAMA_BASE_URL` | No | `http://ollama:11434` | Ollama API endpoint (set by Compose; override for bare-metal) |
| `INTAKE_LOCAL_MODEL` | No | `llama3.2:3b` | Ollama model name for local intent parsing |
| `INTAKE_LOCAL_TIMEOUT_S` | No | `30` | Per-call timeout (seconds) for Ollama requests |

---

## Stopping

```bash
docker compose down       # stop containers, keep data
docker compose down -v    # stop containers AND delete database volume
```
