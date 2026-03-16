# Signer — Low-Level Design

**Component**: `components/Signer/`
**Layer**: Domain Services (Layer 2)
**Spec**: `specs/007-signer/spec.md`
**Created**: 2026-03-14
**Status**: Draft

---

## 1. Purpose & Scope

The Signer provides Ed25519 plan signing and verification as a library service. Its primary purposes are:

1. **Plan integrity** — cryptographic proof that the executed plan is byte-for-byte what was approved
2. **Enterprise audit** — customers can verify stored plans against their signatures to prove no post-approval tampering
3. **Replay protection** — ULID nonces prevent signature reuse across plans

### Boundaries

- **In scope**: Sign plans, verify signatures, canonicalize JSON, hash plans (SHA-256)
- **Out of scope**: Key rotation (NG-5), API endpoints (NG-6), encryption (NG-3), HSM/KMS (NG-1), threshold signatures (NG-2)

### Layer Placement

Signer is a **Domain Services** component (Layer 2). It is called by:
- **Planner** (to sign plans after generation)
- **PreviewOrchestrator** (to verify before preview)
- **ExecuteOrchestrator** (to verify before execution)
- **Audit queries** (to verify stored plan integrity)

It does NOT expose user-facing Preview/Execute wrappers. Like ProfileStore and History, it operates directly.

---

## 2. Conformance

| Document | Version | Reference |
|----------|---------|-----------|
| GLOBAL_SPEC.md | v2.2 (2026-03-05) | §2.4 Plan Signature, §8 Safety & Governance |
| Project_HLD.md | Current | Layer 2 Domain Services, §12 Architectural Decisions |
| MODULAR_ARCHITECTURE.md | Current | Component Dependency Matrix — Signer (cryptography primitive) |
| SHARED_INFRASTRUCTURE.md | Current | N/A — Signer owns no database tables |

---

## 3. Architecture Overview

### Component Structure

```
components/Signer/
├── __init__.py
├── domain/
│   ├── __init__.py
│   └── models.py              # PlanSignature, error classes
├── service/
│   ├── __init__.py
│   └── signer_service.py      # SignerService (sign, verify)
├── adapters/
│   ├── __init__.py
│   └── canonicalizer.py       # JSON canonicalization + SHA-256
├── tests/
│   ├── __init__.py
│   ├── conftest.py            # Test key fixtures, sample plans
│   ├── test_contract.py       # Schema compliance tests
│   ├── test_unit_signer.py    # Sign/verify unit tests
│   └── test_integration.py    # Sign-then-verify + audit tests
└── LLD.md
```

### Blast Radius Analysis

- **Failure mode**: If Signer is unavailable, no plans can be signed or verified → the plan pipeline halts
- **Containment**: Signer is a pure library (no database, no network calls, no external dependencies at runtime). Failure is limited to the calling process.
- **No cascading failures**: Signer has zero infrastructure dependencies. It cannot cause database connection exhaustion, API rate limiting, or network timeouts.
- **Recovery**: Restart the application process. No state to recover.

### Isolation Strategy

Signer is **stateless** — it holds a key pair in memory and performs pure cryptographic operations. It has no:
- Database connections
- Redis connections
- External API calls
- Background tasks
- Queues or caches

---

## 4. Interfaces

### 4.1 Service Interface

```python
from typing import Any
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class SignerService:
    """Ed25519 plan signing and verification service."""

    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        public_key: Ed25519PublicKey,
        pubkey_id: str = "k1",
    ) -> None:
        """Initialize with a single key pair loaded from env vars."""

    async def sign_plan(
        self,
        plan_data: dict[str, Any],
        signer_identity: str = "planner@system",
    ) -> PlanSignature:
        """
        Sign a plan and return a PlanSignature.

        Args:
            plan_data: Plan dictionary to sign.
            signer_identity: Identity of the signer (default: "planner@system").

        Returns:
            PlanSignature conforming to shared/schemas/signature.schema.json.

        Raises:
            SigningKeyNotConfiguredError: If private key is not set.
            ValueError: If plan_data is empty or None.
        """

    async def verify_signature(
        self,
        plan_data: dict[str, Any],
        signature_data: dict[str, Any],
    ) -> bool:
        """
        Verify a plan signature. Raises on failure, returns True on success.

        Args:
            plan_data: Plan dictionary to verify.
            signature_data: Signature dict (PlanSignature fields).

        Returns:
            True if signature is valid.

        Raises:
            InvalidSignatureError: If signature doesn't match plan.
            UnsupportedAlgorithmError: If algo is not "Ed25519".
        """
```

