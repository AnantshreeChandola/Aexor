---
description: Provide a clear, example-driven explanation of any component in the Personal Agent system.
---

Read first:
- Component's `SPEC.md` and `LLD.md` (if they exist)
- `/docs/architecture/Project_HLD.md` for system context
- `/docs/architecture/GLOBAL_SPEC.md` for contracts

## Explanation Format

```markdown
## [Component Name]

### What It Does
1-2 sentence summary in plain English. Focus on the user-visible outcome.

### Where It Fits
**Layer**: [Intake/Core/Runtime/Platform]
**Connects To**:
- **Input from**: Component A (what data)
- **Output to**: Component B (what data)

### Real-World Example

**Scenario**: [Concrete user story, e.g., "Book meeting with Alice next week"]

**Step-by-Step**:

1. **Input Received**:
   ```json
   {
     "intent": "schedule_meeting",
     "entities": {"attendee": "Alice"},
     "constraints": {"timeframe": "next week"}
   }
   ```

2. **Component Processing**:
   - Action 1: Validates input
   - Action 2: Calls external API (preview mode: mock, execute mode: real)
   - Action 3: Formats response

3. **Output Returned**:
   ```json
   {
     "normalized": {
       "available_slots": ["Tue 10 AM", "Thu 2 PM"]
     },
     "source": "preview",
     "can_execute": true
   }
   ```

### Key Concepts

**Concept 1: Preview vs Execute**
- **Preview**: READ-ONLY, shows what will happen, no side effects
- **Execute**: WRITES data, requires approval token
- **Example**: Preview shows 3 time slots, Execute creates calendar event

**Concept 2: Idempotency**
- Safe to retry same operation
- Uses key: `plan_id:step:arg_hash`
- **Example**: Retry create_event doesn't create duplicate meetings

### Common Questions

**Q: When does this component run?**
A: [Explain with example from the 5-phase flow: Intent → Plan → Preview → Execute → Learn]

**Q: What happens if it fails?**
A: [Explain error handling, retries, compensation]

**Q: How does it connect to other components?**
A: [Show with concrete example]

### Code Example (if helpful)
```python
# Minimal working example
async def preview(intent: Intent) -> PreviewWrapper:
    # Fetch data (mock in preview)
    slots = await calendar_adapter.get_free_slots(mock=True)

    # Normalize
    normalized = {"available_slots": slots}

    # Wrap
    return PreviewWrapper(
        normalized=normalized,
        source="preview",
        can_execute=True
    )
```

### Related Components
- **ContextRAG**: Provides evidence for this component
- **WorkflowBuilder**: Converts this component's step into n8n workflow
```

## Style Guidelines

- Use concrete examples (meeting booking, visa watcher, shopping)
- Avoid abstract descriptions ("processes data" → "finds overlapping calendar slots")
- Show actual JSON/code snippets
- Use analogies when helpful
- Keep it concise (prefer examples over long explanations)

## Example References

The HLD has these examples to draw from:
- **Meeting booking**: Complete end-to-end in Section 2
- **Multi-gate shopping**: gate-A (product), gate-B (cart), gate-C (payment) in Section 6
- **Visa watcher**: Temporal workflow, 14-day monitoring in Section 8
