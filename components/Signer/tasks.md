# Tasks: Signer

**Created**: 2026-03-16
**Branch**: feat/signer
**SPEC**: specs/007-signer/spec.md
**LLD**: components/Signer/LLD.md

## Task Organization

Tasks are organized by implementation phase, following the LLD architecture.
The Signer is a **library** (no API routes, no database tables). Phases reflect
the dependency order: domain models first, then adapters (canonicalizer), then
service logic, then DI integration, then tests.

---

## Phase 0: Setup and Scaffolding

### T001 -- Create package structure and __init__.py files

**Description**: Create all directory __init__.py files for the Signer
component package tree. No logic, just empty package markers so imports work.

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/domain/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/service/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/adapters/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/tests/__init__.py`

**Dependencies**: None (first task)
**Acceptance criteria**: `from components.Signer` imports without error.
**Satisfies**: Scaffolding prerequisite for all subsequent tasks.

---

### T002 -- Verify existing dependencies in pyproject.toml

**Description**: Confirm that `cryptography>=41.0`, `ulid-py>=1.1.0`, and
`pydantic>=2.0` are already listed in `pyproject.toml` dependencies. Also
confirm `jsonschema` is available in dev dependencies for contract tests.
No new packages need to be added.

**Files to inspect (read-only)**:
- `/Users/anantshreechandola/Desktop/Personal-agent/pyproject.toml`

**Dependencies**: None
**Acceptance criteria**: All three packages present in `[project] dependencies`.
`jsonschema` present in `[dependency-groups] dev`.
**Satisfies**: LLD Section 9.1 (Python Packages).

---

## Phase 1: Domain Models (Foundation)

### T003 -- Implement PlanSignature Pydantic model

**Description**: Create the `PlanSignature` Pydantic v2 model with all seven
fields matching GLOBAL_SPEC v2.2 Section 2.4 and
`shared/schemas/signature.schema.json`. Fields: `algo`, `signer`, `ts`,
`nonce`, `signature`, `pubkey_id`, `plan_hash`. Use `Field()` with
descriptions, min/max length constraints, and a default of `"Ed25519"` for
`algo`. The model must serialize to JSON that validates against the shared
schema.

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/domain/models.py`

**Dependencies**: T001 (package structure)
**Acceptance criteria**:
- `PlanSignature` model can be instantiated with all required fields.
- `model_dump()` output matches all field names in GLOBAL_SPEC Section 2.4.
- `plan_hash` is constrained to exactly 64 hex characters.
- `nonce` field accepts 26-character ULID strings.
**Satisfies**: FR-001 (signature conformance), FR-003 (plan_hash field),
FR-005 (nonce and ts fields).

---

### T004 -- Implement error classes

**Description**: Create the Signer-specific exception hierarchy as defined
in LLD Section 5.2. Four classes:
1. `SignerError(Exception)` -- base class
2. `SigningKeyNotConfiguredError(SignerError)` -- with `reason` attribute
3. `InvalidSignatureError(SignerError)` -- with `reason` attribute
4. `UnsupportedAlgorithmError(SignerError)` -- with `algo` attribute

All error classes must include a descriptive `__init__` that sets attributes
and calls `super().__init__()` with a formatted message.

**Files to modify**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/domain/models.py`
  (add error classes below PlanSignature, or create a separate errors.py --
  implementer may choose; LLD shows them in the same file)

**Dependencies**: T001
**Acceptance criteria**:
- Each error class is importable from `components.Signer.domain.models`.
- `InvalidSignatureError("hash_mismatch").reason` returns `"hash_mismatch"`.
- `UnsupportedAlgorithmError("RSA").algo` returns `"RSA"`.
- `SigningKeyNotConfiguredError()` has default reason `"Key not configured"`.
**Satisfies**: FR-006 (structured errors).

---

## Phase 2: Adapters (Canonicalizer)

### T005 -- Implement canonicalize_plan() and compute_plan_hash()

**Description**: Create the canonicalizer adapter with two pure functions:

1. `canonicalize_plan(plan_data: dict[str, Any]) -> str`
   - Uses `json.dumps(plan_data, sort_keys=True, separators=(",", ":"))`
   - Returns the canonical JSON string

2. `compute_plan_hash(plan_data: dict[str, Any]) -> str`
   - Calls `canonicalize_plan()` internally
   - Encodes the result as UTF-8 bytes
   - Returns `hashlib.sha256(...).hexdigest()` (64-char hex string)

Both functions are deterministic: same input dict produces same output every
time. This is the critical invariant for the entire Signer.

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/adapters/canonicalizer.py`