### 4.2 Consumer Contracts

#### Planner → Signer (sign)

```python
# Planner calls after plan generation:
signature: PlanSignature = await signer.sign_plan(
    plan_data=plan_dict,
    signer_identity="planner@system",
)
# Planner attaches signature to plan envelope
```

**Input**: Any valid plan dict (non-empty).
**Output**: `PlanSignature` model (see §5).
**Errors to handle**: `ValueError` (empty plan), `SigningKeyNotConfiguredError`.

#### PreviewOrchestrator / ExecuteOrchestrator → Signer (verify)

```python
# Orchestrator calls before preview/execution:
is_valid: bool = await signer.verify_signature(
    plan_data=plan_dict,
    signature_data=signature_dict,
)
# If InvalidSignatureError is raised → reject the plan
```

**Input**: Plan dict + signature dict (from signed plan envelope).
**Output**: `True` on success.
**Errors to handle**: `InvalidSignatureError`, `UnsupportedAlgorithmError`.

#### Audit Queries → Signer (verify stored plans)

```python
# Audit retrieves plan + signature from History/PlanLibrary:
plan = await plan_library.get_plan(plan_id)
try:
    await signer.verify_signature(plan.canonical_json, plan.signature_data)
    # Plan integrity confirmed
except InvalidSignatureError as e:
    # Plan was tampered with after signing
    log.error("Plan integrity violation", plan_id=plan_id, reason=e.reason)
```

### 4.3 Factory Function

```python
def create_signer_service() -> SignerService:
    """
    Create SignerService from environment variables.

    Reads:
        PLAN_SIGNING_PRIVATE_KEY: Hex-encoded Ed25519 private key (32 bytes)
        PLAN_SIGNING_PUBLIC_KEY: Hex-encoded Ed25519 public key (32 bytes)

    Returns:
        Configured SignerService.

    Raises:
        SigningKeyNotConfiguredError: If env vars are missing or invalid.
    """
```

This function is called once during application lifespan startup in `shared/app.py` and stored on `app.state.signer_service`.

---

## 5. Data Model

### 5.1 Domain Entities

#### PlanSignature

Fields match GLOBAL_SPEC v2.2 §2.4 exactly:

```python
from pydantic import BaseModel, Field


class PlanSignature(BaseModel):
    """
    Cryptographic signature for a plan.

    Field names match GLOBAL_SPEC v2.2 §2.4 and
    shared/schemas/signature.schema.json.
    """

    algo: str = Field(
        default="Ed25519",
        description="Signature algorithm",
    )
    signer: str = Field(
        description="Identity of the signer (e.g., 'planner@system')",
    )
    ts: str = Field(
        description="ISO 8601 timestamp of signing",
    )
    nonce: str = Field(
        description="ULID nonce for replay protection",
    )
    signature: str = Field(
        description="Base64-encoded Ed25519 signature",
        min_length=64,
    )
    pubkey_id: str = Field(
        description="Public key identifier (e.g., 'k1')",
    )
    plan_hash: str = Field(
        description="SHA-256 hex digest of canonical plan bytes",
        min_length=64,
        max_length=64,
    )
```

### 5.2 Error Classes

```python
class SignerError(Exception):
    """Base error for Signer component."""


class SigningKeyNotConfiguredError(SignerError):
    """Raised when private/public key env vars are missing or invalid."""

    def __init__(self, reason: str = "Key not configured") -> None:
        self.reason = reason
        super().__init__(f"Signing key not configured: {reason}")


class InvalidSignatureError(SignerError):
    """Raised when signature verification fails."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"Invalid signature: {reason}")


class UnsupportedAlgorithmError(SignerError):
    """Raised when signature uses unsupported algorithm."""

    def __init__(self, algo: str) -> None:
        self.algo = algo
        super().__init__(f"Unsupported algorithm: {algo}")
```

