"""
Embedding Client - OpenAI API integration with circuit breaker.

Handles OpenAI text-embedding-ada-002 API calls with circuit breaker
pattern, retry logic, and rate limiting for embedding generation.
"""

import asyncio
import logging
import time
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta

import openai
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


class EmbeddingCircuitBreakerError(Exception):
    """Raised when circuit breaker is open."""
    pass


class EmbeddingRateLimitError(Exception):
    """Raised when API rate limit is exceeded."""
    pass


class CircuitBreakerState:
    """Circuit breaker state management."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


class EmbeddingCircuitBreaker:
    """
    Circuit breaker for OpenAI embedding API calls.
    
    Implements circuit breaker pattern to prevent cascade failures
    when the OpenAI API is experiencing issues.
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 300,  # 5 minutes
        success_threshold: int = 3
    ):
        """
        Initialize circuit breaker.
        
        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before trying again
            success_threshold: Consecutive successes needed to close circuit
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time: Optional[datetime] = None
        
        logger.info(f"EmbeddingCircuitBreaker initialized with {failure_threshold} failure threshold")

    def should_allow_request(self) -> bool:
        """
        Check if request should be allowed through circuit breaker.
        
        Returns:
            True if request should proceed, False if blocked
        """
        now = datetime.now(timezone.utc)
        
        if self.state == CircuitBreakerState.CLOSED:
            return True
        
        elif self.state == CircuitBreakerState.OPEN:
            if self.last_failure_time and \
               (now - self.last_failure_time).total_seconds() >= self.recovery_timeout:
                # Transition to half-open for testing
                self.state = CircuitBreakerState.HALF_OPEN
                self.success_count = 0
                logger.info("Circuit breaker transitioning to half-open")
                return True
            else:
                return False
        
        elif self.state == CircuitBreakerState.HALF_OPEN:
            return True
        
        return False

    def record_success(self):
        """Record successful API call."""
        if self.state == CircuitBreakerState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.success_threshold:
                # Service recovered, close circuit
                self.state = CircuitBreakerState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                logger.info("Circuit breaker closed - service recovered")
        elif self.state == CircuitBreakerState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0

    def record_failure(self):
        """Record failed API call."""
        self.failure_count += 1
        self.last_failure_time = datetime.now(timezone.utc)
        
        if self.state == CircuitBreakerState.CLOSED:
            if self.failure_count >= self.failure_threshold:
                # Open circuit
                self.state = CircuitBreakerState.OPEN
                logger.warning(f"Circuit breaker opened after {self.failure_count} failures")
        
        elif self.state == CircuitBreakerState.HALF_OPEN:
            # Failed during testing, back to open
            self.state = CircuitBreakerState.OPEN
            logger.warning("Circuit breaker reopened - service still failing")

    def get_status(self) -> Dict[str, Any]:
        """Get circuit breaker status for monitoring."""
        return {
            "state": self.state,
            "failure_count": self.failure_count,
            "success_count": self.success_count,
            "last_failure_time": self.last_failure_time.isoformat() if self.last_failure_time else None
        }


class EmbeddingClient:
    """
    OpenAI embedding API client with circuit breaker and retry logic.
    
    Provides resilient embedding generation with:
    - Circuit breaker pattern for API failures
    - Exponential backoff retry logic
    - Rate limiting awareness
    - Performance monitoring
    """
    
    def __init__(
        self, 
        api_key: Optional[str] = None,
        model: str = "text-embedding-ada-002",
        max_retries: int = 3,
        base_delay: float = 1.0
    ):
        """
        Initialize embedding client.
        
        Args:
            api_key: OpenAI API key (None to use environment variable)
            model: Embedding model to use
            max_retries: Maximum retry attempts
            base_delay: Base delay for exponential backoff
        """
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = model
        self.max_retries = max_retries
        self.base_delay = base_delay
        
        self.circuit_breaker = EmbeddingCircuitBreaker()
        self.rate_limit_tracker = self._create_rate_limit_tracker()
        
        logger.info(f"EmbeddingClient initialized with model {model}")

    def _create_rate_limit_tracker(self) -> Dict[str, Any]:
        """Create rate limit tracking structure."""
        return {
            "requests_per_minute": 0,
            "tokens_per_minute": 0,
            "last_reset": datetime.now(timezone.utc),
            "daily_requests": 0,
            "daily_reset": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
        }

    async def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding vector for input text.
        
        Implements circuit breaker pattern and retry logic for resilience.
        
        Args:
            text: Input text to generate embedding for
            
        Returns:
            1536-dimension vector from text-embedding-ada-002
            
        Raises:
            EmbeddingCircuitBreakerError: If circuit breaker is open
            EmbeddingRateLimitError: If rate limit exceeded
            Exception: For other API errors after retries
        """
        start_time = time.time()
        
        # Check circuit breaker
        if not self.circuit_breaker.should_allow_request():
            raise EmbeddingCircuitBreakerError("Embedding service circuit breaker is open")
        
        # Check rate limits
        if not self._check_rate_limits(text):
            raise EmbeddingRateLimitError("Embedding API rate limit exceeded")
        
        # Attempt generation with retry logic
        last_exception = None
        for attempt in range(self.max_retries + 1):
            try:
                # Call OpenAI API
                response = await self.client.embeddings.create(
                    model=self.model,
                    input=text.strip()[:8192],  # Limit input size
                    encoding_format="float"
                )
                
                # Extract vector from response
                vector = response.data[0].embedding
                
                # Record success for circuit breaker
                self.circuit_breaker.record_success()
                
                # Update rate limit tracking
                self._update_rate_limits(text, response.usage)
                
                # Log successful generation
                latency_ms = (time.time() - start_time) * 1000
                logger.debug(
                    "Embedding generated successfully",
                    extra={
                        "text_length": len(text),
                        "vector_dimensions": len(vector),
                        "generation_latency_ms": latency_ms,
                        "attempt": attempt + 1,
                        "component": "PlanLibrary"
                    }
                )
                
                return vector
                
            except openai.RateLimitError as e:
                logger.warning(f"OpenAI rate limit exceeded: {e}")
                last_exception = EmbeddingRateLimitError("API rate limit exceeded")
                
                # Don't retry rate limit errors
                self.circuit_breaker.record_failure()
                break
                
            except (openai.APIConnectionError, openai.APITimeoutError) as e:
                last_exception = e
                self.circuit_breaker.record_failure()
                
                if attempt < self.max_retries:
                    delay = self.base_delay * (2 ** attempt)  # Exponential backoff
                    logger.warning(
                        f"Embedding API error (attempt {attempt + 1}/{self.max_retries + 1}): {e}. "
                        f"Retrying in {delay}s"
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Embedding generation failed after {self.max_retries + 1} attempts: {e}")
                    
            except Exception as e:
                logger.error(f"Unexpected embedding generation error: {e}")
                self.circuit_breaker.record_failure()
                last_exception = e
                break
        
        # If we get here, all attempts failed
        if last_exception:
            raise last_exception
        else:
            raise Exception("Embedding generation failed for unknown reason")

    def _check_rate_limits(self, text: str) -> bool:
        """
        Check if request would exceed rate limits.
        
        Args:
            text: Input text to check
            
        Returns:
            True if request should be allowed
        """
        now = datetime.now(timezone.utc)
        tracker = self.rate_limit_tracker
        
        # Reset counters if needed
        if (now - tracker["last_reset"]).total_seconds() >= 60:
            tracker["requests_per_minute"] = 0
            tracker["tokens_per_minute"] = 0
            tracker["last_reset"] = now
        
        if now.date() > tracker["daily_reset"].date():
            tracker["daily_requests"] = 0
            tracker["daily_reset"] = now.replace(hour=0, minute=0, second=0)
        
        # Estimate token count (rough approximation)
        estimated_tokens = len(text.split()) * 1.3  # Conservative estimate
        
        # Check limits (conservative values)
        if tracker["requests_per_minute"] >= 3000:  # OpenAI limit is higher
            return False
        
        if tracker["tokens_per_minute"] + estimated_tokens > 1_000_000:  # Conservative
            return False
        
        if tracker["daily_requests"] >= 1_000_000:  # Daily limit
            return False
        
        return True

    def _update_rate_limits(self, text: str, usage: Any):
        """
        Update rate limit counters after successful request.
        
        Args:
            text: Input text that was processed
            usage: Usage object from OpenAI response
        """
        tracker = self.rate_limit_tracker
        tracker["requests_per_minute"] += 1
        tracker["daily_requests"] += 1
        
        # Update token count if available in usage
        if hasattr(usage, 'total_tokens'):
            tracker["tokens_per_minute"] += usage.total_tokens
        else:
            # Fallback estimation
            tracker["tokens_per_minute"] += len(text.split()) * 1.3

    async def health_check(self) -> bool:
        """
        Check embedding client health.
        
        Tests API connectivity and circuit breaker status.
        
        Returns:
            True if service is healthy
        """
        try:
            # Check circuit breaker status
            if not self.circuit_breaker.should_allow_request():
                logger.warning("Embedding health check failed: circuit breaker open")
                return False
            
            # Test with minimal API call
            test_response = await self.generate_embedding("test")
            
            return isinstance(test_response, list) and len(test_response) == 1536
            
        except Exception as e:
            logger.error(f"Embedding client health check failed: {e}")
            return False

    def get_circuit_breaker_status(self) -> Dict[str, Any]:
        """Get circuit breaker status for monitoring."""
        return self.circuit_breaker.get_status()

    def get_rate_limit_status(self) -> Dict[str, Any]:
        """Get rate limit status for monitoring."""
        return {
            "requests_per_minute": self.rate_limit_tracker["requests_per_minute"],
            "tokens_per_minute": self.rate_limit_tracker["tokens_per_minute"],
            "daily_requests": self.rate_limit_tracker["daily_requests"],
            "last_reset": self.rate_limit_tracker["last_reset"].isoformat()
        }