**Dependencies**: T001 (package structure)
**Acceptance criteria**:
- `canonicalize_plan({"b": 1, "a": 2})` returns `'{"a":2,"b":1}'`
- `compute_plan_hash(data)` returns a 64-character lowercase hex string
- Calling `compute_plan_hash()` twice with the same dict returns the same hash
- Different dicts produce different hashes
**Satisfies**: FR-002 (deterministic canonicalization), FR-003 (SHA-256 hash).

---

## Phase 3: Service Layer (SignerService)

### T006 -- Implement SignerService.sign_plan()

**Description**: Implement the `sign_plan()` async method on `SignerService`.

Constructor takes `private_key: Ed25519PrivateKey`,
`public_key: Ed25519PublicKey`, and `pubkey_id: str = "k1"`.

`sign_plan(plan_data, signer_identity="planner@system")` performs:
1. Validate `plan_data` is not None/empty -- raise `ValueError` if so
2. Call `canonicalize_plan(plan_data)` to get canonical JSON string
3. Call `compute_plan_hash(plan_data)` to get SHA-256 hex digest
4. Encode canonical JSON to UTF-8 bytes
5. Sign the bytes with `self._private_key.sign(canonical_bytes)`
6. Base64-encode the signature
7. Generate ULID nonce via `ulid.new().str`
8. Generate ISO 8601 timestamp via `datetime.now(UTC).isoformat()`
9. Return `PlanSignature(algo="Ed25519", signer=signer_identity, ts=ts,
   nonce=nonce, signature=sig_b64, pubkey_id=self._pubkey_id,
   plan_hash=plan_hash)`

Add structured logging: log `plan_hash`, `pubkey_id`, `signer`, `nonce`
on success. Never log private key bytes or full plan content.

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/service/signer_service.py`

**Dependencies**: T003 (PlanSignature model), T004 (error classes),
T005 (canonicalizer)
**Acceptance criteria**:
- Returns a valid `PlanSignature` instance for any non-empty dict.
- `plan_hash` in returned signature matches `compute_plan_hash(plan_data)`.
- `algo` is always `"Ed25519"`.
- `nonce` is a 26-character ULID string.
- `ts` is a valid ISO 8601 datetime string.
- `signature` is a non-empty base64 string.
- Raises `ValueError` for empty or None plan_data.
**Satisfies**: FR-001, FR-002, FR-003, FR-005 (signing with replay protection).

---

### T007 -- Implement SignerService.verify_signature()

**Description**: Implement the `verify_signature()` async method.

`verify_signature(plan_data, signature_data)` performs:
1. Parse `signature_data["algo"]` -- if not `"Ed25519"`, raise
   `UnsupportedAlgorithmError(algo)`
2. Call `compute_plan_hash(plan_data)` to get the recomputed hash
3. Compare recomputed hash with `signature_data["plan_hash"]` --
   if mismatch, raise `InvalidSignatureError(reason="hash_mismatch")`
4. Base64-decode `signature_data["signature"]` -- if malformed,
   raise `InvalidSignatureError(reason="malformed_signature")`
5. Call `canonicalize_plan(plan_data)` to get canonical bytes
6. Call `self._public_key.verify(decoded_sig, canonical_bytes)` --
   if `cryptography.exceptions.InvalidSignature` is raised, catch it and
   raise `InvalidSignatureError(reason="signature_verification_failed")`
7. Return `True`

Add structured logging: log hash comparisons on failure (but never log
full plan content or private keys).

**Files to modify**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/service/signer_service.py`

**Dependencies**: T006 (sign_plan must exist first so the full class is
in place)
**Acceptance criteria**:
- Returns `True` for a validly signed plan.
- Raises `InvalidSignatureError(reason="hash_mismatch")` when plan is
  tampered after signing.
- Raises `UnsupportedAlgorithmError` when algo is not `"Ed25519"`.
- Raises `InvalidSignatureError(reason="malformed_signature")` when
  signature base64 is corrupted.
