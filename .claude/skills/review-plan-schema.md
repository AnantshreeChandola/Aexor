---
description: Validate a plan JSON against GLOBAL_SPEC requirements and HLD architecture.
---

Read first:
- `/docs/architecture/GLOBAL_SPEC.md` for plan schema requirements
- `/docs/architecture/Project_HLD.md` for architecture context

## Validation Checklist

### Required Plan Fields
- [ ] `plan_id` (format: `01HX...` Ed25519-based)
- [ ] `user_id`
- [ ] `intent` (original user request)
- [ ] `graph` (array of steps)
- [ ] `signature` (Ed25519 signature)

### Step Validation
For each step in `graph`:
- [ ] `step` (sequential number: 1, 2, 3, ...)
- [ ] `type` (api/llm_reasoning/policy_check — defaults to "api")
- [ ] `mode` (autonomous/interactive/supervised)
- [ ] `role` (one of 7: Fetcher, Analyzer, Watcher, Resolver, Booker, Notifier, Reasoner)
- [ ] `uses` (connector name, e.g., "google.calendar")
- [ ] `call` (method name)
- [ ] `args` (object with parameters)
- [ ] `after` (dependency array, e.g., [1, 2])
- [ ] `context_from` (optional — steps whose output is passed as context)
- [ ] `can_spawn` (optional — whether step can create new steps at runtime)
- [ ] `max_spawned_steps` (optional — max new steps, default 3, max 10)
- [ ] `policy_ref` (optional — policy rule governing this step)
- [ ] `reasoning_config` (optional — LLM config for reasoning steps)

### Preview-First Safety
- [ ] First step OR preview steps have `"execute_mode": "preview_only"` and `"dry_run": true`
- [ ] Execution steps have `"execute_mode": "execute_only"`
- [ ] Interactive steps have `gate_id` (e.g., "gate-A")
- [ ] No mutations in preview mode

### Determinism
- [ ] Same inputs → same plan → same signature
- [ ] No timestamps in plan (use relative time like "next Tuesday")
- [ ] No random values or UUIDs generated at plan-time

### Preview State Caching
- [ ] Steps marked `"execute_mode": "preview_only"` run in preview only
- [ ] Execution steps reference cached state: `"{{preview.cached_state.field}}"`
- [ ] User selections from preview are reused in execute

### Multi-Gate HITL (if applicable)
- [ ] Multiple gates properly sequenced: gate-A, gate-B, gate-C
- [ ] Each gate has clear approval criteria
- [ ] Gates appear in execution steps only (not preview)

### Dependency Graph
- [ ] No circular dependencies (A → B → A)
- [ ] `after` array is valid (references earlier steps)
- [ ] Parallel steps have `after: []`

## Validation Output Format

```markdown
## Plan Validation Results

### ✓ Passed
- Plan ID format valid: 01HX...
- All steps have valid roles
- Preview-first safety: gate-A present

### ✗ Failed
- **Step 3**: Missing `after` dependency (should be [1, 2])
- **Step 5**: Invalid role "Executor" (should be one of 6 runtime roles)

### ⚠️ Warnings
- Step 2 uses timestamps (prefer relative time)
- No compensation declared for create_event operation
```

## Common Issues

**Missing dependencies**: Step X depends on step Y data but `after` is empty
**Fix**: Add `"after": [Y]`

**Invalid role**: Role name doesn't match the 7 runtime roles
**Fix**: Use Fetcher, Analyzer, Watcher, Resolver, Booker, Notifier, or Reasoner

**No preview step**: Plan executes without preview
**Fix**: Add preview step with `"execute_mode": "preview_only"`

**Circular dependencies**: Step A → Step B → Step A
**Fix**: Redesign dependency graph to be acyclic

**Missing gate_id**: Interactive step needs approval but no gate
**Fix**: Add `"gate_id": "gate-A"` to step

**Preview mutation**: Preview step calls write operation
**Fix**: Mark step as `"execute_mode": "execute_only"` OR use dry_run/mock
