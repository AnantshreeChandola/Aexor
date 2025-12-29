# CLAUDE.md

This file provides comprehensive guidance to Claude Code when working with Python code in this repository.

## Core Development Philosophy

### KISS (Keep It Simple, Stupid)

Simplicity should be a key goal in design. Choose straightforward solutions over complex ones whenever possible. Simple solutions are easier to understand, maintain, and debug.

### YAGNI (You Aren't Gonna Need It)

Avoid building functionality on speculation. Implement features only when they are needed, not when you anticipate they might be useful in the future.

### Design Principles

- **Dependency Inversion**: High-level modules should not depend on low-level modules. Both should depend on abstractions.
- **Open/Closed Principle**: Software entities should be open for extension but closed for modification.
- **Single Responsibility**: Each function, class, and module should have one clear purpose.
- **Fail Fast**: Check for potential errors early and raise exceptions immediately when issues occur.

## 🧱 Code Structure & Modularity

### File and Function Limits

- **Never create a file longer than 500 lines of code**. If approaching this limit, refactor by splitting into modules.
- **Functions should be under 50 lines** with a single, clear responsibility.
- **Classes should be under 100 lines** and represent a single concept or entity.
- **Organize code into clearly separated modules**, grouped by feature or responsibility.
- **Line lenght should be max 100 characters** ruff rule in pyproject.toml
- **Use venv_linux** (the virtual environment) whenever executing Python commands, including for unit tests.

### Project Architecture

Follow modular architecture with shared utilities and component-based design:

```
project/
    # SHARED INFRASTRUCTURE (write once, use everywhere)
    shared/
        __init__.py
        database/
            __init__.py
            adapter.py           # Shared database connections
            models.py            # Core shared tables (users, etc.)
            error_handler.py     # Database error decorators
            tests/
                test_adapter.py
                test_models.py
        
        api/
            __init__.py
            error_handlers.py    # API error response handlers
            middleware/
                auth.py          # Authentication middleware
            tests/
                test_error_handlers.py
        
        security/
            __init__.py
            encryption.py        # Shared encryption utilities
            tests/
                test_encryption.py
        
        schemas/
            __init__.py
            evidence.py          # Evidence Item format
            preference_schema.json    # Universal schema
            preference_registry.py    # Programmatic definitions
            tests/
                test_schemas.py

    # COMPONENT ARCHITECTURE (use shared utilities)
    components/
        ProfileStore/
            __init__.py
            api/
                routes.py        # Uses shared error handlers
            service/
                preference_service.py
            domain/
                models.py        # Pydantic models only
            adapters/
                db.py           # Uses shared database adapter
                schema_registry.py
                encryption.py   # Uses shared encryption
            tests/
                test_api.py
                test_service.py
        
        History/
            # Same structure, uses shared utilities
        
        PlanLibrary/
            # Same structure, uses shared utilities

    # ROOT LEVEL
    tests/
        __init__.py
        conftest.py
    main.py
    __init__.py
```

**Key Principles**:
- **Shared utilities** in `shared/` - write once, import everywhere
- **Components** use shared infrastructure, no duplication
- **Tests** live next to the code they test
- **Database models** shared in `shared/database/models.py`
- **Error handling** centralized with decorators and mixins

## 🔄 Shared Infrastructure & DRY Architecture

### CRITICAL: Always Use Shared Utilities

**DRY Principle**: Write shared utilities ONCE, use everywhere. Never duplicate database initialization, error handling, encryption, or validation logic across components.

### Shared Database Layer

**❌ ANTI-PATTERN: Duplicate database setup in every component**
```python
# DON'T DO THIS - Repeated in every adapter
class ComponentAdapter:
    def __init__(self, database_url: str = None):
        if database_url is None:
            database_url = os.getenv("DATABASE_URL")
        
        self.engine = create_async_engine(database_url, pool_size=5, max_overflow=10)
        self.async_session = async_sessionmaker(self.engine, class_=AsyncSession)
        # 20+ lines of duplicate setup code...
```