- Raises `InvalidSignatureError(reason="signature_verification_failed")`
  when signature bytes are wrong but base64 is valid.
**Satisfies**: FR-004 (signature verification), FR-006 (structured errors).

---

### T008 -- Implement create_signer_service() factory function

**Description**: Implement the factory function that loads Ed25519 keys from
environment variables and returns a configured `SignerService`.

The function reads:
- `PLAN_SIGNING_PRIVATE_KEY` -- hex-encoded Ed25519 private key (32 bytes)
- `PLAN_SIGNING_PUBLIC_KEY` -- hex-encoded Ed25519 public key (32 bytes)

If either is missing or invalid, raise `SigningKeyNotConfiguredError` with
a descriptive reason.

Use `Ed25519PrivateKey.from_private_bytes(bytes.fromhex(hex_str))` and
`Ed25519PublicKey.from_public_bytes(bytes.fromhex(hex_str))` from
`cryptography.hazmat.primitives.asymmetric.ed25519`.

Place this function in the same `signer_service.py` file, below the class.

**Files to modify**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/service/signer_service.py`

**Dependencies**: T006, T007 (the full SignerService class)
**Acceptance criteria**:
- With valid hex env vars set, returns a working `SignerService`.
- With missing env vars, raises `SigningKeyNotConfiguredError`.
- With invalid hex (odd length, non-hex chars), raises
  `SigningKeyNotConfiguredError`.
**Satisfies**: FR-007 (key loading from env vars).

---

## Phase 4: Dependency Injection Integration

### T009 -- Add get_signer_service() to shared/dependencies.py

**Description**: Add a `get_signer_service()` dependency function to
`shared/dependencies.py` that retrieves the `SignerService` from
`request.app.state.signer_service`. Follow the existing pattern used by
`get_plan_service()`, `get_registry_service()`, etc.

**Files to modify**:
- `/Users/anantshreechandola/Desktop/Personal-agent/shared/dependencies.py`

**Dependencies**: T008 (factory function exists)
**Acceptance criteria**:
- `get_signer_service(request)` returns `request.app.state.signer_service`.
- Function signature matches existing patterns in the file.
- Import type annotation uses `Any` (consistent with existing code).
**Satisfies**: LLD Section 7.4 (Dependency Injection Integration).

---

### T010 -- Register SignerService in shared/app.py lifespan

**Description**: Add Signer initialization to the `lifespan()` function in
`shared/app.py`. Import `create_signer_service` from
`components.Signer.service.signer_service` (lazy import inside lifespan,
following existing pattern). Call `create_signer_service()` and store result
on `app.state.signer_service`.

Place the Signer initialization after the shared database setup and before
or alongside the PlanLibrary initialization, since Signer has no database
dependency.

**Files to modify**:
- `/Users/anantshreechandola/Desktop/Personal-agent/shared/app.py`

**Dependencies**: T008, T009
**Acceptance criteria**:
- `app.state.signer_service` is set during lifespan startup.
- Application starts successfully when valid key env vars are set.
- Application fails to start with `SigningKeyNotConfiguredError` when keys
  are missing.
**Satisfies**: LLD Section 7.4 (app.state integration).

---

## Phase 5: Test Fixtures

### T011 -- Create test fixtures and conftest.py

**Description**: Create `conftest.py` with shared test fixtures:

1. `test_key_pair` fixture -- generates a fresh Ed25519 key pair using
   `Ed25519PrivateKey.generate()` for deterministic test isolation
2. `signer_service` fixture -- creates a `SignerService` from the test key pair
3. `sample_plan` fixture -- returns a realistic plan dict matching
   GLOBAL_SPEC Section 2.3 structure (with `plan_id`, `intent`, `graph`,
   `constraints`, `meta` fields)
4. `sample_plan_minimal` fixture -- returns a minimal valid dict `{"step": 1}`
5. `signed_plan` fixture -- returns a tuple of `(plan_data, signature)` by
   signing `sample_plan` with the test `signer_service`

All fixtures should be `pytest.fixture` decorated. The key pair fixture
should have `scope="session"` for performance; the `signer_service` fixture
should have `scope="session"` as well. The `signed_plan` fixture can be
function-scoped since each signature is unique.

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/tests/conftest.py`

