"""
Fact Deriver -- Pure Function Adapter

Extracts PII-light facts from plan execution context using templates.
Deterministic: same (plan, outcome) always produces the same StoreFactRequest.
No LLM, no side effects, no external calls.

Reference: LLD.md SS7.1
"""

from components.History.domain.models import StoreFactRequest

from ..domain.models import FactDerivationError

# Fact text templates keyed by outcome
_SUCCESS_TEMPLATE = "{action} {entity_summary}"
_FAILURE_TEMPLATE = "Failed to {action}: {error_summary}"
_FALLBACK_TEMPLATE = "Executed {intent_type} plan"

# Default TTL from GLOBAL_SPEC SS7 (Tier 3: 30-day history)
DEFAULT_FACT_TTL_DAYS = 30

# Action verb overrides for common intents
_ACTION_VERBS: dict[str, str] = {
    "book": "Booked",
    "schedule": "Scheduled",
    "search": "Searched",
    "send": "Sent",
    "create": "Created",
    "update": "Updated",
    "delete": "Deleted",
    "cancel": "Cancelled",
    "check": "Checked",
    "get": "Retrieved",
    "find": "Found",
    "set": "Set",
    "add": "Added",
    "remove": "Removed",
}


def derive_fact(
    plan: dict,
    outcome: dict,
) -> StoreFactRequest:
    """Extract a PII-light fact from plan execution context.

    Pure function -- no side effects, no LLM calls. Template-based
    and deterministic: same (plan, outcome) always produces the same
    StoreFactRequest.

    Args:
        plan: Plan dict with plan_id, intent (or meta.intent_type),
              entities, graph.
        outcome: Outcome dict with success, error_type, error_details,
                 failed_step.

    Returns:
        StoreFactRequest ready for FactService.store_fact().

    Raises:
        FactDerivationError: If plan is missing required fields.
    """
    plan_id = plan.get("plan_id")
    if not plan_id:
        raise FactDerivationError(
            plan_id="unknown",
            reason="plan is missing plan_id",
        )

    intent_type = _extract_intent_type(plan)
    entities = _extract_entities(plan)
    action = _build_action_summary(plan)
    entity_summary = _build_entity_summary(entities)
    is_success = outcome.get("success", False)

    if is_success:
        fact_text = _build_success_text(action, entity_summary, intent_type)
    else:
        error_summary = _build_error_summary(outcome)
        fact_text = _build_failure_text(action, error_summary)

    return StoreFactRequest(
        fact_text=fact_text,
        intent_type=intent_type,
        entities=entities,
        outcome=is_success,
        source_plan_id=plan_id,
        ttl_days=DEFAULT_FACT_TTL_DAYS,
    )


def _extract_intent_type(plan: dict) -> str:
    """Extract intent_type from plan structure.

    Looks in: plan["meta"]["intent_type"],
              plan["intent"]["intent"],
              plan["intent_type"].
    Falls back to "unknown".
    """
    meta = plan.get("meta")
    if isinstance(meta, dict):
        intent_type = meta.get("intent_type")
        if intent_type:
            return str(intent_type)

    intent = plan.get("intent")
    if isinstance(intent, dict):
        intent_val = intent.get("intent")
        if intent_val:
            return str(intent_val)

    top_level = plan.get("intent_type")
    if top_level:
        return str(top_level)

    return "unknown"


def _extract_entities(plan: dict) -> dict:
    """Extract entity dict from plan for fact storage.

    Looks in: plan["intent"]["entities"],
              plan["entities"].
    Returns empty dict if not found.
    """
    intent = plan.get("intent")
    if isinstance(intent, dict):
        entities = intent.get("entities")
        if isinstance(entities, dict):
            return entities

    entities = plan.get("entities")
    if isinstance(entities, dict):
        return entities

    return {}


def _build_entity_summary(entities: dict) -> str:
    """Build a human-readable entity summary for fact_text.

    Example: {destination: "NYC", airline: "Delta"} -> "to NYC with Delta"
    Example: {contact: "Alice"} -> "with Alice"
    Example: {} -> ""
    """
    if not entities:
        return ""

    parts = []
    # Use specific prepositions for known entity keys
    preposition_map = {
        "destination": "to",
        "origin": "from",
        "contact": "with",
        "recipient": "to",
        "subject": "about",
        "location": "at",
        "date": "on",
        "time": "at",
    }

    for key, value in entities.items():
        prep = preposition_map.get(key, "with")
        parts.append(f"{prep} {value}")

    return " ".join(parts)


def _build_action_summary(plan: dict) -> str:
    """Build a human-readable action summary from intent_type.

    Example: "schedule_meeting" -> "Scheduled meeting"
    Example: "book_flight" -> "Booked flight"
    Example: "search_products" -> "Searched products"
    """
    intent_type = _extract_intent_type(plan)
    if intent_type == "unknown":
        return "execute action"

    parts = intent_type.split("_")
    if not parts:
        return "execute action"

    verb = parts[0].lower()
    rest = " ".join(parts[1:]) if len(parts) > 1 else ""

    past_verb = _ACTION_VERBS.get(verb, verb.capitalize() + "ed")

    if rest:
        return f"{past_verb} {rest}"
    return past_verb


def _build_error_summary(outcome: dict) -> str:
    """Build a human-readable error summary from outcome.

    Example: {"error_type": "timeout", "failed_step": 3} -> "timeout at step 3"
    Example: {"error_type": "api_error"} -> "api_error"
    """
    error_type = outcome.get("error_type", "unknown error")
    failed_step = outcome.get("failed_step")

    if failed_step is not None:
        return f"{error_type} at step {failed_step}"
    return str(error_type)


def _build_success_text(
    action: str,
    entity_summary: str,
    intent_type: str,
) -> str:
    """Build success fact text from components."""
    if entity_summary:
        text = _SUCCESS_TEMPLATE.format(
            action=action,
            entity_summary=entity_summary,
        )
    else:
        text = _FALLBACK_TEMPLATE.format(intent_type=intent_type)
    return text.strip()


def _build_failure_text(
    action: str,
    error_summary: str,
) -> str:
    """Build failure fact text from components."""
    action_lower = action[0].lower() + action[1:] if action else "execute"
    text = _FAILURE_TEMPLATE.format(
        action=action_lower,
        error_summary=error_summary,
    )
    return text.strip()
