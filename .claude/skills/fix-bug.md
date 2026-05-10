---
description: Disciplined bug fixing — scoped changes, LLD/spec compliance, and change summary.
---

## When to Use This Skill

Use this skill whenever fixing a bug, error, or unexpected behavior in any component. This applies to all bug fixes regardless of size — from single-line typos to multi-file issues.

## Rules

### 1. Always Summarize All Changes

After completing a fix, provide a clear summary in this format:

```markdown
## Bug Fix Summary

**Issue**: [What was broken and how it manifested]
**Root Cause**: [Why it was broken]

### Changes Made
| # | File | Change |
|---|------|--------|
| 1 | `path/to/file.py:line` | [What changed and why] |
| 2 | `path/to/other.py:line` | [What changed and why] |

### Verification
- [ ] Relevant tests pass
- [ ] No regressions in related functionality
```

### 2. Do Not Touch Unrelated Components

- Only modify files within the component where the bug exists.
- If the bug originates in component A but surfaces in component B, fix it in component A — do not patch around it in B.
- Do NOT refactor, clean up, add comments, or "improve" code in files unrelated to the bug.
- If you discover a separate issue in another component while investigating, report it but do not fix it in the same change.

**Before editing any file, ask**: "Is this file directly involved in the bug?" If no, do not touch it.

### 3. Follow Component LLD and Spec

Before making changes, read the component's LLD (Low-Level Design) document:
- Located at `components/<ComponentName>/LLD.md`
- Also check the component's spec if referenced in the LLD.

When applying a fix:
- Ensure the fix does not violate any contract, interface, or invariant defined in the LLD.
- Ensure the fix does not change the component's public API or data models in ways the LLD does not allow.
- If the correct fix *requires* violating the LLD or spec (e.g., the spec itself is wrong, or the fix needs an API change), **stop and ask the user for approval** before proceeding. Explain what LLD/spec constraint would be violated and why.

## Process

1. **Read the error** — understand the symptom.
2. **Locate the component** — identify which component owns the buggy code.
3. **Read the component's LLD** — `components/<ComponentName>/LLD.md`.
4. **Trace the root cause** — read only the files needed to understand the bug.
5. **Plan the fix** — verify it stays within the component boundary and complies with the LLD.
6. **Apply the fix** — minimal, scoped edits.
7. **Verify** — run relevant tests, check lint.
8. **Summarize** — provide the change summary (Rule 1).

## Red Flags — Stop and Ask

- The fix requires changing a shared schema in `shared/schemas/`.
- The fix requires modifying another component's API routes or service layer.
- The fix contradicts something stated in the component's LLD.
- The fix changes behavior that other components depend on.
- You are unsure whether the fix is correct.