**✅ BEST PRACTICE: Shared database utilities**
```python
# shared/database/adapter.py - Write ONCE
class SharedDatabaseAdapter:
    def __init__(self, database_url: str = None):
        self.database_url = DatabaseConfig.get_database_url(database_url)
        self.engine = create_async_engine(
            self.database_url, pool_size=5, max_overflow=10, pool_pre_ping=True
        )
        # All setup logic in ONE place
    
    async def get_session(self) -> AsyncContextManager[AsyncSession]:
        """Get database session with automatic transaction management."""
        # Implementation here
    
    async def health_check(self) -> bool:
        """Check database connectivity."""
        # Implementation here

# components/*/adapters/db.py - Use everywhere
class ComponentAdapter:
    def __init__(self):
        self.shared_db = get_database_adapter()  # 1 line!
        
    async def operation(self):
        async with self.shared_db.get_session() as session:
            # Your component logic here
```

### Shared Error Handling with Decorators

**❌ ANTI-PATTERN: Duplicate error handling in every method**
```python
# DON'T DO THIS - Copy-paste in every method
async def get_user(self, user_id: UUID):
    try:
        # Check if user exists
        user_check = await session.execute(
            text("SELECT 1 FROM users WHERE user_id = :user_id"),
            {"user_id": user_id}
        )
        if not user_check.fetchone():
            raise UserNotFoundError(user_id)
        
        # Actual operation
        result = await session.execute(stmt)
        return process_result(result)
        
    except DatabaseError as e:
        logger.error(f"Database error in get_user: {e}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_user: {e}")
        raise
```

**✅ BEST PRACTICE: Error handling decorators**
```python
# shared/database/error_handler.py - Write ONCE
def with_db_error_handling(func):
    """Decorator for consistent database error handling."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except DatabaseError as e:
            logger.error(f"Database error in {func.__name__}: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}")
            raise
    return wrapper

def with_user_existence_check(table_name: str = "users"):
    """Decorator to check if user exists before operation."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Extract user_id from function args/kwargs
            user_id = extract_user_id(args, kwargs, func)
            await check_user_exists(user_id, table_name)
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# Usage in components - Apply decorators
class ComponentAdapter:
    @with_user_existence_check()
    @with_db_error_handling
    async def get_user(self, user_id: UUID):
        # Clean business logic only - no error handling boilerplate!
        async with self.shared_db.get_session() as session:
            result = await session.execute(stmt)
            return process_result(result)
```

### Shared Models Architecture

**❌ ANTI-PATTERN: Duplicate models in every component**
```python
# DON'T DO THIS - Each component recreates same tables
# components/ProfileStore/models.py
class UserTable(Base):
    __tablename__ = "users"
    user_id = Column(UUID, primary_key=True)
    # ... duplicate definition

# components/History/models.py  
class UserTable(Base):  # DUPLICATE!
    __tablename__ = "users"
    user_id = Column(UUID, primary_key=True)
    # ... same definition again
```

**✅ BEST PRACTICE: Shared database models**
```python
# shared/database/models.py - Define ONCE
class UserTable(Base):
    """Core user table - shared across all components."""
    __tablename__ = "users"
    
    user_id = Column(UUID(as_uuid=True), primary_key=True)
    email = Column(String(255), unique=True, nullable=False)
    context_tier = Column(Integer, nullable=False, default=1)
    # ... complete definition in ONE place

class PreferenceTable(Base):
    """Preference storage - owned by ProfileStore."""
    __tablename__ = "preferences"
    
    preference_id = Column(UUID(as_uuid=True), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"))
    # ... specialized table definition

# All components import from shared models
from shared.database.models import UserTable, PreferenceTable
```

### Universal Schema Architecture

