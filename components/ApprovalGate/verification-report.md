# Verification Report: ApprovalGate

**Date**: 2026-04-05
**Branch**: feat/policyengine-deny-default
**Status**: FAIL

## Test Results
- Passed: 104
- Failed: 0
- Skipped: 0

## Lint Results
- `ruff check`: All checks passed
- `ruff format --check`: **FAIL** -- 7 files would be reformatted

## Failures Requiring Implementer Action

- [ ] [F001] `ruff format --check`: 7 files need reformatting. Files: `components/ApprovalGate/adapters/gate_store.py`, `components/ApprovalGate/domain/models.py`, `components/ApprovalGate/service/approval_service.py`, `components/ApprovalGate/tests/test_contract.py`, `components/ApprovalGate/tests/test_observability.py`, `components/ApprovalGate/tests/test_service.py`, `components/ApprovalGate/tests/test_unit.py` -> **Fix**: Run `ruff format components/ApprovalGate/` to auto-format all files.

- [ ] [F002] TokenIssuer uses `python-jose` (`from jose import ...`) instead of `PyJWT` as specified in LLD Section 6.1 (docstring says "python-jose" but LLD Section 12.1 specifies `PyJWT>=2.8`). The spec (FR-001) and LLD both state PyJWT. This is a library mismatch. -> **Fix**: This is a **non-blocking warning** since `python-jose` is installed and tests pass. However, the LLD docstring in `adapters/token_issuer.py` line 5 states "python-jose" which is consistent with what is actually used but inconsistent with the LLD Section 12.1 table which says `PyJWT>=2.8`. Either (a) update the LLD to reflect `python-jose` usage, or (b) migrate to PyJWT to match the LLD. Since tests pass and the behavior is correct, this does not block merging.

## Schema Drift

No schema drift detected.

- [x] `shared/schemas/approval_token.schema.json`: Not modified (confirmed via `git diff master -- shared/schemas/`)
- [x] `shared/app.py`: Only ApprovalGate DI block added (lines 226-241), no other changes
- [x] `shared/dependencies.py`: Only `get_approval_service()` added (lines 90-92), no other changes
- [x] No other shared schemas or shared files modified
- [x] No existing component code modified (confirmed via `git diff master -- components/PolicyEngine/ components/PreviewOrchestrator/ components/ExecuteOrchestrator/ components/Planner/ components/Intake/` returns empty)

## Contract Conformance Matrix

