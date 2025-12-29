"""
Shared Database Error Handling

Global error handling wrapper for database operations.
Provides consistent error handling and transaction management.
"""

import logging
import asyncio
from typing import Callable, TypeVar, Any
from functools import wraps
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

T = TypeVar('T')


class DatabaseError(Exception):
    """Base exception for database-related errors."""
    pass


class UserNotFoundError(DatabaseError):
    """User ID does not exist in the database."""
    def __init__(self, user_id):
        self.user_id = user_id
        super().__init__(f"User {user_id} not found")


class DatabaseConnectionError(DatabaseError):
    """Database connection or transaction error."""
    pass


class DatabaseIntegrityError(DatabaseError):
    """Database integrity constraint violation."""
    pass


def with_db_error_handling(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator to wrap database operations with error handling.
    
    Provides:
    - Automatic transaction rollback on errors
    - Consistent error logging
    - Translation of SQLAlchemy errors to domain errors
    
    Usage:
        @with_db_error_handling
        async def some_db_operation(session: AsyncSession, ...):
            # Database operations here
            pass
    """
    @wraps(func)
    async def wrapper(*args, **kwargs) -> T:
        # Find AsyncSession in args or kwargs
        session = None
        for arg in args:
            if isinstance(arg, AsyncSession):
                session = arg
                break
        
        if session is None:
            session = kwargs.get('session')
        
        try:
            result = await func(*args, **kwargs)
            
            # Commit if we have a session and it's in a transaction
            if session and session.in_transaction():
                await session.commit()
                
            return result
            
        except IntegrityError as e:
            if session and session.in_transaction():
                await session.rollback()
            
            logger.error(f"Database integrity error in {func.__name__}: {e}")
            raise DatabaseIntegrityError(f"Database integrity constraint violated: {str(e)}")
            
        except SQLAlchemyError as e:
            if session and session.in_transaction():
                await session.rollback()
            
            logger.error(f"Database error in {func.__name__}: {e}")
            raise DatabaseConnectionError(f"Database operation failed: {str(e)}")
            
        except Exception as e:
            if session and session.in_transaction():
                await session.rollback()
            
            logger.error(f"Unexpected error in {func.__name__}: {e}")
            raise
    
    return wrapper


def with_user_existence_check(user_check_query: str = None):
    """
    Decorator to check if user exists before executing operation.
    
    Args:
        user_check_query: Custom SQL query to check user existence
                         Default: "SELECT 1 FROM users WHERE user_id = :user_id AND deleted_at IS NULL"
    
    Usage:
        @with_user_existence_check()
        async def some_operation(session: AsyncSession, user_id: UUID, ...):
            # Operation here - user existence already validated
            pass
    """
    if user_check_query is None:
        user_check_query = "SELECT 1 FROM users WHERE user_id = :user_id AND deleted_at IS NULL"
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            # Find session and user_id
            session = None
            user_id = None
            
            # Look for session in args
            for arg in args:
                if isinstance(arg, AsyncSession):
                    session = arg
                    break
            
            # Look for user_id in args/kwargs
            if len(args) > 1:
                # Assume second arg is user_id if not found in kwargs
                user_id = kwargs.get('user_id', args[1] if len(args) > 1 else None)
            else:
                user_id = kwargs.get('user_id')
            
            if not session:
                session = kwargs.get('session')
            
            if not session or not user_id:
                logger.warning(f"Missing session or user_id in {func.__name__}")
                return await func(*args, **kwargs)
            
            # Check user exists
            try:
                from sqlalchemy import text
                result = await session.execute(
                    text(user_check_query),
                    {"user_id": user_id}
                )
                if not result.fetchone():
                    raise UserNotFoundError(user_id)
                    
            except SQLAlchemyError as e:
                logger.error(f"User existence check failed in {func.__name__}: {e}")
                raise DatabaseConnectionError(f"Failed to verify user existence: {str(e)}")
            
            # Proceed with original function
            return await func(*args, **kwargs)
        
        return wrapper
    return decorator


async def execute_with_retry(
    operation: Callable[[], T],
    max_retries: int = 3,
    retry_delay: float = 1.0
) -> T:
    """
    Execute database operation with automatic retry on transient errors.
    
    Args:
        operation: Async callable to execute
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds
        
    Returns:
        Result of operation
        
    Raises:
        Exception: If all retries exhausted
    """
    last_error = None
    
    for attempt in range(max_retries + 1):
        try:
            return await operation()
            
        except (DatabaseConnectionError, SQLAlchemyError) as e:
            last_error = e
            
            if attempt < max_retries:
                logger.warning(
                    f"Database operation failed (attempt {attempt + 1}/{max_retries + 1}): {e}. "
                    f"Retrying in {retry_delay}s..."
                )
                await asyncio.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logger.error(f"Database operation failed after {max_retries + 1} attempts: {e}")
                raise
        
        except Exception as e:
            # Don't retry non-transient errors
            logger.error(f"Non-retryable error in database operation: {e}")
            raise
    
    # This shouldn't be reached, but just in case
    raise last_error or Exception("Unknown error in retry logic")