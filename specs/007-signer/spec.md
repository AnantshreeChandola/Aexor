# Feature Specification: Signer

**Feature Branch**: `feat/signer`
**Created**: 2026-03-14
**Status**: Draft
**Input**: User description: "Signer — Ed25519 plan signing and verification component"

---

## Overview

The Signer component cryptographically signs deterministic plans using Ed25519 and verifies signatures before execution. Its primary purpose is **plan integrity and auditability**: ensuring that the plan stored in History and executed by the orchestrator is byte-for-byte what was originally approved. It provides cryptographic proof for enterprise audit scenarios ("what exactly did the agent execute on this date?") and replay protection via ULID nonces, conforming to the Plan Signature contract in GLOBAL_SPEC §2.4.

---

## Goals

- **G-1**: Provide Ed25519 signing for canonicalized plans, producing signatures conforming to GLOBAL_SPEC §2.4
- **G-2**: Provide Ed25519 verification for signed plans used by PreviewOrchestrator and ExecuteOrchestrator
- **G-3**: Ensure determinism — same canonical plan bytes always produce the same hash
- **G-4**: Protect against replay attacks via ULID nonce in every signature
- **G-5**: Enable enterprise audit — cryptographic proof that stored plans match what was approved and executed

## Non-Goals

- **NG-1**: HSM or external KMS integration (keys are env-based for self-hosted MVP; HSM is a future adapter swap)
- **NG-2**: Multi-party or threshold signatures
- **NG-3**: Encryption of plan data (Signer only signs, does not encrypt)
- **NG-4**: Certificate chain or X.509 infrastructure
- **NG-5**: Key rotation or multi-key management (single key pair for MVP)
- **NG-6**: API routes for key listing or health (Signer is a library, not a service with endpoints)

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Sign a Plan (Priority: P1)

The Planner generates a deterministic plan and calls the Signer to produce an Ed25519 signature. The signed plan can then be passed to PreviewOrchestrator for safe preview execution.

**Why this priority**: Without signing, no plan can enter the preview/execute pipeline. This is the foundational operation.

**Independent Test**: Can be fully tested by signing a plan dict and verifying the output matches GLOBAL_SPEC §2.4 schema.

**Acceptance Scenarios**:

1. **Given** a valid plan dict, **When** `sign_plan()` is called, **Then** a `PlanSignature` is returned containing `algo="Ed25519"`, `signer`, `ts` (ISO 8601), `nonce` (ULID), `signature` (base64), `pubkey_id`, and `plan_hash` (SHA-256 hex).
2. **Given** the same plan dict signed twice with the same key, **When** comparing the `plan_hash` fields, **Then** both hashes are identical (deterministic canonicalization).
3. **Given** a plan dict signed with the configured key, **When** the signature is verified with the corresponding public key, **Then** verification succeeds.

---

### User Story 2 — Verify a Plan Signature (Priority: P1)

PreviewOrchestrator and ExecuteOrchestrator call Signer to verify plan signatures before proceeding. Invalid or tampered plans are rejected.

**Why this priority**: Without verification, plans could be tampered with between signing and execution, and audit proofs would be meaningless.

**Independent Test**: Can be fully tested by signing a plan, tampering with one field, and asserting verification fails.

**Acceptance Scenarios**:

1. **Given** a validly signed plan, **When** `verify_signature()` is called with the plan and its signature, **Then** it returns `True`.
2. **Given** a signed plan where one step's `args` field has been modified, **When** `verify_signature()` is called, **Then** it raises `InvalidSignatureError`.
3. **Given** a signature with `algo` set to `"RSA"`, **When** `verify_signature()` is called, **Then** it raises `UnsupportedAlgorithmError`.

---

### User Story 3 — Replay Protection (Priority: P2)

Each signature includes a ULID nonce. Downstream consumers (ApprovalGate, ExecuteOrchestrator) can detect and reject replayed signatures.

**Why this priority**: Replay protection is a security hardening concern; the nonce field is part of the schema regardless.

**Acceptance Scenarios**:

1. **Given** the same plan is signed twice, **When** comparing the two signatures, **Then** each has a unique `nonce` (ULID) and different `ts`.
2. **Given** a signature, **When** parsing the `nonce`, **Then** it is a valid ULID (26-character Crockford Base32).

---

### User Story 4 — Audit Verification (Priority: P1)

An enterprise customer retrieves a stored plan and its signature from History and verifies that the plan was not modified after approval.

**Why this priority**: This is the primary business justification for the Signer — cryptographic audit proof.

**Acceptance Scenarios**:

1. **Given** a plan and signature retrieved from History, **When** `verify_signature()` is called, **Then** it returns `True` (proving the stored plan is exactly what was signed).
2. **Given** a plan that was modified in the database after signing, **When** `verify_signature()` is called, **Then** it raises `InvalidSignatureError` (proving tampering occurred).

---

### Edge Cases

- What happens when the private key is not configured? → `SigningKeyNotConfiguredError` raised.
- What happens when plan_data is empty or None? → `ValueError` raised before signing.
- What happens when the plan_hash in the signature doesn't match recomputed hash? → `verify_signature()` raises `InvalidSignatureError` with reason `"hash_mismatch"`.
- What happens when the signature base64 is malformed? → `InvalidSignatureError` with reason `"malformed_signature"`.

