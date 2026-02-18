# Architecture Decision Records (ADRs)

This directory contains Architecture Decision Records documenting significant architectural choices for the Personal Agent project.

## What are ADRs?

ADRs capture the **why** behind architectural decisions, complementing the **what** documented in the HLD. They provide historical context that helps future architectural decisions build on past choices.

## When to Create an ADR

The architect agent automatically creates ADRs for:

- Any architectural change affecting multiple components
- Performance optimizations with system-wide impact  
- New infrastructure dependencies or technology choices
- Changes to core patterns (preview-first, dual runtime, etc.)
- Security or privacy model modifications
- Database schema or data model changes

## ADR Workflow

1. **Architect agent** analyzes architectural decision
2. **Creates ADR** in this directory using the standard template
3. **Updates HLD** if architecture changes
4. **Other agents** read ADR context for future work

## ADR Template

See any existing ADR file for the standard template structure, or reference the template in `/Users/anantshreechandola/Desktop/Personal-agent/.claude/agents/architect.md`.

## Existing ADRs

- **[ADR-003: Evidence Gathering Optimization](003-evidence-gathering-optimization.md)** - Memory layer performance improvements through denormalized views

## Integration with Agents

All development agents (planner, implementer, verifier, pr-manager) read ADRs to understand architectural context and ensure consistency with established decisions.

This enables:
- **Consistent implementation** following established patterns
- **Informed planning** with awareness of architectural constraints  
- **Quality verification** against architectural principles
- **Proper documentation** linking changes to decisions