"""
Embedding Client for PlanLibrary

OpenAI API client for generating plan embeddings.
Implements circuit breaker pattern for fault tolerance.

Reference: LLD.md, tasks.md T302/T500
"""

import logging
import os
import time
from enum import Enum

from ..domain.models import EmbeddingServiceError

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    Circuit breaker for external API calls.

    States: CLOSED -> OPEN (after failure_threshold failures) ->
            HALF_OPEN (after timeout) -> CLOSED (on success)
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        timeout_seconds: float = 300.0,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time: float = 0.0

    def can_execute(self) -> bool:
        """Check if circuit allows execution."""
        if self.state == CircuitState.CLOSED:
            return True
        if self.state == CircuitState.OPEN:
            elapsed = time.time() - self.last_failure_time
            if elapsed >= self.timeout_seconds:
                self.state = CircuitState.HALF_OPEN
                logger.info(
                    "Circuit breaker transitioning to HALF_OPEN",
                    extra={"component": "PlanLibrary"},
                )
                return True
            return False
        # HALF_OPEN: allow one attempt
        return True

    def record_success(self) -> None:
        """Record successful call."""
        if self.state == CircuitState.HALF_OPEN:
            logger.info(
                "Circuit breaker transitioning to CLOSED",
                extra={"component": "PlanLibrary"},
            )
        self.failure_count = 0
        self.state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.warning(
                "Circuit breaker OPEN after consecutive failures",
                extra={
                    "failure_count": self.failure_count,
                    "component": "PlanLibrary",
                },
            )


class EmbeddingClient:
    """
    OpenAI embedding API client with circuit breaker.

    Generates 1536-dimension embeddings using text-embedding-ada-002.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "text-embedding-ada-002",
        max_retries: int = 3,
        retry_base_delay: float = 1.0,
    ) -> None:
        """
        Initialize embedding client.

        Args:
            api_key: OpenAI API key (reads OPENAI_API_KEY env if None)
            model: Embedding model name
            max_retries: Maximum retry attempts
            retry_base_delay: Base delay for exponential backoff
        """
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.model = model
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.circuit_breaker = CircuitBreaker(
            failure_threshold=3,
            timeout_seconds=300.0,
        )
        logger.info(
            "Embedding client initialized",
            extra={
                "model": model,
                "component": "PlanLibrary",
            },
        )

    async def generate_embedding(self, text: str) -> list[float]:
        """
        Generate embedding for plan text.

        Args:
            text: Text to generate embedding for

        Returns:
            1536-dimension float vector

        Raises:
            EmbeddingServiceError: On persistent failure
        """
        if not self.circuit_breaker.can_execute():
            logger.warning(
                "Circuit breaker OPEN, skipping embedding generation",
                extra={"component": "PlanLibrary"},
            )
            raise EmbeddingServiceError(
                reason="Circuit breaker is open"
            )

        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                result = await self._call_api(text)
                self.circuit_breaker.record_success()
                return result

            except Exception as e:
                last_error = e
                self.circuit_breaker.record_failure()
                delay = self.retry_base_delay * (2 ** attempt)
                logger.warning(
                    "Embedding generation failed, retrying",
                    extra={
                        "attempt": attempt + 1,
                        "max_retries": self.max_retries,
                        "delay_seconds": delay,
                        "component": "PlanLibrary",
                    },
                )

                if attempt < self.max_retries - 1:
                    import asyncio
                    await asyncio.sleep(delay)

        raise EmbeddingServiceError(
            reason=f"Failed after {self.max_retries} attempts: {last_error}"
        )

    async def _call_api(self, text: str) -> list[float]:
        """
        Make actual API call to OpenAI.

        Args:
            text: Text to embed

        Returns:
            Embedding vector

        Raises:
            Exception: On API failure
        """
        try:
            import openai

            client = openai.AsyncOpenAI(api_key=self.api_key)
            response = await client.embeddings.create(
                input=text,
                model=self.model,
            )
            return response.data[0].embedding

        except Exception as e:
            logger.error(
                "OpenAI API call failed",
                extra={
                    "error_type": type(e).__name__,
                    "component": "PlanLibrary",
                },
            )
            raise