**❌ ANTI-PATTERN: Individual schema files for each preference type**
```
schemas/
  ├── meeting_duration.json      # Duplicate validation logic
  ├── work_hours.json           # Duplicate validation logic  
  ├── timezone.json             # Duplicate validation logic
  ├── passport_number.json      # Duplicate validation logic
  └── notification_settings.json # 100+ lines of duplicate structure
```

**✅ BEST PRACTICE: Universal schema with metadata-driven validation**
```python
# shared/schemas/preference_schema.json - ONE schema for ALL types
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "Universal Preference Schema",
  "description": "Flexible schema for all preference types with metadata-driven validation",
  "properties": {
    "key": {"type": "string", "pattern": "^[a-zA-Z0-9_]+$"},
    "value": {"oneOf": [{"type": "string"}, {"type": "number"}, {"type": "object"}]},
    "metadata": {
      "type": "object",
      "properties": {
        "type": {"enum": ["string", "integer", "object"]},
        "sensitive": {"type": "boolean"},
        "validation": {"type": "object"}  // Type-specific rules
      }
    }
  }
}

# shared/schemas/preference_registry.py - Programmatic definitions
class PreferenceRegistry:
    def __init__(self):
        self.preferences = {}
        self._register_core_preferences()
    
    def _register_core_preferences(self):
        self.register(PreferenceDefinition(
            key="meeting_duration_min",
            value_type="integer", 
            default=30,
            validation={"minimum": 15, "maximum": 240}
        ))
        # Define all preferences programmatically
```

### Shared Infrastructure Rules

1. **Database Layer**: Use `shared/database/adapter.py` for ALL database connections
2. **Error Handling**: Use decorators from `shared/database/error_handler.py` 
3. **API Errors**: Use `shared/api/error_handlers.py` for consistent responses
4. **Models**: Define shared tables in `shared/database/models.py`
5. **Schemas**: Use universal schemas with metadata-driven validation
6. **Encryption**: Use shared utilities in `shared/security/`

### Benefits of Shared Architecture

- ✅ **~70% reduction** in duplicate code
- ✅ **Single source of truth** for database connections, error handling, models
- ✅ **Consistent behavior** across all components  
- ✅ **Easier maintenance** - fix once, applies everywhere
- ✅ **Better testing** - test shared utilities thoroughly once
- ✅ **Faster development** - no need to rewrite common functionality

## 🛠️ Development Environment

### UV Package Management

This project uses UV for blazing-fast Python package and environment management.

```bash
# Install UV (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment
uv venv

# Sync dependencies
uv sync

# Add a package ***NEVER UPDATE A DEPENDENCY DIRECTLY IN PYPROJECT.toml***
# ALWAYS USE UV ADD
uv add requests

# Add development dependency
uv add --dev pytest ruff mypy

# Remove a package
uv remove requests

# Run commands in the environment
uv run python script.py
uv run pytest
uv run ruff check .

# Install specific Python version
uv python install 3.12
```

### Development Commands

```bash
# Run all tests
uv run pytest

# Run specific tests with verbose output
uv run pytest tests/test_module.py -v

# Run tests with coverage
uv run pytest --cov=src --cov-report=html

# Format code
uv run ruff format .

# Check linting
uv run ruff check .

# Fix linting issues automatically
uv run ruff check --fix .

# Type checking
uv run mypy src/

# Run pre-commit hooks
uv run pre-commit run --all-files
```

## 📋 Style & Conventions

### Python Style Guide

- **Follow PEP8** with these specific choices:
  - Line length: 100 characters (set by Ruff in pyproject.toml)
  - Use double quotes for strings
  - Use trailing commas in multi-line structures
- **Always use type hints** for function signatures and class attributes
- **Format with `ruff format`** (faster alternative to Black)
- **Use `pydantic` v2** for data validation and settings management

### Docstring Standards

Use Google-style docstrings for all public functions, classes, and modules:

