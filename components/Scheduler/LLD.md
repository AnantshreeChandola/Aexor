# Scheduler — Low-Level Design (LLD)

**Component**: `components/Scheduler/`
**Layer**: Platform / Service Layer
**Type**: API component (REST endpoints + background scheduler)
**Created**: 2026-05-01

---

## 1. Purpose & Scope

Scheduler enables **one-time and recurring plan execution** by managing
APScheduler (AsyncIOScheduler) jobs backed by a PostgreSQL source of truth.
Users create schedules from the plan builder or plan history, and the
Scheduler executes them through the same pipeline as manual execution.

**Responsibilities**:
- Accept schedule creation requests (one-time date or recurring cron)
- Persist schedule configuration to `scheduled_plans` table
- Manage APScheduler job lifecycle (register, pause, resume, delete)
- Execute scheduled plans via Planner → ApprovalGate → ExecuteOrchestrator
- Handle approval gates per configurable mode (`auto_approve` / `notify_and_wait`)
- Recover active schedules on server restart (DB is source of truth)

**Out of scope**:
- Dynamic entity re-evaluation (e.g. resolving "tomorrow" at run time — v2)
- Email/push notifications for `notify_and_wait` mode (logs + UI status only)
- Multi-timezone scheduling per step (single timezone per schedule)

---

## 2. Architecture Overview

### 2.1 Layer Placement

Scheduler sits in the **Platform/Service Layer** alongside Audit and
ExecutionMonitor. It depends on Domain Layer services (Planner,
ApprovalGate, ExecuteOrchestrator) for plan execution.

```
                  ┌───────────────────────┐
                  │   Scheduler API       │  REST endpoints
                  └──────────┬────────────┘
                             ▼
                  ┌───────────────────────┐
                  │  SchedulerService     │  APScheduler lifecycle
                  └──────────┬────────────┘
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        PlannerService  ApprovalGate  ExecuteOrchestrator
              │              │              │
              ▼              ▼              ▼
         (LLM / RAG)    (JWT tokens)   (MCP tools)
```

### 2.2 Job Store Strategy

APScheduler's built-in SQLAlchemy job store uses synchronous DB access,
which conflicts with the project's async-only `asyncpg` setup. Instead:

- **In-memory job store**: APScheduler's default MemoryJobStore
- **PostgreSQL as source of truth**: `scheduled_plans` table
- **Startup recovery**: All `status='active'` schedules are loaded from DB
  and re-registered as APScheduler jobs on server start
- **Misfire grace**: `misfire_grace_time=300` catches up on runs missed
  during restarts (within 5 minutes)

---

## 3. Data Model

### 3.1 Database Table: `scheduled_plans`

Owned by Scheduler component. See `shared/database/models.py`.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID PK | `gen_random_uuid()` |
| `user_id` | UUID FK → users | CASCADE delete |
| `name` | String(255) | Human-readable label |
| `intent_type` | String(64) | e.g. "schedule_meeting" |
| `skeleton_json` | JSONB | Serialized plan skeleton |
| `entities_json` | JSONB | Entity key-value map |
| `constraints_json` | JSONB | Default `{}` |
| `schedule_type` | String(16) | `"once"` or `"recurring"` |
| `scheduled_at` | DateTime(tz) | For one-time (NULL for recurring) |
| `cron_expression` | String(128) | Human-readable display string |
| `recurrence_config` | JSONB | UI-friendly recurrence descriptor |
| `timezone` | String(64) | IANA timezone |
| `status` | String(16) | `active` / `paused` / `completed` / `failed` / `cancelled` |
| `approval_mode` | String(16) | `auto_approve` / `notify_and_wait` |
| `last_run_at` | DateTime(tz) | Last execution time |
| `next_run_at` | DateTime(tz) | Next scheduled run |
| `run_count` | Integer | Default 0 |
| `max_runs` | Integer nullable | NULL = unlimited |
| `last_error` | JSONB nullable | Last error details |
| `source_plan_id` | String(26) nullable | Link to original plan |
| `created_at` | DateTime(tz) | `NOW()` |
| `updated_at` | DateTime(tz) | `NOW()` |

### 3.2 Domain Models

- `ScheduledPlan` — maps to DB table (Pydantic BaseModel)
- `RecurrenceConfig` — UI-friendly recurrence descriptor
- `CreateScheduledPlanRequest` / `UpdateScheduledPlanRequest` — API schemas
- `ScheduledPlanResponse` / `ScheduledPlanListResponse` — API responses

---

## 4. API Endpoints

Prefix: `/api/scheduled-plans`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/scheduled-plans` | POST | Create scheduled plan |
| `/api/scheduled-plans` | GET | List user's scheduled plans |
| `/api/scheduled-plans/{id}` | GET | Get specific schedule |
| `/api/scheduled-plans/{id}` | PATCH | Update (pause/resume/edit) |
| `/api/scheduled-plans/{id}` | DELETE | Delete schedule + remove job |

All routes use `get_auth_context` for user scoping.

---

## 5. Cron Builder

`adapters/cron_builder.py` converts `RecurrenceConfig` to APScheduler
`CronTrigger` kwargs:

| Frequency | Trigger kwargs |
|-----------|----------------|
| hourly | `{"hour": "*/N", "minute": M}` |
| daily | `{"hour": H, "minute": M}` or `{"day": "*/N", ...}` |
| weekly | `{"day_of_week": "0,2,4", "hour": H, "minute": M}` |
| monthly | `{"day": D, "hour": H, "minute": M}` |

Also generates human-readable display strings (e.g. "Every weekday at 09:00").

---

## 6. Execution Pipeline

When APScheduler fires a job, `_execute_scheduled_plan()` runs:

1. Load schedule from DB, verify `status == 'active'`
2. Build `Intent` from stored `intent_type` + `entities_json` + `timezone`
3. Call `planner_service.generate_plan(intent)` → get plan
4. Issue approval token via `approval_service.approve()`
5. Execute via `execute_service.execute_plan(request)`
6. Handle `GateApprovalRequired` based on `approval_mode`:
   - **`auto_approve`**: auto-resolve gate, re-execute
   - **`notify_and_wait`**: record error, pause schedule for user intervention
7. Record outcome via `record_execution()`
8. For one-time: mark `status='completed'`

---

## 7. Approval Gate Handling

| Mode | Behavior |
|------|----------|
| `auto_approve` (default) | Gates are automatically approved; plan runs unattended |
| `notify_and_wait` | On gate hit, schedule is paused with error details; user must manually resume after reviewing |

---

## 8. Concurrency & Safety

| Setting | Value | Purpose |
|---------|-------|---------|
| `max_instances` | 1 | Prevent overlapping runs of same schedule |
| `coalesce` | True | Collapse missed fires into one execution |
| `misfire_grace_time` | 300s | Fire on restart if within 5-minute window |

---

## 9. Frontend Integration

### 9.1 Schedule Modal
Two-tab modal (One time / Recurring) accessible from:
- Builder view "Schedule" button
- Plan history "Schedule" button per successful plan

### 9.2 Scheduled Plans View
Sidebar "Scheduled" tab showing all user's scheduled plans with:
- Status badges (Active/Paused/Completed/Failed)
- Approval mode indicator
- Schedule description and next run time
- Pause/Resume and Delete actions

---

## 10. Testing

- `tests/test_cron_builder.py` — RecurrenceConfig → trigger kwargs + display strings
- `tests/test_service.py` — SchedulerService CRUD + validation with mocked dependencies
