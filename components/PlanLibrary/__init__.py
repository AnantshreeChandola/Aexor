"""
PlanLibrary - Memory Layer Component

Memory layer component for storing and retrieving executed plans with outcomes.
Supports plan pattern learning, similarity search, and performance analytics.

Architecture:
- Domain: Plan entities and validation models  
- Service: Business logic for plan storage and querying
- Adapters: Database, vector search, and embedding generation
- API: FastAPI endpoints for plan operations
"""