```python
def calculate_discount(
    price: Decimal,
    discount_percent: float,
    min_amount: Decimal = Decimal("0.01")
) -> Decimal:
    """
    Calculate the discounted price for a product.

    Args:
        price: Original price of the product
        discount_percent: Discount percentage (0-100)
        min_amount: Minimum allowed final price

    Returns:
        Final price after applying discount

    Raises:
        ValueError: If discount_percent is not between 0 and 100
        ValueError: If final price would be below min_amount

    Example:
        >>> calculate_discount(Decimal("100"), 20)
        Decimal('80.00')
    """
```

### Naming Conventions

- **Variables and functions**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private attributes/methods**: `_leading_underscore`
- **Type aliases**: `PascalCase`
- **Enum values**: `UPPER_SNAKE_CASE`

## 🧪 Testing Strategy

### Test-Driven Development (TDD)

1. **Write the test first** - Define expected behavior before implementation
2. **Watch it fail** - Ensure the test actually tests something
3. **Write minimal code** - Just enough to make the test pass
4. **Refactor** - Improve code while keeping tests green
5. **Repeat** - One test at a time

### Testing Best Practices

```python
# Always use pytest fixtures for setup
import pytest
from datetime import datetime

@pytest.fixture
def sample_user():
    """Provide a sample user for testing."""
    return User(
        id=123,
        name="Test User",
        email="test@example.com",
        created_at=datetime.now()
    )

# Use descriptive test names
def test_user_can_update_email_when_valid(sample_user):
    """Test that users can update their email with valid input."""
    new_email = "newemail@example.com"
    sample_user.update_email(new_email)
    assert sample_user.email == new_email

# Test edge cases and error conditions
def test_user_update_email_fails_with_invalid_format(sample_user):
    """Test that invalid email formats are rejected."""
    with pytest.raises(ValidationError) as exc_info:
        sample_user.update_email("not-an-email")
    assert "Invalid email format" in str(exc_info.value)
```

### Test Organization

- Unit tests: Test individual functions/methods in isolation
- Integration tests: Test component interactions
- End-to-end tests: Test complete user workflows
- Keep test files next to the code they test
- Use `conftest.py` for shared fixtures
- Aim for 80%+ code coverage, but focus on critical paths

## 🚨 Error Handling

### DRY Principle: Never Repeat Exception Handling Code

**❌ ANTI-PATTERN: Duplicate exception handling in every route/function**
```python
# DON'T DO THIS - Repeated in every route
try:
    result = service.operation()
    return success_response(result)
except UserNotFoundError as e:
    logger.warning(f"User not found: {e}")
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=ErrorResponse(
            error_code="USER_NOT_FOUND",
            message=str(e),
            details={"user_id": str(e.user_id)}
        ).model_dump()
    )
except ConsentDeniedError as e:
    logger.warning(f"Consent denied: {e}")
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content=ErrorResponse(
            error_code="CONSENT_DENIED",
            message=str(e),
            details={
                "user_id": str(e.user_id),
                "required_tier": e.required_tier,
                "current_tier": e.current_tier
            }
        ).model_dump()
    )
    # ... 50+ more lines of duplicate code
```

**✅ BEST PRACTICE: Centralized error handling**
```python
# shared/api/error_handlers.py
class ErrorHandlerMixin:
    """Centralized error handling for API routes."""
    
    def handle_service_errors(self, error) -> JSONResponse:
        """Handle all common service exceptions in one place."""
        error_type = type(error).__name__
        
        if error_type == 'UserNotFoundError':
            return APIErrorHandler.handle_user_not_found(error)
        elif error_type == 'ConsentDeniedError':
            return APIErrorHandler.handle_consent_denied(error)
        elif error_type == 'ValidationError':
            return APIErrorHandler.handle_validation_error(error)
        # ... handle all error types once
        
# routes.py - Clean and DRY
error_handler = ErrorHandlerMixin()

@router.post("/endpoint")
async def clean_endpoint():
    try:
        result = service.operation()
        return success_response(result)
    except (UserNotFoundError, ConsentDeniedError, ValidationError) as e:
        return error_handler.handle_service_errors(e)  # Just 1 line!
```