**Dependencies**: T006, T007 (SignerService must be implemented)
**Acceptance criteria**:
- `signer_service` fixture returns a functional `SignerService`.
- `sample_plan` fixture returns a dict with required plan fields.
- `signed_plan` fixture returns a `(dict, PlanSignature)` tuple.
- Fixtures are importable by all test files in the `tests/` directory.
**Satisfies**: Testing prerequisite for all test tasks.

---

## Phase 6: Unit Tests

### T012 -- Write unit tests for canonicalizer

**Description**: Write unit tests for `canonicalize_plan()` and
`compute_plan_hash()` in a dedicated test file. Test cases:

1. `test_canonicalize_sorted_keys` -- keys are sorted regardless of input order
2. `test_canonicalize_no_whitespace` -- no spaces in output
3. `test_canonicalize_deterministic` -- same dict gives same string every time
4. `test_canonicalize_nested_objects` -- nested dicts also sorted
5. `test_compute_hash_returns_64_hex_chars` -- length and character validation
6. `test_compute_hash_deterministic` -- same dict gives same hash
7. `test_compute_hash_different_inputs` -- different dicts give different hashes
8. `test_canonicalize_handles_lists` -- list order preserved (not sorted)
9. `test_canonicalize_handles_special_chars` -- unicode, escaping

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/tests/test_canonicalizer.py`

**Dependencies**: T005 (canonicalizer), T001 (package structure)
**Acceptance criteria**: All tests pass. 100% coverage of canonicalizer.py.
**Satisfies**: FR-002, FR-003, SC-004 (determinism).

---

### T013 -- Write unit tests for sign_plan()

**Description**: Write unit tests for `SignerService.sign_plan()`. Test cases:

1. `test_sign_returns_plan_signature` -- return type is `PlanSignature`
2. `test_sign_algo_is_ed25519` -- `algo` field is `"Ed25519"`
3. `test_sign_plan_hash_deterministic` -- same plan dict gives same `plan_hash`
4. `test_sign_nonce_is_unique` -- two signatures of same plan have different nonces
5. `test_sign_ts_is_unique` -- two signatures have different timestamps
6. `test_sign_signature_is_base64` -- signature field is valid base64
7. `test_sign_pubkey_id_matches_constructor` -- uses the `pubkey_id` from init
8. `test_sign_signer_identity_default` -- default is `"planner@system"`
9. `test_sign_signer_identity_custom` -- custom identity is used
10. `test_sign_empty_plan_raises_value_error` -- `{}` raises ValueError
11. `test_sign_none_plan_raises_value_error` -- `None` raises ValueError
12. `test_sign_plan_hash_matches_compute_plan_hash` -- hash matches
    `compute_plan_hash()` output for the same dict

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/tests/test_unit_signer.py`

**Dependencies**: T006 (sign_plan), T011 (fixtures)
**Acceptance criteria**: All tests pass with the `signer_service` fixture.
**Satisfies**: FR-001, FR-002, FR-003, FR-005, SC-004 (deterministic hash).

---

### T014 -- Write unit tests for verify_signature()

**Description**: Add verify-specific tests to `test_unit_signer.py`. Test cases:

1. `test_verify_valid_signature_returns_true` -- sign then verify succeeds
2. `test_verify_tampered_plan_raises_invalid_signature` -- modify one field
   after signing, verify raises `InvalidSignatureError(reason="hash_mismatch")`
3. `test_verify_wrong_algo_raises_unsupported` -- pass `algo="RSA"`, raises
   `UnsupportedAlgorithmError`
4. `test_verify_malformed_base64_raises_invalid` -- corrupt the signature
   base64 string, raises `InvalidSignatureError(reason="malformed_signature")`
5. `test_verify_wrong_signature_bytes` -- valid base64 but wrong bytes,
   raises `InvalidSignatureError(reason="signature_verification_failed")`
6. `test_verify_plan_hash_mismatch_explicit` -- manually set a wrong
   `plan_hash` in signature_data, raises `InvalidSignatureError`
7. `test_verify_different_key_fails` -- sign with key A, verify with service
   using key B, raises `InvalidSignatureError`