---

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST sign plan dicts using Ed25519 and return a `PlanSignature` conforming to `shared/schemas/signature.schema.json`.
- **FR-002**: System MUST canonicalize plan JSON deterministically (sorted keys, no whitespace) before hashing.
- **FR-003**: System MUST hash canonical plan bytes with SHA-256 and include the hash in the signature as `plan_hash`.
- **FR-004**: System MUST verify Ed25519 signatures given a plan dict and signature dict.
- **FR-005**: System MUST include a ULID `nonce` and ISO 8601 `ts` in every signature for replay protection.
- **FR-006**: System MUST raise structured errors for invalid signatures, unsupported algorithms, and misconfigured keys.
- **FR-007**: System MUST load signing key pair from environment variables (`PLAN_SIGNING_PRIVATE_KEY`, `PLAN_SIGNING_PUBLIC_KEY`).

### Key Entities

- **PlanSignature**: The output of a signing operation — contains `algo`, `signer`, `ts`, `nonce`, `signature`, `pubkey_id`, `plan_hash`.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Signing a plan completes in < 5ms p95 (Ed25519 is fast; canonicalization dominates).
- **SC-002**: Verification completes in < 5ms p95.
- **SC-003**: 100% of signatures produced conform to `shared/schemas/signature.schema.json` (validated in contract tests).
- **SC-004**: Determinism: signing the same canonical plan bytes with the same key always produces the same `plan_hash`.
- **SC-005**: Tamper detection: any single-byte change in the plan causes verification failure.

---

## Interfaces & Contracts (conform to GLOBAL_SPEC v2)

### Note on Preview/Execute

The Signer is an **internal domain service** — it does NOT expose user-facing Preview/Execute wrappers. It is called by the Planner (to sign) and by PreviewOrchestrator/ExecuteOrchestrator (to verify). Like ProfileStore and History, it operates directly without the Preview/Execute safety model applied to itself.

### Service Interface

```python
class SignerService:
    def __init__(self, private_key: Ed25519PrivateKey, public_key: Ed25519PublicKey):
        """Initialize with a single key pair loaded from env vars."""

    async def sign_plan(
        self,
        plan_data: dict[str, Any],
        signer_identity: str = "planner@system",
    ) -> PlanSignature:
        """Sign a plan and return a PlanSignature."""

    async def verify_signature(
        self,
        plan_data: dict[str, Any],
        signature_data: dict[str, Any],
    ) -> bool:
        """Verify a plan signature. Raises on failure."""
```

### PlanSignature Output (GLOBAL_SPEC §2.4)

```json
{
  "algo": "Ed25519",
  "signer": "planner@system",
  "ts": "2026-03-14T10:00:00Z",
  "nonce": "01HXYZ...",
  "signature": "base64...",
  "pubkey_id": "k1",
  "plan_hash": "sha256hex..."
}
```

**Reference**: `shared/schemas/signature.schema.json`, `docs/architecture/GLOBAL_SPEC.md` (v2, §2.4)

---

## Component Mapping

- **Target**: `components/Signer/`
- Files expected to change:
  - `components/Signer/__init__.py`
  - `components/Signer/domain/__init__.py`
  - `components/Signer/domain/models.py` — `PlanSignature`, error classes
  - `components/Signer/service/__init__.py`
  - `components/Signer/service/signer_service.py` — `SignerService` (sign, verify)
  - `components/Signer/adapters/__init__.py`
  - `components/Signer/adapters/canonicalizer.py` — Deterministic JSON canonicalization + SHA-256 hashing
  - `components/Signer/tests/__init__.py`
  - `components/Signer/tests/conftest.py` — Shared fixtures (test keys, sample plans)
  - `components/Signer/tests/test_contract.py` — Schema compliance, GLOBAL_SPEC §2.4 validation
  - `components/Signer/tests/test_unit_signer.py` — Sign/verify unit tests
  - `components/Signer/tests/test_integration.py` — End-to-end sign-then-verify + audit verification tests

---

## Dependencies & Risks

### Dependencies
- `cryptography` library (already in `pyproject.toml` — `cryptography>=41.0`) for Ed25519 operations
- `ulid-py` (already in `pyproject.toml` — `ulid-py>=1.1.0`) for nonce generation
- `shared/schemas/signature.schema.json` — existing schema for output validation
- PlanLibrary's `canonicalize_plan()` — reuse or promote to shared utility

### Risks
- **Key compromise**: If the private signing key is leaked, all plans can be forged. Mitigation: keys loaded from env, never logged, never in plan data. Future mitigation: HSM integration via adapter swap (no interface change needed).
- **Canonicalization divergence**: If Signer and PlanLibrary use different canonicalization logic, verification fails. Mitigation: promote `canonicalize_plan()` to a shared utility.
- **Clock skew**: Signature `ts` depends on system clock. Minimal risk for single-tenant deployment.

---

## Non-Functional Requirements

- Inherit baseline from GLOBAL_SPEC v2 §3:
  - Structured logging with `plan_id` correlation
  - No secrets/PII in logs (private keys never logged)
- **Signing latency**: p95 < 5ms (Ed25519 signing is ~microseconds; canonicalization is the bottleneck)
- **Verification latency**: p95 < 5ms
- **Key storage**: Private key loaded from environment variable; never persisted in database or logs
- **Availability**: Same as system baseline (99.9%)

---

## Open Questions

1. **Should `canonicalize_plan()` be promoted from PlanLibrary to `shared/`?** — Recommended yes, to ensure Signer and PlanLibrary always agree on canonical form.
2. **Should signature verification be exposed as an API endpoint or remain a library call?** — Recommend library call for MVP; orchestrators call the service directly via dependency injection.

---

## Conformance

This work conforms to `docs/architecture/GLOBAL_SPEC.md` v2 (§2.4 Plan Signature, §8 Safety & Governance).