### 5.3 Schema Reference

Output must conform to: `shared/schemas/signature.schema.json`

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "PlanSignature",
  "required": ["algo", "signer", "ts", "nonce", "signature", "pubkey_id"],
  "properties": {
    "algo": { "type": "string", "const": "Ed25519" },
    "signer": { "type": "string" },
    "ts": { "type": "string", "format": "date-time" },
    "nonce": { "type": "string", "minLength": 26, "maxLength": 26 },
    "signature": { "type": "string", "minLength": 64 },
    "pubkey_id": { "type": "string" },
    "plan_hash": { "type": "string", "minLength": 64, "maxLength": 64 }
  }
}
```

### 5.4 Note on user_id

Signer does **not** own any database tables and does not store user-specific data. The `user_id` requirement applies to Memory Layer components; Signer is a stateless Domain Services component.

---

## 6. Database Schema & Migrations

**Not applicable.** Signer owns no database tables. It is a pure library with no persistence. Plan signatures are stored by PlanLibrary (in `plans.signature_data` JSONB column).

---

## 7. Adapters

### 7.1 Canonicalizer

```python
# adapters/canonicalizer.py

import hashlib
import json
from typing import Any


def canonicalize_plan(plan_data: dict[str, Any]) -> str:
    """
    Canonicalize plan JSON for deterministic hashing.

    Sorted keys, no whitespace, consistent formatting.

    Args:
        plan_data: Plan dictionary to canonicalize.

    Returns:
        Canonical JSON string.
    """
    return json.dumps(plan_data, sort_keys=True, separators=(",", ":"))


def compute_plan_hash(plan_data: dict[str, Any]) -> str:
    """
    Compute SHA-256 hash of canonical plan bytes.

    Args:
        plan_data: Plan dictionary to hash.

    Returns:
        SHA-256 hex digest (64 characters).
    """
    canonical = canonicalize_plan(plan_data)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

**Note on PlanLibrary's existing `canonicalize_plan()`**: PlanLibrary has an identical implementation in `components/PlanLibrary/domain/models.py`. Per the spec's Open Question #1, this should be promoted to `shared/` to ensure Signer and PlanLibrary always agree on canonical form. For the MVP, Signer implements its own copy (identical logic). A follow-up task should extract both to a shared utility.

### 7.2 Key Loading

Keys are loaded from environment variables during application startup:

```python
# In create_signer_service():
import os
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

private_hex = os.environ.get("PLAN_SIGNING_PRIVATE_KEY")
public_hex = os.environ.get("PLAN_SIGNING_PUBLIC_KEY")

if not private_hex or not public_hex:
    raise SigningKeyNotConfiguredError("PLAN_SIGNING_PRIVATE_KEY or PLAN_SIGNING_PUBLIC_KEY not set")

private_key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(private_hex))
public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_hex))
```

### 7.3 Shared Infrastructure Usage

| Shared utility | Usage in Signer |
|---------------|-----------------|
| `shared/database/adapter.py` | Not used (no DB) |
| `shared/database/error_handler.py` | Not used (no DB) |
| `shared/api/error_handlers.py` | Not used (no API routes) |
| `shared/dependencies.py` | `get_signer_service()` added for DI |
| `shared/app.py` | `create_signer_service()` called in lifespan |
| `shared/schemas/signature.schema.json` | Output validation in contract tests |

### 7.4 Dependency Injection Integration

```python
# shared/dependencies.py — add:
def get_signer_service(request: Request) -> SignerService:
    return request.app.state.signer_service

# shared/app.py — add to lifespan:
from components.Signer.service.signer_service import create_signer_service
app.state.signer_service = create_signer_service()
```

### 7.5 Idempotency

Signing is **not idempotent by design** — each call produces a unique signature (different `nonce`, `ts`). However, the `plan_hash` is deterministic: same plan bytes → same hash. This is by design per FR-005 (replay protection).

Verification is **idempotent** — same plan + same signature → same result every time.

---

## 8. Sequences

### 8.1 Sign Plan (Happy Path)