**Files to modify**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/tests/test_unit_signer.py`

**Dependencies**: T007 (verify_signature), T013 (sign tests already in file)
**Acceptance criteria**: All tests pass. Covers all error paths in
`verify_signature()`.
**Satisfies**: FR-004, FR-006, SC-005 (tamper detection).

---

### T015 -- Write unit tests for create_signer_service() factory

**Description**: Write tests for the factory function. Test cases:

1. `test_factory_creates_service_with_valid_keys` -- set both env vars to
   valid hex-encoded keys, verify a `SignerService` is returned
2. `test_factory_missing_private_key_raises` -- unset `PLAN_SIGNING_PRIVATE_KEY`,
   raises `SigningKeyNotConfiguredError`
3. `test_factory_missing_public_key_raises` -- unset `PLAN_SIGNING_PUBLIC_KEY`,
   raises `SigningKeyNotConfiguredError`
4. `test_factory_invalid_hex_raises` -- set env var to non-hex string,
   raises `SigningKeyNotConfiguredError`
5. `test_factory_wrong_key_length_raises` -- set env var to hex of wrong
   length (e.g., 16 bytes instead of 32), raises `SigningKeyNotConfiguredError`

Use `monkeypatch.setenv()` and `monkeypatch.delenv()` for env var control.
Generate valid test keys with `Ed25519PrivateKey.generate()` and extract
hex bytes for the valid-key test case.

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/tests/test_factory.py`

**Dependencies**: T008 (factory function), T011 (fixtures)
**Acceptance criteria**: All tests pass. Covers all error paths in factory.
**Satisfies**: FR-007 (key loading from env).

---

## Phase 7: Contract Tests

### T016 -- Write contract tests against signature.schema.json

**Description**: Write contract tests that validate `PlanSignature` output
against the shared JSON schema at
`shared/schemas/signature.schema.json`. Test cases:

1. `test_signature_output_conforms_to_schema` -- sign a plan, dump to dict,
   validate against `signature.schema.json` using `jsonschema.validate()`
2. `test_signature_algo_field_matches_enum` -- verify `algo` is in the
   schema's `enum: ["Ed25519"]`
3. `test_signature_nonce_matches_ulid_pattern` -- verify nonce matches
   regex `^[0-9A-HJKMNP-TV-Z]{26}$`
4. `test_signature_plan_hash_matches_hex_pattern` -- verify plan_hash
   matches regex `^[a-f0-9]{64}$`
5. `test_signature_pubkey_id_matches_pattern` -- verify pubkey_id matches
   regex `^k[0-9]+$`
6. `test_signature_signer_matches_pattern` -- verify signer matches
   regex `^[a-zA-Z0-9_-]+@[a-zA-Z0-9_-]+$`
7. `test_signature_no_additional_properties` -- verify `model_dump()` does
   not include extra fields beyond what the schema defines

Load the schema file with `json.load()`. Use `jsonschema` library for
validation.

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/tests/test_contract.py`

**Dependencies**: T006 (sign_plan), T011 (fixtures)
**Acceptance criteria**: All tests pass. Schema validation succeeds for
every signature produced.
**Satisfies**: SC-003 (100% schema conformance), GLOBAL_SPEC Section 2.4.

---

## Phase 8: Integration Tests

### T017 -- Write integration tests (sign-then-verify roundtrip)

**Description**: Write integration tests that exercise the full sign-then-verify
flow end to end. Test cases:

1. `test_sign_then_verify_roundtrip` -- sign a plan, then verify with the
   same service, assert returns `True`
2. `test_sign_then_verify_with_complex_plan` -- use a realistic multi-step
   plan graph with nested args, lists, and constraints
3. `test_audit_verification_scenario` -- simulate the audit flow: sign a
   plan, serialize the plan and signature to JSON (as if stored in DB),
   deserialize, verify with Signer -- must return `True`
4. `test_audit_detects_post_storage_tampering` -- sign, serialize, modify
   one field in the serialized plan, deserialize, verify -- must raise
   `InvalidSignatureError`
5. `test_replay_protection_unique_nonces` -- sign the same plan 10 times,
   collect all nonces, assert all are unique
6. `test_multiple_plans_independent_signatures` -- sign two different plans,
   verify each with its own signature succeeds, cross-verify fails

**Files to create**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/tests/test_integration.py`

