"""
Fact Deriver -- Pure Function Adapter

Extracts PII-light facts from plan execution context using templates.
Deterministic: same (plan, outcome) always produces the same StoreFactRequest.
No LLM, no side effects, no external calls.

Reference: LLD.md SS7.1
"""

from components.History.domain.models import StoreFactRequest
from shared.schemas.outcome import PlanOutcome
from shared.schemas.plan import Plan

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
    plan: Plan,
    outcome: PlanOutcome,
) -> StoreFactRequest:
    """Extract a PII-light fact from plan execution context.

    Pure function -- no side effects, no LLM calls. Template-based
    and deterministic: same (plan, outcome) always produces the same
    StoreFactRequest.

    Args:
        plan: Typed Plan model with plan_id, intent, graph, meta.
        outcome: Typed PlanOutcome model with success, error_type, etc.

    Returns:
        StoreFactRequest ready for FactService.store_fact().

    Raises:
        FactDerivationError: If plan is missing required fields.
    """
    plan_id = plan.plan_id
    if not plan_id:
        raise FactDerivationError(
            plan_id="unknown",
            reason="plan is missing plan_id",
        )

    intent_type = plan.intent.intent
    entities = plan.intent.entities
    action = _build_action_summary(intent_type)
    entity_summary = _build_entity_summary(entities)

    # Check for Reasoner/hybrid execution steps
    reasoning_summary = _build_reasoning_summary(plan)

    if outcome.success:
        fact_text = _build_success_text(action, entity_summary, intent_type)
        if reasoning_summary:
            fact_text = f"{fact_text} ({reasoning_summary})"
    else:
        error_summary = _build_error_summary(outcome)
        fact_text = _build_failure_text(action, error_summary)

    return StoreFactRequest(
        fact_text=fact_text,
        intent_type=intent_type,
        entities=entities,
        outcome=outcome.success,
        source_plan_id=plan_id,
        ttl_days=DEFAULT_FACT_TTL_DAYS,
    )


def _build_reasoning_summary(plan: Plan) -> str:
    """Build a summary of LLM reasoning steps in the plan.

    Example: Plan with 2 Reasoner steps -> "LLM analyzed 2 reasoning steps"
    Example: Plan with 0 Reasoner steps -> ""
    """
    reasoning_steps = [s for s in plan.graph if s.role == "Reasoner" or s.type == "llm_reasoning"]
    if not reasoning_steps:
        return ""

    count = len(reasoning_steps)
    if count == 1:
        return "LLM analyzed 1 reasoning step"
    return f"LLM analyzed {count} reasoning steps"


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


def _build_action_summary(intent_type: str) -> str:
    """Build a human-readable action summary from intent_type.

    Example: "schedule_meeting" -> "Scheduled meeting"
    Example: "book_flight" -> "Booked flight"
    Example: "search_products" -> "Searched products"
    """
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


def _build_error_summary(outcome: PlanOutcome) -> str:
    """Build a human-readable error summary from outcome.

    Example: PlanOutcome(error_type="timeout", failed_step=3) -> "timeout at step 3"
    Example: PlanOutcome(error_type="api_error") -> "api_error"
    """
    error_type = outcome.error_type or "unknown error"
    failed_step = outcome.failed_step

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
