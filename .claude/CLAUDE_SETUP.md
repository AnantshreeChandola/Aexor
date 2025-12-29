# Claude Code Setup Guide — Personal Agent Project

**Status**: ✓ Complete & Production-Ready

---

## What You Have

### Agents (`.claude/agents/`) — 5 total
1. **planner** - Maps SPEC/LLD acceptance criteria to tasks, proposes tests first
2. **implementer** - Implements tasks with preview-first safety enforcement
3. **verifier** - Validates plans, runs tests, checks schemas and backward compatibility
4. **pr-manager** - Creates PRs with proper templates and conformance checks
5. **architect** - Makes architectural decisions, analyzes blast radius and fault isolation

### Slash Commands (`.claude/commands/`) — 3 total
1. **/primer** - Read-only repo overview, proposes best next command
2. **/design** - Generates LLD and flow diagram from SPEC
3. **/specify** - Creates SPEC using Spec Kit workbench

### Skills (`.claude/skills/`) — 8 total
1. **create-component-spec** - Template for writing SPEC.md
2. **create-component-lld** - Template for writing LLD.md
3. **review-plan-schema** - Validate plan JSON against GLOBAL_SPEC
4. **explain-component** - Explain components with real-world examples
5. **add-test-cases** - Generate tests from acceptance criteria
6. **review-architecture** - Architectural review checklist (conformance, scalability, observability)
7. **quick-fix** - Fast bug fixes for small, isolated issues (< 3 files)
8. **update-component-status** - Update component implementation tracker

### Infrastructure (`.claude/`)
- **COMPONENT_STATUS.md** - Track implementation progress for all 16 components
- **QUICK_REFERENCE.md** - Fast lookup for 6 runtime roles, 4 layers, commands, patterns
- **CLAUDE_SETUP.md** - This file
- **settings.json** - Secure permissions, git safeguards, Python/test tooling

---

## Recommended Workflows

### For New Components (Full Cycle)
```bash
/primer                      # Understand repo structure
/specify                     # Create SPEC using Spec Kit
/design                      # Generate LLD from SPEC
/flow_orchestrate            # Auto-run: planner → implementer → verifier → pr-manager
```

**Manual Alternative** (if you want control over each step):
```bash
# Use planner agent          # Map SPEC → tasks
# Use implementer agent      # Write code with preview-first safety
# Use verifier agent         # Run tests, validate schemas
# Use pr-manager agent       # Create PR with conformance checks
```

### For Bug Fixes
```bash
# Small fix (< 3 files, < 10 lines)
/quick-fix

# Medium fix (affects multiple components or tests)
# Use verifier agent → implementer agent

# Large fix (architectural change)
# Use architect agent → planner → implementer → verifier → pr-manager
```

### For Understanding Codebase
```bash
/primer                      # High-level repo overview
/explain-component           # Explain specific component with examples
# Check QUICK_REFERENCE.md   # Fast lookup for roles, layers, patterns
```

### For Architectural Decisions
```bash
# Use architect agent        # Evaluate trade-offs, analyze blast radius
/review-architecture         # Quick architectural review of SPEC/LLD
```

---

## Skills vs Agents vs Commands

### Skills (Templates/Prompts)
**What**: Reusable prompt templates for repetitive tasks
**When**: Creating SPECs, LLDs, tests; validating plans; quick reviews
**How**: Type skill name in conversation or reference directly

**Examples**:
- `/create-component-spec` - Generate SPEC.md template
- `/add-test-cases` - Generate tests from acceptance criteria
- `/review-architecture` - Check SPEC/LLD against GLOBAL_SPEC

### Agents (Autonomous Workers)
**What**: Specialized AI workers for complex, multi-step tasks
**When**: Planning, implementation, testing, PR creation, architectural decisions
**How**: Reference in conversation or spawn with Task tool

**Examples**:
- **planner**: Maps acceptance criteria → ordered tasks → test plan
- **implementer**: Writes code, enforces preview-first safety
- **architect**: Evaluates trade-offs, analyzes blast radius

