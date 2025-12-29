---
description: Create or update a feature specification using Spec Kit, aligned to this repo’s GLOBAL_SPEC and component-first structure.
---

/system
Act in **workbench mode**: you will use Spec Kit to draft a spec in its workspace, not in components/. Do **not** modify files outside the Spec Kit output path. Do **not** push to main. After drafting, recommend the next step to promote the spec into `components/<Name>/SPEC.md` (the source of truth).

/user
## Inputs (ask if missing)
- Feature title/description (natural language)
- Target Component Name(s), e.g., FocusBlock (if unknown, propose one)

## Steps

1) Create workbench spec (Spec Kit)
   Run from repo root:
       .specify/scripts/bash/create-new-feature.sh --json "$ARGUMENTS"
   Parse JSON for: BRANCH_NAME, SPEC_FILE, and, if present, SPECS_DIR (absolute paths only).
   Ensure current branch == BRANCH_NAME (feature branch; never main).

2) Read canonical repo rules for content
   - .specify/memory/constitution.md (PR rules, no push to main, CI gates)
   - docs/architecture/PROJECT_STRUCTURE.md (component-first rules)
   - docs/architecture/GLOBAL_SPEC.md (Intent + Preview/Execute envelopes, NFRs)
   - docs/architecture/Project_HLD.md (system context)

3) Load the template
   - Load .specify/templates/spec-template.md.
   - If template missing, fall back to the “Fallback Spec Skeleton” section below.

4) Write the spec to SPEC_FILE (overwrite allowed)
   Preserve section order from the template.
   Replace placeholders with concrete content derived from the feature description and the canonical docs.
   Must include these sections (required):
   - Overview (single paragraph)
   - Goals / Non-Goals
   - User Stories (bullets or Gherkin style)
   - Acceptance Criteria (testable, numbered) — each AC must be verifiable in CI
   - Interfaces & Contracts
     - Intent (input shape)
     - Preview wrapper (normalized payload outline + "source":"preview", "can_execute")
     - Execute wrapper (result id + status)
     - Reference: docs/architecture/GLOBAL_SPEC.md (v1)
   - Component Mapping — target component name(s) and the files that will change under components/<Name>/... (api/service/domain/adapters/schemas/tests)
   - Dependencies & Risks
   - NFRs — either inherit the baseline or list explicit deltas (perf, observability, safety)
   - Open Questions
   - Conformance — “This work conforms to docs/architecture/GLOBAL_SPEC.md v1.”

5) Quality gates on the spec
   - ACs are specific & testable (each maps to a future test).
   - Contracts reference the universal envelopes and outline the component’s normalized payload fields.
   - Component Mapping names exact paths (e.g., components/FocusBlock/schemas/response.normalized.json).

6) Report results
   Print:
   - Branch: BRANCH_NAME
   - Spec file: SPEC_FILE (absolute path in .specify/specs/)
   - Promotion: Copy SPEC to specs/<spec-id>/SPEC.md (source of truth)
   - Next step: /design (generate LLD in components/<Name>/LLD.md)

## Constraints
- Write only to SPEC_FILE in the Spec Kit workspace; no other files.
- Never push to main; BRANCH_NAME must be a feature branch.
- Treat .specify/* as a workbench; source of truth is specs/<spec-id>/SPEC.md after promotion.

/assistant
## Fallback Spec Skeleton (used only if the template is missing)
Replace ⟨placeholders⟩ and write to SPEC_FILE.

---
# Feature Spec — ⟨Title⟩

## Overview
One-paragraph summary of the feature and its user value.

## Goals
- …

## Non-Goals
- …

## User Stories
- As a ⟨role⟩, I want ⟨capability⟩ so that ⟨value⟩.

## Acceptance Criteria (testable)
1) Given … when … then … (maps to future CI test)
2) …

## Interfaces & Contracts (conform to GLOBAL_SPEC v1)

Intent (input):
    {
      "intent": "⟨action⟩",
      "entities": {},
      "constraints": {},
      "tz": "America/Chicago"
    }

Preview (wrapper + normalized outline):
    {
      "normalized": {
        "⟨key⟩": "⟨type⟩",
        "…": "…"
      },
      "source": "preview",
      "can_execute": true,
      "evidence": []
    }

Execute (wrapper):
    {
      "provider": "⟨name⟩",
      "result": { "id": "⟨id⟩" },
      "status": "created|updated|error"
    }

Reference: docs/architecture/GLOBAL_SPEC.md (v1)

## Component Mapping
- Target: components/⟨Name⟩/
- Files expected to change:
  - api/…
  - service/…
  - domain/…
  - adapters/…
  - schemas/response.normalized.json
  - tests/test_contract.py

## Dependencies & Risks
- …

## Non-Functional Requirements
- Inherit baseline (Preview p95 < 800ms; Execute p95 < 2s; structured logs; no secrets/PII), unless deltas:
- …

## Open Questions
- …

## Conformance
This work conforms to docs/architecture/GLOBAL_SPEC.md v1.
---
