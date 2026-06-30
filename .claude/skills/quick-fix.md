---
description: Quick bug fix for small, isolated issues without full planning cycle.
---

## When to Use This Skill

**✓ Use for**:
- Typos in code, docs, or comments
- Small logic errors (single function/method)
- Test failures (single test fix)
- Lint/formatting issues (ruff, mypy errors)
- Missing imports
- Small schema mismatches
- Documentation updates

**✗ Do NOT use for**:
- New features or capabilities
- Breaking changes to APIs
- Multi-component changes
- Architectural decisions
- Database schema migrations
- Changes affecting > 3 files

## Quick Fix Process

### 1. Identify the Issue
**Read minimal context**:
- Error message/test failure
- The specific file mentioned
- No need to read entire codebase

### 2. Verify the Fix Location
**Check**:
- Is this a single file?
- Is the fix < 10 lines?
- No dependencies on other components?

If any answer is "No", stop and use the full workflow (planner → implementer → verifier).

### 3. Apply the Fix
**Use Edit tool** (not Write):
- Single Edit operation
- Preserve surrounding code
- Match existing code style

### 4. Verify the Fix
**Run relevant tests**:
```bash
# For Python
pytest path/to/test_file.py -v

# For linting
ruff check path/to/file.py
```

**Check git diff**:
```bash
git diff path/to/file.py
```

### 5. Report Results
**Format**:
```markdown
## Quick Fix Applied

**Issue**: [Brief description]
**File**: path/to/file.py:line
**Fix**: [What changed in 1 sentence]

**Verification**:
- [✓] Tests pass
- [✓] Lint passes
- [✓] Git diff reviewed
```

## Examples

### Example 1: Typo Fix
```
Issue: Typo in function name "schdule_meeting" should be "schedule_meeting"
File: components/Intake/service/service.py:45
Fix: Renamed function to correct spelling

Verification:
- ✓ Tests pass (pytest components/Intake/tests/)
- ✓ Lint passes (ruff check)
- ✓ Git diff shows only the function name change
```

### Example 2: Missing Import
```
Issue: ImportError: cannot import name 'Intent' from 'schemas'
File: components/Planner/service/planner.py:3
Fix: Added missing import: from schemas.input import Intent

Verification:
- ✓ Tests pass
- ✓ No other errors
```

### Example 3: Test Assertion Fix
```
Issue: Test expects 30 but got 1800 (seconds vs minutes conversion)
File: components/ContextRAG/tests/test_service.py:67
Fix: Changed assertion from 30 to 1800 (correct expectation)

Verification:
- ✓ Test now passes
- ✓ Logic is correct (1800 seconds = 30 minutes)
```

## Red Flags (Use Full Workflow Instead)

**🚨 Stop and use planner if**:
- Fix requires changing > 3 files
- Fix changes component API/contract
- Fix requires new dependencies
- Fix affects database schema
- Fix requires architectural decision
- You're not 100% confident in the fix
- Tests fail after the fix
- The bug reveals a deeper design issue

## Git Workflow for Quick Fixes

```bash
# 1. Check current branch (should be on feature branch, not main)
git status

# 2. Apply fix (use Edit tool)

# 3. Verify
pytest path/to/tests/
ruff check path/to/file.py

# 4. Review diff
git diff

# 5. Stage and commit (if on feature branch)
git add path/to/file.py
git commit -m "fix: [brief description of fix]"

# 6. Push if needed
git push origin <branch-name>
```

**Important**: Quick fixes should still be part of a feature branch, not committed directly to main.

## When in Doubt

**Ask yourself**:
1. Can I explain this fix in < 10 words?
2. Does this fix touch only 1-2 files?
3. Am I 100% confident this won't break anything?

If any answer is "No" → Use full workflow (planner → implementer → verifier → pr-manager)