### Slash Commands (Pre-defined Workflows)
**What**: Structured workflows that combine multiple steps
**When**: Repo exploration, SPEC/LLD generation
**How**: Type `/command-name` in chat

**Examples**:
- `/primer` - Read canonical docs, propose next step
- `/specify` - Create SPEC with Spec Kit workbench
- `/design` - Generate LLD and Mermaid diagram

---

## Agent Responsibilities

| Agent | Purpose | Creates | Does NOT Create |
|-------|---------|---------|-----------------|
| **planner** | Maps SPEC → tasks | Task lists, test plans | SPECs, LLDs |
| **implementer** | Writes code | Code, tests, schemas | SPECs, LLDs |
| **verifier** | Validates | Test results, validation reports | SPECs, LLDs |
| **pr-manager** | Creates PRs | PR with conformance checks | SPECs, LLDs |
| **architect** | Makes decisions | ADRs, trade-off analysis, recommendations | SPECs, LLDs |

**For creating SPECs/LLDs**:
- Use `/specify` command (Spec Kit workflow)
- Use `/design` command (LLD generation)
- Use **skills** (create-component-spec, create-component-lld) for templates

---

## Custom Agents vs Built-in Subagents

| Your Custom Agents | Built-in Subagents | When to Use |
|-------------------|-------------------|-------------|
| **planner** | `Task(subagent_type="Plan")` | Your planner for component-specific planning with GLOBAL_SPEC |
| **implementer** | `Task(subagent_type="general-purpose")` | Your implementer for preview-first safety enforcement |
| **verifier** | `Task(subagent_type="general-purpose")` | Your verifier for contract validation |
| **architect** | `Task(subagent_type="senior-architect")` | Your architect for project-specific decisions |
| N/A | `Task(subagent_type="Explore")` | Use built-in for codebase exploration |

**Recommendation**: Use your custom agents for project-specific workflows. Use built-in subagents for general exploration.

---

## Quick Examples

### Example 1: Create Component SPEC
```
User: I need to create a SPEC for the Intake component
Assistant: I'll use the create-component-spec skill.
[Generates SPEC.md template, fills in from Project_HLD.md]
```

### Example 2: Architectural Decision
```
User: Should we merge PreviewOrchestrator and ExecuteOrchestrator?
Assistant: I'll use the architect agent to analyze this.
[Evaluates trade-offs, analyzes blast radius, recommends keeping separate]
```

### Example 3: Full Component Implementation
```
User: Implement the PluginRegistry component
Assistant: I'll follow the full workflow:
1. /primer (understand repo)
2. /specify (create SPEC)
3. /design (generate LLD)
4. planner agent (map to tasks)
5. implementer agent (write code)
6. verifier agent (run tests)
7. pr-manager agent (create PR)
```

---

## Key Files Reference

| Need | File |
|------|------|
| Fast lookup (roles, layers, patterns) | `.claude/QUICK_REFERENCE.md` |
| Component implementation status | `.claude/COMPONENT_STATUS.md` |
| This setup guide | `.claude/CLAUDE_SETUP.md` |
| System architecture | `docs/architecture/Project_HLD.md` |
| Universal contracts | `docs/architecture/GLOBAL_SPEC.md` |
| Project rules | `.claude/CLAUDE.md` |

---

## Next Steps

1. Check **COMPONENT_STATUS.md** to see what to build first
2. Use **/primer** to understand the repo
3. Pick a component from the critical path:
   - PluginRegistry (source of truth for tools)
   - Signer (plan signing with Ed25519)
   - ProfileStore (user preferences)
   - PlanLibrary (store past plans)
4. Run **/specify** to create its SPEC
5. Follow the full workflow above

---

## Tips for Efficient Usage

1. **Always start with /primer** before making changes
2. **Use QUICK_REFERENCE.md** for fast lookups
3. **Check COMPONENT_STATUS.md** to see progress
4. **Use architect agent** for decisions, not SPEC/LLD creation
5. **Use quick-fix skill** only for tiny bugs (< 10 lines, < 3 files)
6. **Update COMPONENT_STATUS.md** after completing components

---

**You're all set to start building!** 🚀