```
Planner                    SignerService              Canonicalizer
   │                            │                          │
   │  sign_plan(plan_data)      │                          │
   │───────────────────────────>│                          │
   │                            │  canonicalize_plan()     │
   │                            │─────────────────────────>│
   │                            │  canonical_json          │
   │                            │<─────────────────────────│
   │                            │                          │
   │                            │  compute_plan_hash()     │
   │                            │─────────────────────────>│
   │                            │  sha256_hex              │
   │                            │<─────────────────────────│
   │                            │                          │
   │                            │  Ed25519.sign(bytes)     │
   │                            │  generate ULID nonce     │
   │                            │  generate ISO timestamp  │
   │                            │                          │
   │  PlanSignature             │                          │
   │<───────────────────────────│                          │
```

### 8.2 Verify Signature (Happy Path)

```
Orchestrator               SignerService              Canonicalizer
   │                            │                          │
   │  verify_signature(         │                          │
   │    plan_data,              │                          │
   │    signature_data)         │                          │
   │───────────────────────────>│                          │
   │                            │  check algo == Ed25519   │
   │                            │                          │
   │                            │  canonicalize_plan()     │
   │                            │─────────────────────────>│
   │                            │  canonical_json          │
   │                            │<─────────────────────────│
   │                            │                          │
   │                            │  compute_plan_hash()     │
   │                            │─────────────────────────>│
   │                            │  sha256_hex              │
   │                            │<─────────────────────────│
   │                            │                          │
   │                            │  compare plan_hash       │
   │                            │  Ed25519.verify(sig,     │
   │                            │    canonical_bytes)      │
   │                            │                          │
   │  True                      │                          │
   │<───────────────────────────│                          │
```

### 8.3 Verify Signature (Tampered Plan)

```
Orchestrator               SignerService
   │                            │
   │  verify_signature(         │
   │    tampered_plan,          │
   │    original_signature)     │
   │───────────────────────────>│
   │                            │  recompute hash → "7b2e91..."
   │                            │  signature.plan_hash → "a3f8c2..."
   │                            │  MISMATCH
   │                            │
   │  InvalidSignatureError     │
   │    reason="hash_mismatch"  │
   │<───────────────────────────│
```

### 8.4 Verify Signature (Unsupported Algorithm)

```
Orchestrator               SignerService
   │                            │
   │  verify_signature(         │
   │    plan_data,              │
   │    {"algo": "RSA", ...})   │
   │───────────────────────────>│
   │                            │  algo != "Ed25519"
   │                            │
   │  UnsupportedAlgorithmError │
   │    algo="RSA"              │
   │<───────────────────────────│
```

### 8.5 Sign Plan (Key Not Configured)

```
Planner                    create_signer_service()
   │                            │
   │  (app startup)             │
   │───────────────────────────>│
   │                            │  PLAN_SIGNING_PRIVATE_KEY not set
   │                            │
   │  SigningKeyNotConfiguredError
   │<───────────────────────────│
   │  (application fails to start)
```

### 8.6 Audit Verification Flow

```
AuditQuery                 PlanLibrary            SignerService
   │                            │                       │
   │  get_plan(plan_id)         │                       │
   │───────────────────────────>│                       │
   │  {canonical_json,          │                       │
   │   signature_data}          │                       │
   │<───────────────────────────│                       │
   │                            │                       │
   │  verify_signature(canonical_json, signature_data)  │
   │────────────────────────────────────────────────────>│
   │                                                    │
   │  True (integrity confirmed)                        │
   │<────────────────────────────────────────────────────│
```

### 8.7 Graceful Degradation

Signer is a **required** component — there is no graceful degradation. If signing fails (key not configured), the application should fail to start. If verification fails (invalid signature), the plan must be rejected. This is the safety model's design intent.

### 8.8 Retry/Idempotency Path

Signer operations are synchronous, in-process, and deterministic (except for nonce/ts generation). There are no network calls to retry. If a caller's process crashes between signing and storing the signature, the caller simply re-signs (a new signature is produced — this is safe because no state was persisted).

---

## 9. Dependencies & External Integrations

### 9.1 Python Packages

