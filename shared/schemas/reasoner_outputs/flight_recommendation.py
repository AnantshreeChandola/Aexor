"""
FlightRecommendationV1 -- Tier 1 reasoner output schema.

Used when a Tier 1 reasoner recommends flights
from search results.
"""

from pydantic import BaseModel, Field


class FlightOption(BaseModel):
    """A single flight option."""

    airline: str = Field(..., description="Airline name")
    flight_number: str = Field(..., description="Flight number")
    departure: str = Field(
        ..., description="ISO-8601 departure datetime"
    )
    arrival: str = Field(
        ..., description="ISO-8601 arrival datetime"
    )
    price_usd: float = Field(
        ..., ge=0, description="Price in USD"
    )
    stops: int = Field(
        default=0, ge=0, description="Number of stops"
    )


class FlightRecommendationV1(BaseModel):
    """Tier 1 reasoner output: flight recommendation."""

    recommended: FlightOption = Field(
        ..., description="Top recommended flight"
    )
    alternatives: list[FlightOption] = Field(
        default_factory=list,
        description="Alternative flight options",
    )
    reason: str = Field(
        ...,
        description="Explanation for the recommendation",
    )
