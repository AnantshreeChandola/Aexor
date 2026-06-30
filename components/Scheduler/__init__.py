"""
Scheduler Component — One-Time & Recurring Plan Execution

Manages scheduled plans using APScheduler (AsyncIOScheduler) with in-memory
job store and PostgreSQL as source of truth for recovery on restart.

Sub-modules:
    domain/   — Pydantic models, exceptions
    adapters/ — Database CRUD, cron builder utility
    service/  — APScheduler lifecycle, job execution
    api/      — REST endpoints under /api/scheduled-plans
"""

__version__ = "0.1.0"