| Package | Version | Justification |
|---------|---------|---------------|
| `cryptography` | `>=41.0` | Ed25519 signing/verification (already in pyproject.toml) |
| `ulid-py` | `>=1.1.0` | ULID nonce generation (already in pyproject.toml) |
| `pydantic` | `>=2.0` | PlanSignature model validation (already in pyproject.toml) |

No new dependencies required.

### 9.2 Internal Component Dependencies

| Component | Dependency Type | Direction |
|-----------|----------------|-----------|
| Planner | Consumer | Planner → Signer (sign) |
| PreviewOrchestrator | Consumer | PreviewOrchestrator → Signer (verify) |
| ExecuteOrchestrator | Consumer | ExecuteOrchestrator → Signer (verify) |
| PlanLibrary | Shared logic | Both use `canonicalize_plan()` — should be shared |
| Audit | Consumer | Audit → Signer (verify stored plans) |

This matches MODULAR_ARCHITECTURE §4 Domain/Service Layer dependency graph.

### 9.3 External Services

None. Signer is fully self-contained with no external API calls.

---

## 10. Observability & Safety

### 10.1 Structured Logging

```python
import logging

logger = logging.getLogger("signer")

# Sign operation
logger.info("plan_signed", extra={
    "plan_hash": plan_hash,
    "pubkey_id": pubkey_id,
    "signer": signer_identity,
    "nonce": nonce,
})

# Verify success
logger.info("signature_verified", extra={
    "plan_hash": signature_data["plan_hash"],
    "pubkey_id": signature_data["pubkey_id"],
})

# Verify failure
logger.warning("signature_verification_failed", extra={
    "reason": error.reason,
    "plan_hash_expected": signature_data.get("plan_hash"),
    "plan_hash_computed": computed_hash,
})
```

**Never log**: Private key bytes, full signature bytes (base64 is OK), full plan content.

### 10.2 Prometheus Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `signer_sign_duration_seconds` | Histogram | `status` | Time to sign a plan |
| `signer_verify_duration_seconds` | Histogram | `status`, `result` | Time to verify a signature |
| `signer_sign_total` | Counter | `status` | Total sign operations |
| `signer_verify_total` | Counter | `status`, `result` | Total verify operations |
| `signer_verify_failures_total` | Counter | `reason` | Verification failures by reason |

Labels:
- `status`: `success`, `error`
- `result`: `valid`, `invalid`
- `reason`: `hash_mismatch`, `malformed_signature`, `unsupported_algorithm`

### 10.3 Error Classes Summary

| Error | HTTP Code (if exposed) | When |
|-------|----------------------|------|
| `SigningKeyNotConfiguredError` | N/A (startup failure) | Key env vars missing |
| `InvalidSignatureError` | 400 (via caller) | Verification fails |
| `UnsupportedAlgorithmError` | 400 (via caller) | algo != "Ed25519" |
| `ValueError` | 400 (via caller) | Empty/None plan_data |

Note: Signer has no API routes, so HTTP codes are determined by the calling component's route handler. The caller maps Signer errors using `ErrorResponse` from `shared/api/error_handlers.py`.

---

## 11. Caching Strategy

**Not applicable.** Signer has no Redis usage and no cacheable operations. Every sign produces a unique result (nonce/ts). Verification is pure computation (~microseconds) — caching would add overhead, not reduce it.

---

## 12. Non-Functional Requirements

### 12.1 Performance

| Operation | p95 Target | p99 Target | Notes |
|-----------|-----------|-----------|-------|
| `sign_plan()` | < 5ms | < 10ms | Ed25519 sign ~microseconds; canonicalization dominates |
| `verify_signature()` | < 5ms | < 10ms | Ed25519 verify ~microseconds; canonicalization dominates |
| `canonicalize_plan()` | < 1ms | < 2ms | `json.dumps` with sort_keys |
| `compute_plan_hash()` | < 1ms | < 2ms | SHA-256 is hardware-accelerated |

### 12.2 Availability

- Signer is in-process — its availability equals the application process availability
- No external dependencies → no availability degradation from third parties
- Target: Same as system baseline (99.9% cloud, best-effort local)

### 12.3 Scalability