### Exception Best Practices

```python
# Create custom exceptions for your domain
class PaymentError(Exception):
    """Base exception for payment-related errors."""
    pass

class InsufficientFundsError(PaymentError):
    """Raised when account has insufficient funds."""
    def __init__(self, required: Decimal, available: Decimal):
        self.required = required
        self.available = available
        super().__init__(
            f"Insufficient funds: required {required}, available {available}"
        )

# Use specific exception handling
try:
    process_payment(amount)
except InsufficientFundsError as e:
    logger.warning(f"Payment failed: {e}")
    return PaymentResult(success=False, reason="insufficient_funds")
except PaymentError as e:
    logger.error(f"Payment error: {e}")
    return PaymentResult(success=False, reason="payment_error")

# Use context managers for resource management
from contextlib import contextmanager

@contextmanager
def database_transaction():
    """Provide a transactional scope for database operations."""
    conn = get_connection()
    trans = conn.begin_transaction()
    try:
        yield conn
        trans.commit()
    except Exception:
        trans.rollback()
        raise
    finally:
        conn.close()
```

### Logging Strategy

```python
import logging
from functools import wraps

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

# Log function entry/exit for debugging
def log_execution(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        logger.debug(f"Entering {func.__name__}")
        try:
            result = func(*args, **kwargs)
            logger.debug(f"Exiting {func.__name__} successfully")
            return result
        except Exception as e:
            logger.exception(f"Error in {func.__name__}: {e}")
            raise
    return wrapper
```

## 🔧 Configuration Management

### Environment Variables and Settings

```python
from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    """Application settings with validation."""
    app_name: str = "MyApp"
    debug: bool = False
    database_url: str
    redis_url: str = "redis://localhost:6379"
    api_key: str
    max_connections: int = 100

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False

@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()

# Usage
settings = get_settings()
```

## 🏗️ Data Models and Validation

### Example Pydantic Models strict with pydantic v2

```python
from pydantic import BaseModel, Field, validator, EmailStr
from datetime import datetime
from typing import Optional, List
from decimal import Decimal

class ProductBase(BaseModel):
    """Base product model with common fields."""
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    price: Decimal = Field(..., gt=0, decimal_places=2)
    category: str
    tags: List[str] = []

    @validator('price')
    def validate_price(cls, v):
        if v > Decimal('1000000'):
            raise ValueError('Price cannot exceed 1,000,000')
        return v

    class Config:
        json_encoders = {
            Decimal: str,
            datetime: lambda v: v.isoformat()
        }

class ProductCreate(ProductBase):
    """Model for creating new products."""
    pass

class ProductUpdate(BaseModel):
    """Model for updating products - all fields optional."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(None, gt=0, decimal_places=2)
    category: Optional[str] = None
    tags: Optional[List[str]] = None

class Product(ProductBase):
    """Complete product model with database fields."""
    id: int
    created_at: datetime
    updated_at: datetime
    is_active: bool = True

    class Config:
        from_attributes = True  # Enable ORM mode
```

## 🔄 Git Workflow

### Branch Strategy

- `main` - Production-ready code
- `develop` - Integration branch for features
- `feature/*` - New features
- `fix/*` - Bug fixes
- `docs/*` - Documentation updates
- `refactor/*` - Code refactoring
- `test/*` - Test additions or fixes

### Commit Message Format

Never include claude code, or written by claude code in commit messages

```
<type>(<scope>): <subject>

<body>

<footer>
``
Types: feat, fix, docs, style, refactor, test, chore

Example:
```

feat(auth): add two-factor authentication

- Implement TOTP generation and validation
- Add QR code generation for authenticator apps
- Update user model with 2FA fields

Closes #123

````

## 🗄️ Database Naming Standards

### Entity-Specific Primary Keys
All database tables use entity-specific primary keys for clarity and consistency:

```sql
-- ✅ STANDARDIZED: Entity-specific primary keys
sessions.session_id UUID PRIMARY KEY
leads.lead_id UUID PRIMARY KEY
messages.message_id UUID PRIMARY KEY
daily_metrics.daily_metric_id UUID PRIMARY KEY
agencies.agency_id UUID PRIMARY KEY
````

### Field Naming Conventions

```sql
-- Primary keys: {entity}_id
session_id, lead_id, message_id

-- Foreign keys: {referenced_entity}_id
session_id REFERENCES sessions(session_id)
agency_id REFERENCES agencies(agency_id)

-- Timestamps: {action}_at
created_at, updated_at, started_at, expires_at

-- Booleans: is_{state}
is_connected, is_active, is_qualified

-- Counts: {entity}_count
message_count, lead_count, notification_count

-- Durations: {property}_{unit}
duration_seconds, timeout_minutes
```

### Repository Pattern Auto-Derivation

The enhanced BaseRepository automatically derives table names and primary keys:

```python
# ✅ STANDARDIZED: Convention-based repositories
class LeadRepository(BaseRepository[Lead]):
    def __init__(self):
        super().__init__()  # Auto-derives "leads" and "lead_id"

class SessionRepository(BaseRepository[AvatarSession]):
    def __init__(self):
        super().__init__()  # Auto-derives "sessions" and "session_id"
```

**Benefits**:

- ✅ Self-documenting schema
- ✅ Clear foreign key relationships
- ✅ Eliminates repository method overrides
- ✅ Consistent with entity naming patterns

### Model-Database Alignment

Models mirror database fields exactly to eliminate field mapping complexity:

```python
# ✅ STANDARDIZED: Models mirror database exactly
class Lead(BaseModel):
    lead_id: UUID = Field(default_factory=uuid4)  # Matches database field
    session_id: UUID                               # Matches database field
    agency_id: str                                 # Matches database field
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(
        use_enum_values=True,
        populate_by_name=True,
        alias_generator=None  # Use exact field names
    )
```

### API Route Standards

```python
# ✅ STANDARDIZED: RESTful with consistent parameter naming
router = APIRouter(prefix="/api/v1/leads", tags=["leads"])

@router.get("/{lead_id}")           # GET /api/v1/leads/{lead_id}
@router.put("/{lead_id}")           # PUT /api/v1/leads/{lead_id}
@router.delete("/{lead_id}")        # DELETE /api/v1/leads/{lead_id}

# Sub-resources
@router.get("/{lead_id}/messages")  # GET /api/v1/leads/{lead_id}/messages
@router.get("/agency/{agency_id}")  # GET /api/v1/leads/agency/{agency_id}
```

For complete naming standards, see [NAMING_CONVENTIONS.md](./NAMING_CONVENTIONS.md).

## 📝 Documentation Standards

### Code Documentation

- Every module should have a docstring explaining its purpose
- Public functions must have complete docstrings
- Complex logic should have inline comments with `# Reason:` prefix
- Keep README.md updated with setup instructions and examples
- Maintain CHANGELOG.md for version history

### API Documentation

```python
from fastapi import APIRouter, HTTPException, status
from typing import List

router = APIRouter(prefix="/products", tags=["products"])

@router.get(
    "/",
    response_model=List[Product],
    summary="List all products",
    description="Retrieve a paginated list of all active products"
)
async def list_products(
    skip: int = 0,
    limit: int = 100,
    category: Optional[str] = None
) -> List[Product]:
    """
    Retrieve products with optional filtering.

    - **skip**: Number of products to skip (for pagination)
    - **limit**: Maximum number of products to return
    - **category**: Filter by product category
    """
    # Implementation here
```

## 🚀 Performance Considerations

### Optimization Guidelines

- Profile before optimizing - use `cProfile` or `py-spy`
- Use `lru_cache` for expensive computations
- Prefer generators for large datasets
- Use `asyncio` for I/O-bound operations
- Consider `multiprocessing` for CPU-bound tasks
- Cache database queries appropriately

### Example Optimization

