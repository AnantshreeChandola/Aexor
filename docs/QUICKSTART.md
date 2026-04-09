# Docker Quickstart

Get the Personal Agent system running end-to-end with Docker in under 5 commands.

---

## 1. Prerequisites

- **Docker** (with Compose v2): [install](https://docs.docker.com/get-docker/)
- **Anthropic API key**: [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys)
- **Composio account** (for tool integrations): [app.composio.dev](https://app.composio.dev/) — sign up and create an MCP server to get your server URL and API key
- **curl** (or any HTTP client)

---

## 2. Configure

```bash
cp .env.example .env
```

Open `.env` and set your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

### Composio (MCP tool integrations)

To enable tool execution (Google Calendar, Gmail, Slack, etc.), add your Composio MCP server credentials:

```
MCP_SERVER_COMPOSIO_URL=https://backend.composio.dev/v3/mcp/YOUR_SERVER_ID
MCP_SERVER_COMPOSIO_API_KEY=your-composio-api-key
```

To get these values:

1. Sign up at [app.composio.dev](https://app.composio.dev/)
2. Go to **Settings > API Keys** and copy your API key
3. Create an MCP server (or use the default one) and copy the server URL from the MCP server page

Without these values the app still starts, but tool invocations will fail with a "server not configured" error. The Intake and Planner components work without Composio — only actual tool execution requires it.

Optionally, restrict which tools are exposed to the planner:

```
TOOL_ALLOWLIST=GOOGLECALENDAR_CREATE_EVENT,GOOGLECALENDAR_FIND_FREE_SLOTS,GMAIL_SEND_EMAIL
```

All other values have safe dev defaults. For production, regenerate `ENCRYPTION_KEY`, `JWT_SECRET`, `APPROVAL_TOKEN_SECRET`, and `CREDENTIAL_MASTER_KEY` using the commands in `.env.example`.

---

## 3. Start

```bash
docker compose up --build -d
```

This builds the app image (downloads the ONNX embedding model, installs deps), starts PostgreSQL 16 (pgvector), Redis 7, and the FastAPI application.

---

## 4. Verify

Wait for all three containers to be healthy:

```bash
docker compose ps
```

Expected output shows `db`, `redis`, and `app` all with status `healthy`.

Check app logs for successful initialization:

```bash
docker compose logs app --tail 30
```

Look for:
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

### `ANTHROPIC_API_KEY: Set ANTHROPIC_API_KEY in .env`

You haven't set the API key. Edit `.env` and add your key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

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
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic Claude API key for Planner and Intake |
| `DATABASE_URL` | No | `postgresql+asyncpg://agent:agent@db:5432/personal_agent` | PostgreSQL connection (set by compose) |
| `REDIS_URL` | No | `redis://redis:6379` | Redis connection (set by compose) |
| `ENCRYPTION_KEY` | Yes | dev default in compose | AES-256 base64 key for ProfileStore encryption |
| `JWT_SECRET` | Yes | dev default in compose | JWT signing secret (min 32 chars) |
| `APPROVAL_TOKEN_SECRET` | Yes | dev default in compose | ApprovalGate JWT secret |
| `CREDENTIAL_MASTER_KEY` | No | zero-filled dev default | AES-256-GCM hex key for credential vault |
| `ONNX_MODEL_PATH` | No | `/app/models/model.onnx` | Path to all-MiniLM-L6-v2 ONNX model |
| `LOG_LEVEL` | No | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `ENVIRONMENT` | No | `development` | Runtime environment identifier |
| `PLANNER_PRIMARY_MODEL` | No | `claude-sonnet-4-5-20250929` | Primary LLM model for Planner |
| `PLANNER_FALLBACK_MODEL` | No | `claude-haiku-4-5-20251001` | Fallback LLM model for Planner |
| `PLANNER_LLM_TIMEOUT_S` | No | `30` | LLM call timeout in seconds |
| `JWT_EXPIRE_MINUTES` | No | `60` | JWT token expiration in minutes |
| `MCP_SERVER_COMPOSIO_URL` | No | — | Composio MCP server URL (enables tool execution) |
| `MCP_SERVER_COMPOSIO_API_KEY` | No | — | Composio API key (service-level auth) |
| `MCP_SERVERS` | No | — | JSON blob alternative for multiple MCP servers |
| `TOOL_ALLOWLIST` | No | — | Comma-separated tool names to expose (filters `tools/list`) |
| `TOOL_BLOCKLIST` | No | — | Comma-separated tool names to hide |

---

## Stopping

```bash
docker compose down       # stop containers, keep data
docker compose down -v    # stop containers AND delete database volume
```
