---
description: Read-only primer. Build an accurate mental model of this repo from its sources of truth and propose the single best next command.
---

/system
Act as a **read-only** primer. Do **not** modify files, run package installs, or open PRs. Your goal is to understand the project from its canonical docs and produce a concise, actionable summary plus the one best next step. Prefer canonical paths over workbench copies. Be specific and include exact file paths in your output when referring to artifacts.

## What to read (in this order)
/user
1) **.specify/memory/constitution.md** — repo laws (no push to main, PR must link SPEC/LLD, CI is the gate)
2) **PROJECT_STRUCTURE.md** — component-first layout & rules
3) **docs/architecture/GLOBAL_SPEC.md** — Intent + Preview/Execute envelopes & NFRs
4) **docs/architecture/Project_HLD.md** — system diagram, boundaries, flows
5) **.github/pull_request_template.md** — required links/checklist
6) **.claude/claude.md** — agent guidance, “paths of truth”
7) **.claude/settings.json** — project permissions/allowlist (tools available)
8) **.github/workflows/ci.yml** — how CI enforces tests & schema validation
9) **pyproject.toml** (or `package.json`) — dependencies, scripts (if present)
10) **README.md** — high-level purpose (if present)
11) **components/** — list existing components and note which are missing SPEC/LLD
12) **Spec Kit fallback only:** `.specify/memory/constitution.md` (symlink to root) and `.specify/templates/*` to understand any workbench flows

## Optional quick scans (read-only helpers)
- Run: `tree -L 2` to see top-level structure; if unavailable, `ls -la` by dirs
- Run: `rg -n --hidden --glob '!**/.git/**' '(LLD\.md|response\.normalized\.json)' components/` to inventory component implementation files
- If available, use Serena search to locate key symbols or files. If Serena fails, fall back to `rg`.

## Output (keep it concise, but precise)
- **Repo laws (1–3 lines):** The key rules from `.specify/memory/constitution.md` that affect PRs & CI.
- **Structure (bullets):** How components are organized (`api/`, `service/`, `domain/`, `adapters/`, `schemas/`, `tests/`), plus global docs/ci.
- **Global contracts:** Summarize the **Intent** input and **Preview/Execute** envelopes from `GLOBAL_SPEC.md`.
- **Key configs & dependencies:** Call out `pyproject.toml` (or `package.json`), `.claude/settings.json`, CI workflow, and anything notable (e.g., UV usage, Ruff, PyTest).
- **Component status:** List any components found under `components/` with whether they have `LLD.md` and `schemas/response.normalized.json`. Note corresponding SPEC files should be in `specs/` directory.
- **Risks/assumptions:** Anything that could block a smooth Plan → Implement → Verify → PR flow.
- **Next recommended command:** Exactly one of:
  - `/specify` (if no SPEC exists for the target component),
  - `/plan` (component-first planner) if SPEC exists but needs LLD/schema/tests,
  - `/flow_orchestrate` (Planner → Implementer → Verifier → PR Manager) when SPEC/LLD exist and you’re ready to implement.

## Constraints
- Read-only; no file edits or network installs.
- Prefer canonical sources: `.specify/memory/constitution.md`, `docs/architecture/*`, `components/<Name>/*`.
- Treat `.specify/*` as a **workbench** only; do not consider it the source of truth.
