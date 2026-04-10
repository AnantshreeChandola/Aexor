"""
Reasoner output schema registry -- SPEC 037 FR-014.

Exports SCHEMA_REGISTRY mapping string keys to Pydantic classes
for Tier 1 reasoner output validation.
"""

from pydantic import BaseModel

from .email_summary import EmailSummaryV1
from .flight_recommendation import FlightRecommendationV1
from .free_slots import FreeSlotsV1
from .freebusy_sanitized import FreeBusySanitizedV1
from .slot_proposal import SlotProposalV1

SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {
    "slot_proposal_v1": SlotProposalV1,
    "free_slots_v1": FreeSlotsV1,
    "flight_recommendation_v1": FlightRecommendationV1,
    "email_summary_v1": EmailSummaryV1,
    "freebusy_sanitized_v1": FreeBusySanitizedV1,
}

__all__ = [
    "SCHEMA_REGISTRY",
    "SlotProposalV1",
    "FreeSlotsV1",
    "FlightRecommendationV1",
    "EmailSummaryV1",
    "FreeBusySanitizedV1",
]
