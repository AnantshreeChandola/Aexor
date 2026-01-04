# Code Style & Conventions

## Python Style (from pyproject.toml)
- **Python 3.11+** minimum version
- **Line length**: 100 characters
- **Indent**: 4 spaces
- **Quote style**: Double quotes
- **Type hints**: Required (strict mypy)

## Ruff Configuration
- Uses pycodestyle (E, W), pyflakes (F), isort (I), flake8-bugbear (B)
- Comprehensions (C4), pyupgrade (UP), unused-arguments (ARG)
- Simplify (SIM), type-checking (TCH), pathlib (PTH)
- Ignores E501 (line length handled by formatter), B008, B904

## MyPy Settings
- Strict mode enabled
- Disallow untyped definitions
- Warn on unused configs and redundant casts
- Check untyped definitions

## Naming Conventions
- **Files**: snake_case.py
- **Classes**: PascalCase
- **Functions/variables**: snake_case
- **Constants**: UPPER_SNAKE_CASE
- **Private members**: _leading_underscore

## Documentation
- **Docstrings**: Use for all public functions/classes
- **Type hints**: Required for all function signatures
- **Comments**: Minimal, code should be self-documenting

## Architecture Patterns
- **Component-first**: Each component is self-contained with SPEC.md, LLD.md, schemas/, tests/
- **Preview-first**: All operations must have preview mode
- **Deterministic**: Same inputs produce same outputs
- **Fault isolation**: Components should not fail together