```python
from functools import lru_cache
import asyncio
from typing import AsyncIterator

@lru_cache(maxsize=1000)
def expensive_calculation(n: int) -> int:
    """Cache results of expensive calculations."""
    # Complex computation here
    return result

async def process_large_dataset() -> AsyncIterator[dict]:
    """Process large dataset without loading all into memory."""
    async with aiofiles.open('large_file.json', mode='r') as f:
        async for line in f:
            data = json.loads(line)
            # Process and yield each item
            yield process_item(data)
```

## 🛡️ Security Best Practices

### Security Guidelines

- Never commit secrets - use environment variables
- Validate all user input with Pydantic
- Use parameterized queries for database operations
- Implement rate limiting for APIs
- Keep dependencies updated with `uv`
- Use HTTPS for all external communications
- Implement proper authentication and authorization

### Example Security Implementation

```python
from passlib.context import CryptContext
import secrets

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(password: str) -> str:
    """Hash password using bcrypt."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)

def generate_secure_token(length: int = 32) -> str:
    """Generate a cryptographically secure random token."""
    return secrets.token_urlsafe(length)
```

## 🔍 Debugging Tools

### Debugging Commands

```bash
# Interactive debugging with ipdb
uv add --dev ipdb
# Add breakpoint: import ipdb; ipdb.set_trace()

# Memory profiling
uv add --dev memory-profiler
uv run python -m memory_profiler script.py

# Line profiling
uv add --dev line-profiler
# Add @profile decorator to functions

# Debug with rich traceback
uv add --dev rich
# In code: from rich.traceback import install; install()
```

## 📊 Monitoring and Observability

### Structured Logging

```python
import structlog

logger = structlog.get_logger()

# Log with context
logger.info(
    "payment_processed",
    user_id=user.id,
    amount=amount,
    currency="USD",
    processing_time=processing_time
)
```

## 📚 Useful Resources

### Essential Tools

- UV Documentation: https://github.com/astral-sh/uv
- Ruff: https://github.com/astral-sh/ruff
- Pytest: https://docs.pytest.org/
- Pydantic: https://docs.pydantic.dev/
- FastAPI: https://fastapi.tiangolo.com/

### Python Best Practices

- PEP 8: https://pep8.org/
- PEP 484 (Type Hints): https://www.python.org/dev/peps/pep-0484/
- The Hitchhiker's Guide to Python: https://docs.python-guide.org/

## ⚠️ Important Notes

- **NEVER ASSUME OR GUESS** - When in doubt, ask for clarification
- **Always verify file paths and module names** before use
- **Keep CLAUDE.md updated** when adding new patterns or dependencies
- **Test your code** - No feature is complete without tests
- **Document your decisions** - Future developers (including yourself) will thank you

## 🔍 Search Command Requirements

**CRITICAL**: Always use `rg` (ripgrep) instead of traditional `grep` and `find` commands:

```bash
# ❌ Don't use grep
grep -r "pattern" .

# ✅ Use rg instead
rg "pattern"

# ❌ Don't use find with name
find . -name "*.py"

# ✅ Use rg with file filtering
rg --files | rg "\.py$"
# or
rg --files -g "*.py"
```

**Enforcement Rules:**

```
(
    r"^grep\b(?!.*\|)",
    "Use 'rg' (ripgrep) instead of 'grep' for better performance and features",
),
(
    r"^find\s+\S+\s+-name\b",
    "Use 'rg --files | rg pattern' or 'rg --files -g pattern' instead of 'find -name' for better performance",
),
```

## 🚀 GitHub Flow Workflow Summary

main (protected) ←── PR ←── feature/your-feature
↓ ↑
deploy development

### Daily Workflow:

1. git checkout main && git pull origin main
2. git checkout -b feature/new-feature
3. Make changes + tests
4. git push origin feature/new-feature
5. Create PR → Review → Merge to main

---

_This document is a living guide. Update it as the project evolves and new patterns emerge._