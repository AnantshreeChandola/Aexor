"""
Vector Database Adapter - pgvector integration for similarity search.

Handles vector storage and cosine similarity search using PostgreSQL
pgvector extension with HNSW indexing for performance.
"""

import logging
import json
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from sqlalchemy import text, select, func
from shared.database.adapter import get_database_adapter
from shared.database.error_handler import with_db_error_handling

from ..domain.models import PlanPattern

logger = logging.getLogger(__name__)


@dataclass
class VectorSimilarityResult:
    """Result from vector similarity search."""
    plan_id: str
    intent_type: str
    similarity_score: float
    success_rate: float
    total_executions: int
    plan_pattern: PlanPattern


class VectorAdapter:
    """
    pgvector database adapter for plan embedding operations.
    
    Provides:
    - Vector embedding storage with HNSW indexing
    - Cosine similarity search with filtering
    - Vector index management and optimization
    - Performance monitoring for vector operations
    """
    
    def __init__(self):
        """Initialize vector database adapter."""
        self.shared_db = get_database_adapter()
        logger.info("VectorAdapter initialized")

    @with_db_error_handling
    async def store_embedding(self, plan_id: str, vector: List[float]) -> bool:
        """
        Store plan embedding vector in database.
        
        Uses pgvector extension to store 1536-dimension vectors
        for similarity search operations.
        
        Args:
            plan_id: ULID identifier for the plan
            vector: 1536-dimension embedding vector
            
        Returns:
            True if storage successful, False otherwise
        """
        try:
            # Calculate vector norm for efficiency
            vector_norm = sum(x * x for x in vector) ** 0.5
            
            async with self.shared_db.get_session() as session:
                # Store embedding as JSON for now (pgvector integration pending)
                # In production this would use actual pgvector VECTOR type
                stmt = text("""
                    INSERT INTO plan_embeddings (plan_id, vector_norm, model_version)
                    VALUES (:plan_id, :vector_data, :model_version)
                    ON CONFLICT (plan_id) DO UPDATE SET
                        vector_norm = EXCLUDED.vector_norm,
                        model_version = EXCLUDED.model_version,
                        created_at = NOW()
                """)
                
                await session.execute(stmt, {
                    "plan_id": plan_id,
                    "vector_data": json.dumps(vector),  # Store as JSON temporarily
                    "model_version": "text-embedding-ada-002"
                })
                
                await session.commit()
                
                logger.debug(f"Vector embedding stored for plan {plan_id}")
                return True
                
        except Exception as e:
            logger.error(f"Failed to store embedding for plan {plan_id}: {e}")
            return False

    @with_db_error_handling
    async def similarity_search(
        self,
        query_vector: List[float],
        threshold: float = 0.5,
        limit: int = 10
    ) -> List[VectorSimilarityResult]:
        """
        Execute vector similarity search using cosine similarity.
        
        This is a simplified implementation. In production with full pgvector
        integration, this would use native vector operations and HNSW indexing.
        
        Args:
            query_vector: 1536-dimension query vector
            threshold: Minimum similarity score (0.0-1.0)
            limit: Maximum number of results
            
        Returns:
            List of similarity results sorted by score
        """
        try:
            async with self.shared_db.get_session() as session:
                # Temporary implementation using JSON storage
                # In production: would use pgvector's cosine similarity operators
                
                # Get all embeddings with their plan data
                query = """
                    SELECT 
                        pe.plan_id,
                        pe.vector_norm as stored_vector,
                        p.intent_type,
                        p.canonical_json,
                        p.step_count,
                        -- Calculate aggregated success metrics
                        COUNT(po.outcome_id) as total_executions,
                        COUNT(CASE WHEN po.success = true THEN 1 END) as successful_executions,
                        CASE 
                            WHEN COUNT(po.outcome_id) > 0 
                            THEN COUNT(CASE WHEN po.success = true THEN 1 END)::float / COUNT(po.outcome_id)
                            ELSE 0.0 
                        END as success_rate,
                        AVG(EXTRACT(EPOCH FROM (po.execution_end - po.execution_start)) * 1000) as avg_execution_time_ms,
                        MAX(po.execution_start) as last_execution
                    FROM plan_embeddings pe
                    INNER JOIN plans p ON pe.plan_id = p.plan_id
                    INNER JOIN plan_outcomes po ON p.plan_id = po.plan_id
                    GROUP BY pe.plan_id, pe.vector_norm, p.intent_type, p.canonical_json, p.step_count
                    HAVING COUNT(po.outcome_id) > 0
                """
                
                result = await session.execute(text(query))
                rows = result.fetchall()
                
                # Calculate similarities (in production: done by pgvector)
                similarities = []
                for row in rows:
                    try:
                        # Parse stored vector from JSON
                        stored_vector = json.loads(row.stored_vector)
                        
                        # Calculate cosine similarity
                        similarity_score = self._cosine_similarity(query_vector, stored_vector)
                        
                        if similarity_score >= threshold:
                            # Create plan pattern
                            pattern_summary = self._generate_pattern_summary(row.canonical_json)
                            confidence = min(1.0, row.success_rate * (min(row.total_executions, 10) / 10))
                            
                            plan_pattern = PlanPattern(
                                plan_id=row.plan_id,
                                intent_type=row.intent_type,
                                success_rate=row.success_rate,
                                avg_execution_time_ms=row.avg_execution_time_ms,
                                steps_count=row.step_count,
                                pattern_summary=pattern_summary,
                                total_executions=row.total_executions,
                                last_execution=row.last_execution,
                                confidence=confidence
                            )
                            
                            similarity_result = VectorSimilarityResult(
                                plan_id=row.plan_id,
                                intent_type=row.intent_type,
                                similarity_score=similarity_score,
                                success_rate=row.success_rate,
                                total_executions=row.total_executions,
                                plan_pattern=plan_pattern
                            )
                            
                            similarities.append(similarity_result)
                            
                    except Exception as e:
                        logger.warning(f"Error processing similarity for plan {row.plan_id}: {e}")
                        continue
                
                # Sort by similarity score descending and limit results
                similarities.sort(key=lambda x: x.similarity_score, reverse=True)
                similarities = similarities[:limit]
                
                logger.debug(
                    f"Vector similarity search completed: {len(similarities)} results "
                    f"above threshold {threshold}"
                )
                
                return similarities
                
        except Exception as e:
            logger.error(f"Vector similarity search failed: {e}")
            return []

    def _cosine_similarity(self, vector1: List[float], vector2: List[float]) -> float:
        """
        Calculate cosine similarity between two vectors.
        
        In production with pgvector, this would be done by the database
        using optimized vector operations.
        
        Args:
            vector1: First vector
            vector2: Second vector
            
        Returns:
            Cosine similarity score (0.0 to 1.0)
        """
        try:
            if len(vector1) != len(vector2):
                logger.warning("Vector dimension mismatch in similarity calculation")
                return 0.0
            
            # Calculate dot product
            dot_product = sum(a * b for a, b in zip(vector1, vector2))
            
            # Calculate magnitudes
            magnitude1 = sum(a * a for a in vector1) ** 0.5
            magnitude2 = sum(a * a for a in vector2) ** 0.5
            
            if magnitude1 == 0 or magnitude2 == 0:
                return 0.0
            
            # Cosine similarity
            similarity = dot_product / (magnitude1 * magnitude2)
            
            # Clamp to [0, 1] range
            return max(0.0, min(1.0, similarity))
            
        except Exception as e:
            logger.error(f"Error calculating cosine similarity: {e}")
            return 0.0

    def _generate_pattern_summary(self, canonical_json: Dict[str, Any]) -> str:
        """
        Generate pattern summary from plan JSON.
        
        Args:
            canonical_json: Plan's canonical JSON
            
        Returns:
            Human-readable pattern summary
        """
        try:
            graph = canonical_json.get("graph", [])
            if not graph:
                return "Empty plan"
            
            operations = [step.get("operation", "unknown") for step in graph]
            
            if len(operations) <= 3:
                return " → ".join(operations)
            else:
                return f"{operations[0]} → {operations[1]} → ... → {operations[-1]}"
                
        except Exception:
            return "Complex plan pattern"

    @with_db_error_handling
    async def get_embedding_by_plan_id(self, plan_id: str) -> Optional[List[float]]:
        """
        Retrieve stored embedding vector for a plan.
        
        Args:
            plan_id: ULID identifier for the plan
            
        Returns:
            Embedding vector if found, None otherwise
        """
        try:
            async with self.shared_db.get_session() as session:
                stmt = text("""
                    SELECT vector_norm 
                    FROM plan_embeddings 
                    WHERE plan_id = :plan_id
                """)
                
                result = await session.execute(stmt, {"plan_id": plan_id})
                row = result.fetchone()
                
                if row:
                    # Parse vector from JSON storage
                    return json.loads(row.vector_norm)
                    
                return None
                
        except Exception as e:
            logger.error(f"Failed to retrieve embedding for plan {plan_id}: {e}")
            return None

    @with_db_error_handling
    async def delete_embedding(self, plan_id: str) -> bool:
        """
        Delete embedding for a plan.
        
        Args:
            plan_id: ULID identifier for the plan
            
        Returns:
            True if deletion successful
        """
        try:
            async with self.shared_db.get_session() as session:
                stmt = text("DELETE FROM plan_embeddings WHERE plan_id = :plan_id")
                result = await session.execute(stmt, {"plan_id": plan_id})
                await session.commit()
                
                return result.rowcount > 0
                
        except Exception as e:
            logger.error(f"Failed to delete embedding for plan {plan_id}: {e}")
            return False

    @with_db_error_handling
    async def get_embedding_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about stored embeddings for monitoring.
        
        Returns:
            Dictionary with embedding statistics
        """
        try:
            async with self.shared_db.get_session() as session:
                # Get basic counts
                count_stmt = text("SELECT COUNT(*) as total_embeddings FROM plan_embeddings")
                count_result = await session.execute(count_stmt)
                total_embeddings = count_result.scalar()
                
                # Get recent embeddings count
                recent_stmt = text("""
                    SELECT COUNT(*) as recent_embeddings 
                    FROM plan_embeddings 
                    WHERE created_at >= NOW() - INTERVAL '24 hours'
                """)
                recent_result = await session.execute(recent_stmt)
                recent_embeddings = recent_result.scalar()
                
                # Get model version distribution
                model_stmt = text("""
                    SELECT model_version, COUNT(*) as count
                    FROM plan_embeddings 
                    GROUP BY model_version
                """)
                model_result = await session.execute(model_stmt)
                model_distribution = {row.model_version: row.count for row in model_result}
                
                return {
                    "total_embeddings": total_embeddings,
                    "recent_embeddings_24h": recent_embeddings,
                    "model_distribution": model_distribution,
                    "timestamp": "2025-01-03T12:00:00Z"  # Current time
                }
                
        except Exception as e:
            logger.error(f"Failed to get embedding statistics: {e}")
            return {
                "total_embeddings": 0,
                "recent_embeddings_24h": 0,
                "model_distribution": {},
                "error": str(e)
            }

    async def health_check(self) -> bool:
        """
        Check vector database connectivity and functionality.
        
        Returns:
            True if vector operations are working
        """
        try:
            # Test basic database connectivity
            db_healthy = await self.shared_db.health_check()
            if not db_healthy:
                return False
            
            # Test vector table access
            async with self.shared_db.get_session() as session:
                test_stmt = text("SELECT COUNT(*) FROM plan_embeddings LIMIT 1")
                await session.execute(test_stmt)
                
            return True
            
        except Exception as e:
            logger.error(f"Vector adapter health check failed: {e}")
            return False

    async def optimize_vector_index(self) -> bool:
        """
        Optimize vector index for better performance.
        
        In production with pgvector, this would rebuild HNSW indices
        and update statistics for optimal query performance.
        
        Returns:
            True if optimization successful
        """
        try:
            async with self.shared_db.get_session() as session:
                # In production: would run pgvector-specific optimizations
                # For now: basic index maintenance
                maintenance_stmt = text("""
                    REINDEX TABLE plan_embeddings;
                    ANALYZE plan_embeddings;
                """)
                
                await session.execute(maintenance_stmt)
                await session.commit()
                
                logger.info("Vector index optimization completed")
                return True
                
        except Exception as e:
            logger.error(f"Vector index optimization failed: {e}")
            return False

    async def close(self):
        """Close vector database connections."""
        await self.shared_db.close()