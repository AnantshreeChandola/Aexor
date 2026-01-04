"""
Vector Service - Embedding generation and similarity search.

Handles OpenAI embedding generation with circuit breaker pattern,
pgvector similarity search, and background embedding queue.
"""

import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any

from ..domain.models import SimilarityMatch, PlanPattern
from ..adapters.embedding_client import EmbeddingClient
from ..adapters.vector_db import VectorAdapter

logger = logging.getLogger(__name__)


class EmbeddingServiceError(Exception):
    """Raised when embedding generation fails."""
    pass


class VectorSearchUnavailableError(Exception):
    """Raised when vector search is temporarily unavailable."""
    pass


class VectorService:
    """
    Vector embedding and similarity search service.
    
    Implements:
    - Async embedding generation with OpenAI API
    - Circuit breaker pattern for API failures
    - Background queue for embedding retry
    - pgvector cosine similarity search
    """
    
    def __init__(
        self,
        embedding_client: EmbeddingClient,
        vector_adapter: VectorAdapter
    ):
        """
        Initialize vector service with required adapters.
        
        Args:
            embedding_client: OpenAI embedding API client
            vector_adapter: pgvector database adapter
        """
        self.embedding_client = embedding_client
        self.vector_adapter = vector_adapter
        self._embedding_queue: asyncio.Queue = asyncio.Queue()
        self._queue_task: Optional[asyncio.Task] = None
        
        logger.info("VectorService initialized")
        
        # Start background embedding processor
        self._start_embedding_processor()

    def _start_embedding_processor(self):
        """Start background task for processing embedding queue."""
        if self._queue_task is None or self._queue_task.done():
            self._queue_task = asyncio.create_task(self._process_embedding_queue())
            logger.info("Background embedding processor started")

    async def similarity_search(
        self,
        query_text: str,
        similarity_threshold: float = 0.5,
        limit: int = 10,
        success_threshold: float = 0.5
    ) -> List[SimilarityMatch]:
        """
        Find similar plans using vector embeddings.
        
        Performs cosine similarity search in pgvector and filters
        by similarity threshold and success rate.
        
        Args:
            query_text: Text to find similar plans for
            similarity_threshold: Minimum similarity score (0.0-1.0)
            limit: Maximum number of results
            success_threshold: Minimum success rate filter
            
        Returns:
            List of SimilarityMatch objects sorted by relevance score
            
        Raises:
            VectorSearchUnavailableError: If vector search is unavailable
            ValueError: If parameters are invalid
        """
        start_time = datetime.now(timezone.utc)
        
        try:
            # Validate parameters
            if not query_text or len(query_text.strip()) == 0:
                raise ValueError("query_text cannot be empty")
            
            if not (0.0 <= similarity_threshold <= 1.0):
                raise ValueError("similarity_threshold must be between 0.0 and 1.0")
            
            if limit <= 0 or limit > 1000:
                raise ValueError("limit must be between 1 and 1000")
            
            # Generate embedding for query text
            try:
                query_vector = await self.embedding_client.generate_embedding(query_text)
            except Exception as e:
                logger.error(f"Failed to generate query embedding: {e}")
                raise VectorSearchUnavailableError("Embedding generation failed")
            
            # Perform vector similarity search
            similarity_results = await self.vector_adapter.similarity_search(
                query_vector=query_vector,
                threshold=similarity_threshold,
                limit=limit * 2  # Get more results to filter by success rate
            )
            
            # Convert to SimilarityMatch objects and filter by success rate
            matches = []
            for result in similarity_results:
                if result.success_rate >= success_threshold:
                    # Calculate combined relevance score
                    relevance_score = self._calculate_relevance_score(
                        similarity_score=result.similarity_score,
                        success_rate=result.success_rate,
                        total_executions=result.total_executions
                    )
                    
                    match = SimilarityMatch(
                        plan_id=result.plan_id,
                        intent_type=result.intent_type,
                        similarity_score=result.similarity_score,
                        success_rate=result.success_rate,
                        relevance_score=relevance_score,
                        plan_pattern=result.plan_pattern
                    )
                    matches.append(match)
            
            # Sort by relevance score and limit results
            matches.sort(key=lambda x: x.relevance_score, reverse=True)
            matches = matches[:limit]
            
            # Log search performance
            latency_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
            logger.info(
                "Vector similarity search completed",
                extra={
                    "query_length": len(query_text),
                    "similarity_threshold": similarity_threshold,
                    "results_count": len(matches),
                    "search_latency_ms": latency_ms,
                    "component": "PlanLibrary"
                }
            )
            
            return matches
            
        except (ValueError, VectorSearchUnavailableError):
            # Re-raise domain errors
            raise
        except Exception as e:
            logger.error(f"Unexpected error in similarity search: {e}")
            raise VectorSearchUnavailableError(f"Search failed: {str(e)}")

    async def queue_embedding_generation(
        self, 
        plan_id: str, 
        plan_text: str
    ) -> bool:
        """
        Queue background embedding generation for a plan.
        
        Non-blocking operation that queues the plan for async
        embedding generation with retry logic.
        
        Args:
            plan_id: ULID of the plan
            plan_text: Text representation for embedding
            
        Returns:
            True if successfully queued, False if queue is full
        """
        try:
            # Add to queue (non-blocking)
            embedding_task = {
                "plan_id": plan_id,
                "plan_text": plan_text,
                "queued_at": datetime.now(timezone.utc),
                "retry_count": 0
            }
            
            self.embedding_queue.put_nowait(embedding_task)
            
            logger.debug(f"Embedding generation queued for plan {plan_id}")
            return True
            
        except asyncio.QueueFull:
            logger.warning(f"Embedding queue full, skipping plan {plan_id}")
            return False
        except Exception as e:
            logger.error(f"Error queuing embedding for plan {plan_id}: {e}")
            return False

    async def _process_embedding_queue(self):
        """
        Background task to process embedding generation queue.
        
        Runs continuously processing queued embedding requests
        with retry logic and exponential backoff.
        """
        logger.info("Starting embedding queue processor")
        
        while True:
            try:
                # Wait for next task (blocking)
                task = await self.embedding_queue.get()
                
                try:
                    await self._generate_and_store_embedding(task)
                except Exception as e:
                    await self._handle_embedding_failure(task, e)
                finally:
                    self.embedding_queue.task_done()
                    
            except asyncio.CancelledError:
                logger.info("Embedding queue processor cancelled")
                break
            except Exception as e:
                logger.error(f"Error in embedding queue processor: {e}")
                # Continue processing despite errors
                await asyncio.sleep(1)

    async def _generate_and_store_embedding(self, task: Dict[str, Any]):
        """
        Generate embedding and store in database.
        
        Args:
            task: Dictionary with plan_id, plan_text, etc.
        """
        plan_id = task["plan_id"]
        plan_text = task["plan_text"]
        
        try:
            # Generate embedding vector
            vector = await self.embedding_client.generate_embedding(plan_text)
            
            # Store in vector database
            success = await self.vector_adapter.store_embedding(
                plan_id=plan_id,
                vector=vector
            )
            
            if success:
                logger.info(f"Embedding generated and stored for plan {plan_id}")
            else:
                raise EmbeddingServiceError("Failed to store embedding in database")
                
        except Exception as e:
            logger.error(f"Failed to generate/store embedding for plan {plan_id}: {e}")
            raise

    async def _handle_embedding_failure(self, task: Dict[str, Any], error: Exception):
        """
        Handle failed embedding generation with retry logic.
        
        Args:
            task: Failed embedding task
            error: Exception that caused failure
        """
        plan_id = task["plan_id"]
        retry_count = task.get("retry_count", 0)
        max_retries = 3
        
        if retry_count < max_retries:
            # Exponential backoff: 2^retry_count minutes
            delay_minutes = 2 ** retry_count
            delay_seconds = delay_minutes * 60
            
            logger.warning(
                f"Embedding generation failed for plan {plan_id} "
                f"(attempt {retry_count + 1}/{max_retries}). "
                f"Retrying in {delay_minutes} minutes: {error}"
            )
            
            # Re-queue with updated retry count
            task["retry_count"] = retry_count + 1
            
            # Schedule retry with delay
            asyncio.create_task(self._delayed_retry(task, delay_seconds))
        else:
            logger.error(
                f"Embedding generation failed permanently for plan {plan_id} "
                f"after {max_retries} retries: {error}"
            )

    async def _delayed_retry(self, task: Dict[str, Any], delay_seconds: float):
        """
        Schedule delayed retry of embedding generation.
        
        Args:
            task: Embedding task to retry
            delay_seconds: Delay before retry
        """
        await asyncio.sleep(delay_seconds)
        
        try:
            await self.embedding_queue.put(task)
        except Exception as e:
            logger.error(f"Failed to re-queue embedding task: {e}")

    def _calculate_relevance_score(
        self,
        similarity_score: float,
        success_rate: float,
        total_executions: int
    ) -> float:
        """
        Calculate combined relevance score for ranking results.
        
        Combines similarity, success rate, and execution confidence
        into a single ranking score.
        
        Args:
            similarity_score: Cosine similarity score (0.0-1.0)
            success_rate: Plan success rate (0.0-1.0)
            total_executions: Number of plan executions
            
        Returns:
            Combined relevance score (0.0-1.0)
        """
        # Weights for different factors
        similarity_weight = 0.5  # Text similarity importance
        success_weight = 0.3     # Success rate importance  
        confidence_weight = 0.2  # Execution confidence importance
        
        # Calculate execution confidence (more executions = more confidence)
        # Use sigmoid to map execution count to 0-1 range
        execution_confidence = min(1.0, total_executions / 10.0)
        
        # Weighted combination
        relevance_score = (
            similarity_weight * similarity_score +
            success_weight * success_rate +
            confidence_weight * execution_confidence
        )
        
        return min(1.0, max(0.0, relevance_score))

    @property
    def embedding_queue(self) -> asyncio.Queue:
        """Get reference to embedding queue for testing."""
        return self._embedding_queue

    async def get_queue_status(self) -> Dict[str, Any]:
        """
        Get embedding queue status for monitoring.
        
        Returns:
            Dictionary with queue statistics
        """
        return {
            "queue_size": self.embedding_queue.qsize(),
            "processor_running": self._queue_task and not self._queue_task.done(),
            "processor_status": "running" if self._queue_task and not self._queue_task.done() else "stopped"
        }

    async def health_check(self) -> bool:
        """
        Check health of vector service dependencies.
        
        Returns:
            True if all dependencies are healthy
        """
        try:
            # Check embedding client (circuit breaker status)
            embedding_healthy = await self.embedding_client.health_check()
            
            # Check vector database
            vector_db_healthy = await self.vector_adapter.health_check()
            
            # Check queue processor is running
            processor_running = self._queue_task and not self._queue_task.done()
            
            return embedding_healthy and vector_db_healthy and processor_running
            
        except Exception as e:
            logger.error(f"Vector service health check failed: {e}")
            return False

    async def close(self):
        """
        Shutdown vector service and cleanup resources.
        
        Cancels background tasks and waits for queue to empty.
        """
        logger.info("Shutting down VectorService")
        
        # Cancel background processor
        if self._queue_task and not self._queue_task.done():
            self._queue_task.cancel()
            try:
                await self._queue_task
            except asyncio.CancelledError:
                pass
        
        # Wait for remaining queue items to process
        if not self.embedding_queue.empty():
            logger.info("Waiting for embedding queue to empty")
            await self.embedding_queue.join()
        
        logger.info("VectorService shutdown complete")