**Dependencies**: T006, T007, T011 (fixtures)
**Acceptance criteria**: All tests pass. Demonstrates full sign/verify
lifecycle and audit scenarios.
**Satisfies**: User Story 1, User Story 2, User Story 3 (replay protection),
User Story 4 (audit verification), SC-005 (tamper detection).

---

## Phase 9: Observability

### T018 -- Add structured logging to SignerService

**Description**: Ensure structured logging is present in `sign_plan()` and
`verify_signature()` as specified in LLD Section 10.1. Use the standard
`logging` module with `logging.getLogger("signer")`.

Log events:
- `sign_plan` success: log `plan_hash`, `pubkey_id`, `signer`, `nonce`
- `verify_signature` success: log `plan_hash`, `pubkey_id`
- `verify_signature` failure: log `reason`, `plan_hash_expected`,
  `plan_hash_computed`

Verify that:
- Private key bytes are **never** logged
- Full plan content is **never** logged
- Full signature bytes are **never** logged (base64 OK, raw bytes not OK)

This task may be partially done during T006/T007 implementation. This task
ensures completeness and adds a test to verify no PII/secrets in logs.

**Files to modify**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/service/signer_service.py`

**Dependencies**: T006, T007
**Acceptance criteria**:
- Signing produces an INFO log with `plan_hash`, `pubkey_id`, `signer`, `nonce`.
- Verification success produces an INFO log with `plan_hash`, `pubkey_id`.
- Verification failure produces a WARNING log with `reason`.
- No private key bytes appear in any log output.
**Satisfies**: GLOBAL_SPEC Section 8 (observability), Constitution Section VI.

---

### T019 -- Verify no PII/secrets in logs (test)

**Description**: Write a test that captures log output during sign and verify
operations and asserts that no private key material appears. Use `caplog`
pytest fixture.

Test cases:
1. `test_sign_does_not_log_private_key` -- sign a plan, check captured logs
   do not contain the hex-encoded private key
2. `test_sign_does_not_log_full_plan` -- sign a plan with known content,
   verify the full plan JSON does not appear in logs
3. `test_verify_failure_does_not_log_full_plan` -- trigger a verification
   failure, verify logs contain reason but not full plan content

**Files to create or modify**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/tests/test_observability.py`

**Dependencies**: T018, T011 (fixtures)
**Acceptance criteria**: All tests pass.
**Satisfies**: Constitution Section VI (no secrets/PII in logs).

---

## Phase 10: Component Export

### T020 -- Configure component __init__.py exports

**Description**: Update `components/Signer/__init__.py` to re-export the
key public API symbols for consumer convenience:

```python
from components.Signer.domain.models import (
    PlanSignature,
    SignerError,
    SigningKeyNotConfiguredError,
    InvalidSignatureError,
    UnsupportedAlgorithmError,
)
from components.Signer.service.signer_service import (
    SignerService,
    create_signer_service,
)
```

Also update `components/Signer/domain/__init__.py`,
`components/Signer/service/__init__.py`, and
`components/Signer/adapters/__init__.py` with appropriate re-exports.

