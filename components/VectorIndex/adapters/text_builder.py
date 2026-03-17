"""
TextBuilder Adapter

Pure functions to convert plan dicts into structured search text
for embedding and tsvector generation.

Format: "{intent_type} | {actions} | {constraint_keys} | {entity_values}"
"""

import logging

logger = logging.getLogger("vectorindex")


def extract_intent_type(plan_data: dict) -> str:
    """Extract intent_type from plan_data with fallback chain.

    Priority order:
        1. plan_data["intent_type"]
        2. plan_data["intent"]["intent"]
        3. "unknown"

    Args:
        plan_data: Plan dictionary.

    Returns:
        Intent type string.
    """
    if not plan_data:
        return "unknown"

    # Priority 1: top-level intent_type
    intent_type = plan_data.get("intent_type")
    if intent_type:
        return str(intent_type)

    # Priority 2: nested intent.intent
    intent_block = plan_data.get("intent")
    if isinstance(intent_block, dict):
        nested = intent_block.get("intent")
        if nested:
            return str(nested)

    return "unknown"


def build_search_text(plan_data: dict) -> str:
    """Build structured text representation of a plan for embedding + tsvector.

    Format:
        "{intent_type} | {action_1} {action_2} ... | {constraint_keys} | {entity_values}"

    Example:
        "schedule_meeting | search_calendar check_availability | max_duration | Alice SFO"

    Args:
        plan_data: Plan dictionary with optional keys: intent_type, graph,
            constraints, intent (with entities).

    Returns:
        Structured search text string.

    Raises:
        ValueError: If plan_data is empty or None.
    """
    if not plan_data:
        raise ValueError("plan_data must be a non-empty dict")

    intent_type = extract_intent_type(plan_data)

    # Extract actions from graph steps
    actions = _extract_actions(plan_data)
    actions_text = " ".join(actions) if actions else ""

    # Extract constraint keys
    constraints = plan_data.get("constraints", {})
    constraint_keys = " ".join(constraints.keys()) if isinstance(constraints, dict) else ""

    # Extract entity values
    entity_values = _extract_entity_values(plan_data)
    entities_text = " ".join(str(v) for v in entity_values) if entity_values else ""

    parts = [intent_type, actions_text, constraint_keys, entities_text]
    return " | ".join(parts)


def _extract_actions(plan_data: dict) -> list[str]:
    """Extract action names from plan graph steps.

    Looks for 'action' or 'call' keys in each step of the graph.

    Args:
        plan_data: Plan dictionary.

    Returns:
        List of action name strings.
    """
    graph = plan_data.get("graph", [])
    if not isinstance(graph, list):
        return []

    actions = []
    for step in graph:
        if not isinstance(step, dict):
            continue
        action = step.get("action") or step.get("call")
        if action:
            actions.append(str(action))
    return actions


def _extract_entity_values(plan_data: dict) -> list[str]:
    """Extract entity values from plan data.

    Looks in plan_data["intent"]["entities"] or plan_data["entities"].

    Args:
        plan_data: Plan dictionary.

    Returns:
        List of entity value strings.
    """
    # Try intent.entities first
    intent_block = plan_data.get("intent")
    if isinstance(intent_block, dict):
        entities = intent_block.get("entities")
        if isinstance(entities, dict):
            return [str(v) for v in entities.values()]

    # Fall back to top-level entities
    entities = plan_data.get("entities")
    if isinstance(entities, dict):
        return [str(v) for v in entities.values()]

    return []
