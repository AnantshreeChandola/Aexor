---
description: Write a SPEC.md for a new component following the project's component-first architecture.
---

Read first:
- `/docs/architecture/GLOBAL_SPEC.md`
- `/docs/architecture/Project_HLD.md`
- `/PROJECT_STRUCTURE.md`

Identify which layer the component belongs to: Intake, Core, Runtime, or Platform.

## SPEC.md Template

```markdown
# [Component Name] — SPEC

## Purpose
What problem does this component solve? (1-2 sentences)

## Acceptance Criteria
- [ ] AC1: Testable criterion (maps to future test)
- [ ] AC2: Testable criterion (maps to future test)
- [ ] AC3: Testable criterion (maps to future test)

## Component Type
**Layer**: [Intake/Core/Runtime/Platform]
**Mode**: [Stateless/Stateful/Orchestrator/Gateway]

## API Contract

### Inputs
```json
{
  "field1": "type and description",
  "field2": "type and description"
}
```

### Outputs (Preview wrapper)
```json
{
  "normalized": {
    "result_field": "type and description"
  },
  "source": "preview",
  "can_execute": true,
  "evidence": []
}
```

### Outputs (Execute wrapper)
```json
{
  "provider": "component_name",
  "result": {"id": "resource_id"},
  "status": "created|updated|error"
}
```

### Errors
- `ERROR_CODE_1`: When X happens
- `ERROR_CODE_2`: When Y happens

## Dependencies
- Component A: For what purpose
- Component B: For what purpose

## Examples

### Example 1: Happy Path
**Input**:
```json
{...}
```

**Preview Output**:
```json
{...}
```

**Execute Output**:
```json
{...}
```

### Example 2: Edge Case
**Input**: ...
**Output**: ...

## Non-Goals
- What this component does NOT do
- What is out of scope

## Conformance
This component conforms to `/docs/architecture/GLOBAL_SPEC.md` v2.

## Open Questions
- [ ] Question 1?
- [ ] Question 2?
```

Follow the preview-first safety model. Ensure all acceptance criteria are testable and map 1:1 to future tests.