- Stateless — horizontal scaling is automatic with application instances
- No shared state between instances (each loads its own key pair from env)
- Single-user and multi-user: no difference (no per-user state)

### 12.4 Testing Strategy

| Test Type | File | Coverage |
|-----------|------|----------|
| Unit — sign | `test_unit_signer.py` | Sign returns valid PlanSignature, deterministic hash, unique nonce/ts |
| Unit — verify | `test_unit_signer.py` | Verify valid sig, tampered plan, wrong algo, malformed sig |
| Contract | `test_contract.py` | Output matches `signature.schema.json`, GLOBAL_SPEC §2.4 fields |
| Integration | `test_integration.py` | Sign-then-verify roundtrip, audit verification scenario |
| Edge cases | `test_unit_signer.py` | Empty plan, None plan, missing key, base64 corruption |

---

## 13. Architectural Considerations

### 13.1 Determinism

- `canonicalize_plan()` is deterministic: same dict → same JSON string → same hash
- Ed25519 signing is **not** deterministic per RFC 8032 (randomized internally), but the `plan_hash` is deterministic
- The `nonce` (ULID) and `ts` are intentionally unique per signature (replay protection)

### 13.2 State Management

Signer is **fully stateless** at runtime. The key pair is loaded once at startup and held in memory. No persistence, no background tasks, no queues.

### 13.3 Future HSM Integration

The `SignerService` constructor takes key objects directly. To add HSM support:
1. Create an `HsmKeyAdapter` that wraps HSM operations behind the same `Ed25519PrivateKey` / `Ed25519PublicKey` interface
2. Swap the factory function to use the HSM adapter
3. No changes to `SignerService` internals

This is why the spec lists HSM as NG-1 — the interface is designed for adapter swap without service changes.

### 13.4 Canonicalization Divergence Risk

PlanLibrary and Signer both implement `canonicalize_plan()` with identical logic (`json.dumps(data, sort_keys=True, separators=(",", ":"))`). If one changes without the other, verification fails for plans signed by one and verified by the other.

**Mitigation**: Follow-up task to promote `canonicalize_plan()` and `compute_plan_hash()` to `shared/utils/canonicalize.py`. Both components import from shared.

---

## 14. Architecture Decision Records

### Referenced ADRs

| ADR | Relevance |
|-----|-----------|
| `0001-component-first.md` | Signer follows component-first structure under `components/Signer/` |

### New Decisions (no ADR required — documented in spec)

- **Single key pair from env vars** — simplest MVP; HSM upgrade path preserved via adapter pattern
- **Library, not service** — no API routes; called via dependency injection
- **Duplicate canonicalization** — acceptable for MVP; shared utility is a follow-up

---

## 15. Risks & Open Questions

### Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Key compromise (env var leaked) | High | Keys never logged, never in plan data. HSM is the upgrade path. |
| Canonicalization divergence | Medium | Promote to shared utility (follow-up task) |
| Clock skew (ts field) | Low | Single-tenant deployment; ts is for audit, not security |

### Open Questions

1. **Should `canonicalize_plan()` be promoted to `shared/`?** — Recommended yes. Deferred to follow-up task.
2. **Should signature verification be exposed as an API endpoint?** — Recommended no for MVP. Library call via DI is sufficient.

---

## 16. Post-Generation Validation Checklist

- [x] Data model fields match GLOBAL_SPEC §2.4 (`algo`, `signer`, `ts`, `nonce`, `signature`, `pubkey_id`, `plan_hash`)
- [x] `user_id` — N/A (Signer owns no database tables, no user-specific state)
- [x] Conformance header references current document versions (GLOBAL_SPEC v2.2)
- [x] Table ownership — N/A (no tables)
- [x] Component dependencies match MODULAR_ARCHITECTURE (Planner, orchestrators, Audit)
- [x] Every upstream consumer has documented interface contract (§4.2)
- [x] Storage APIs idempotent — N/A (no storage)
- [x] DDL — N/A (no tables)
- [x] Migration file — N/A (no tables)
- [x] Prometheus metrics defined with names and types (§10.2)
- [x] No deprecated library versions
- [x] Error handling uses `ErrorResponse` — N/A (no API routes; callers handle)
- [x] Database adapter — N/A (no database)