**Files to modify**:
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/domain/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/service/__init__.py`
- `/Users/anantshreechandola/Desktop/Personal-agent/components/Signer/adapters/__init__.py`

**Dependencies**: T006, T007, T008 (all implementation complete)
**Acceptance criteria**:
- `from components.Signer import SignerService, PlanSignature` works.
- `from components.Signer import create_signer_service` works.
- `from components.Signer import InvalidSignatureError` works.
**Satisfies**: Component-first architecture (clean public API).

---

## Task Summary

| Phase | Tasks | IDs | Count |
|-------|-------|-----|-------|
| Phase 0: Setup | Package structure, dependency check | T001-T002 | 2 |
| Phase 1: Domain | PlanSignature model, error classes | T003-T004 | 2 |
| Phase 2: Adapters | Canonicalizer (canonicalize + hash) | T005 | 1 |
| Phase 3: Service | sign_plan, verify_signature, factory | T006-T008 | 3 |
| Phase 4: DI Integration | shared/dependencies + shared/app | T009-T010 | 2 |
| Phase 5: Fixtures | conftest.py with test fixtures | T011 | 1 |
| Phase 6: Unit Tests | Canonicalizer, sign, verify, factory | T012-T015 | 4 |
| Phase 7: Contract Tests | Schema compliance | T016 | 1 |
| Phase 8: Integration Tests | Roundtrip, audit, replay | T017 | 1 |
| Phase 9: Observability | Logging, PII safety | T018-T019 | 2 |
| Phase 10: Exports | __init__.py re-exports | T020 | 1 |
| **Total** | | **T001-T020** | **20** |

---

## Dependencies (from LLD Section 9)

### External Python Packages (all already in pyproject.toml)

| Package | Version | Purpose |
|---------|---------|---------|
| `cryptography` | `>=41.0` | Ed25519 signing and verification |
| `ulid-py` | `>=1.1.0` | ULID nonce generation |
| `pydantic` | `>=2.0` | PlanSignature model validation |
| `jsonschema` | `>=4.26.0` (dev) | Contract test schema validation |

### Internal Component Dependencies

| Component | Direction | Usage |
|-----------|-----------|-------|
| None | N/A | Signer is a zero-dependency security primitive |

### Shared Infrastructure

| Shared module | Usage |
|---------------|-------|
| `shared/dependencies.py` | `get_signer_service()` added (T009) |
| `shared/app.py` | `create_signer_service()` called in lifespan (T010) |
| `shared/schemas/signature.schema.json` | Contract test validation (T016) |

---

## Architectural Considerations

### Blast Radius (from LLD Section 3)

- **If Signer fails**: No plans can be signed or verified. The entire
  plan pipeline halts (Planner cannot produce signed plans,
  PreviewOrchestrator and ExecuteOrchestrator cannot verify).
- **Containment**: Signer is a pure in-process library with zero
  infrastructure dependencies. Failure is limited to the calling process.
  No database connections, no network calls, no cascading failures.
- **Recovery**: Restart the application process. No state to recover.

### Determinism (from LLD Section 13.1)

- **Canonicalization**: `canonicalize_plan()` is fully deterministic.
  Same dict produces same JSON string produces same SHA-256 hash.
- **Signing**: Ed25519 signing is internally randomized (RFC 8032), so
  the raw signature bytes differ on each call. However, `plan_hash` is
  deterministic. This is by design for replay protection (FR-005).
- **Verification**: Fully deterministic. Same plan + same signature
  produces the same True/InvalidSignatureError result every time.

### Idempotency (from LLD Section 7.5)

- **sign_plan()**: NOT idempotent by design (unique nonce and ts per call).
- **verify_signature()**: Idempotent (pure computation, no side effects).

### Future: Shared Canonicalization (from LLD Section 13.4)

PlanLibrary and Signer both implement identical `canonicalize_plan()` logic.
A follow-up task (not in this task list) should promote both implementations
to `shared/utils/canonicalize.py` to prevent divergence.

---

## Acceptance Criteria Traceability

| SPEC AC / FR | Task(s) | Verification |
|-------------|---------|--------------|
| FR-001: Sign with Ed25519, conform to schema | T003, T006, T016 | Contract test validates schema |
| FR-002: Deterministic canonicalization | T005, T012 | Unit tests assert determinism |
| FR-003: SHA-256 plan_hash | T005, T006, T012 | Unit tests assert 64-char hex |
| FR-004: Verify signatures | T007, T014 | Unit tests for valid/invalid cases |
| FR-005: ULID nonce + ISO ts | T006, T013, T017 | Unit + integration tests |
| FR-006: Structured errors | T004, T014, T015 | Error class tests |
| FR-007: Keys from env vars | T008, T015 | Factory tests with monkeypatch |
| SC-001: Sign p95 < 5ms | T017 | Integration test (informational) |
| SC-002: Verify p95 < 5ms | T017 | Integration test (informational) |
| SC-003: 100% schema conformance | T016 | Contract tests |
| SC-004: Deterministic plan_hash | T012, T013 | Unit tests |
| SC-005: Tamper detection | T014, T017 | Unit + integration tests |
| User Story 1: Sign a plan | T006, T013 | Unit tests |
| User Story 2: Verify a signature | T007, T014 | Unit tests |
| User Story 3: Replay protection | T006, T013, T017 | Nonce uniqueness tests |
| User Story 4: Audit verification | T017 | Integration tests |