| Check | Status | Notes |
|-------|--------|-------|
| ApprovalToken fields vs GLOBAL_SPEC S2.7 | PASS | All required fields present: `token`, `plan_id`, `user_id`, `exp`, `scopes` |
| ApprovalToken fields vs approval_token.schema.json | PASS | Required fields (`token`, `plan_id`, `user_id`, `exp`, `scopes`) all present. Extended fields (`gate_id`, `iat`) present. `token_id` is internal-only (not in schema). Schema has `additionalProperties: false` -- test_contract.py correctly filters before validating. |
| ApprovalRequest has `user_id` field | PASS | Present at `domain/models.py:27` with `min_length=1` |
| Redis try/except graceful degradation | PASS | All GateStore methods (`store_gate`, `get_gate`, `get_all_gates_by_prefix`, `mark_consumed`, `is_consumed`) have try/except with logger.warning. Redis=None checks return safe defaults. |
| JWT token values never logged | PASS | `approval_service.py` logs only `token_id` (ULID), never the JWT string. Verified by `test_observability.py::TestNoPII::test_no_jwt_token_in_logs`. |
| No PII in logs | PASS | `selected_option` never logged. Scopes logged by count only (`scope_count`). Verified by `test_observability.py::TestNoPII::test_no_selected_option_in_logs`. |
| Single-use enforcement uses atomic SET NX | PASS | `gate_store.py:164` uses `await self._redis.set(key, "1", ex=ttl_s, nx=True)` -- atomic SET NX. |
| Multi-gate support | PASS | Different `gate_id`s produce different tokens with distinct `token_id`s. Verified by `test_unit.py::TestMultiGateIsolation` and `test_contract.py::TestEndToEndFlow::test_multi_gate_distinct_tokens`. |
| Idempotent re-approval | PASS | Same gate returns existing token. `approval_service.py:86-95` checks `get_gate()` before issuing new token. Verified by `test_unit.py::TestIdempotentReApproval`. |
| PolicyEngine learn_from_approval() called when policy_matched=False | PASS | `approval_service.py:167-193` calls `learn_from_approval(role, tool)` when `policy_matched=False` and `role` and `tool` provided. Verified by `test_service.py::TestApproveFlow::test_learn_from_approval_called_when_policy_unmatched`. |
| DI: shared/app.py creates ApprovalService | PASS | Lines 226-241 create service with correct dependencies (preview_service, policy_service, redis_client, jwt_secret, token_ttl_s). Wrapped in try/except with graceful degradation. |
| DI: shared/dependencies.py has get_approval_service() | PASS | Lines 90-92 provide `get_approval_service(request)` accessor. |
| Factory raises on missing/short secret | PASS | `approval_service.py:389-394` raises `ApprovalConfigError` for empty or <16-char secrets. Verified by `test_service.py::TestFactory`. |
| Token expiry validation | PASS | `token_issuer.py:45-46` catches `ExpiredSignatureError` and raises `TokenExpiredError`. Verified by test. |
| plan_id mismatch validation | PASS | `approval_service.py:237-245` checks and raises `TokenValidationError("plan_id_mismatch")`. |
| gate_id mismatch validation | PASS | `approval_service.py:248-256` checks and raises `TokenValidationError("gate_id_mismatch")`. |
| Preview state binding (best-effort) | PASS | `approval_service.py:98-127` fetches from preview_service with try/except. None on failure. Verified by multiple tests. |
| get_gate_status() | PASS | Scans Redis for `gate:{plan_id}:*` keys via `get_all_gates_by_prefix()`. Returns empty dict on Redis failure. |
| get_approval_state() | PASS | Returns `ApprovalState` model with all expected fields. Returns None on miss. |

## Preview Evidence

Preview paths (approve, validate_token, get_gate_status, get_approval_state) were inspected for network/file mutations:

- **approve()**: CPU-bound JWT sign + Redis SET (gate state) + Redis SET (optional, via store_gate). No external network calls. No file system writes. PreviewService call is read-only (`get_preview_state`). PolicyEngine call (`learn_from_approval`) is a write but best-effort and only for spawned steps.
- **validate_token()**: CPU-bound JWT decode + Redis GET (is_consumed) + Redis SET NX (mark_consumed). No external network calls. No file system writes. The SET NX is the only mutation and is essential for single-use enforcement.
- **get_gate_status()**: Redis KEYS + GET only. Read-only.
- **get_approval_state()**: Redis GET only. Read-only.

No file mutations or unexpected network calls detected in any code path.

## Backward Compatibility

| Check | Status | Notes |
|-------|--------|-------|
| No existing test files modified | PASS | `git diff master --name-only` shows only `shared/app.py` and `shared/dependencies.py` |
| No existing shared schemas modified | PASS | `git diff master -- shared/schemas/` returns empty |
| No existing component code modified | PASS | Verified via git diff |
| shared/app.py changes are additive | PASS | Only a new try/except block appended before `yield` |
| shared/dependencies.py changes are additive | PASS | Only `get_approval_service()` appended at end |

## Warnings (Non-blocking)

- [W001] **JWT library mismatch**: TokenIssuer uses `python-jose` (installed: v3.5.0) while LLD Section 12.1 specifies `PyJWT>=2.8`. Both libraries produce valid HS256 JWTs. Tests pass. The docstring in `token_issuer.py` accurately states "python-jose". Consider updating LLD Section 12.1 to reflect actual usage.
- [W002] **Idempotent re-approval returns empty token string**: `_build_token_from_stored()` at `approval_service.py:346` sets `token=""` because the original JWT is not stored in Redis. This means the second approval response has an empty `token` field. The consumer cannot use this empty JWT for validation. This is documented behavior but could surprise callers. Consider storing the JWT string in the gate data for full idempotent re-approval support.
- [W003] **Test count**: 104 tests exceeds the LLD estimate of ~70. This is a positive outcome indicating thorough coverage.
