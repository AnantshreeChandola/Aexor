"""
PlanWriter Component

Domain Layer library that persists plan execution outcomes
to PlanLibrary, History, and VectorIndex. Closes the learning loop
so the system improves from past executions.
"""

from components.PlanWriter.adapters.fact_deriver import derive_fact
from components.PlanWriter.domain.models import (
    BulkPersistResult,
    FactDerivationError,
    PersistResult,
    PlanLibraryWriteError,
    PlanWriterError,
)
from components.PlanWriter.service.plan_writer_service import (
    PlanWriterService,
    create_plan_writer_service,
)

__all__ = [
    "BulkPersistResult",
    "FactDerivationError",
    "PersistResult",
    "PlanLibraryWriteError",
    "PlanWriterError",
    "PlanWriterService",
    "create_plan_writer_service",
    "derive_fact",
]
