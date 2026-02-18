# Development Commands

## Testing
- `pytest` - Run all tests
- `pytest tests/` - Run acceptance/contract tests
- `pytest components/` - Run component tests
- `pytest --cov=components --cov=shared` - Run with coverage

## Code Quality
- `ruff check` - Lint code
- `ruff format` - Format code
- `mypy` - Type checking

## Development Tools
- `uvicorn main:app --reload` - Run development server
- `python -m pytest` - Alternative test runner

## Git Commands (macOS)
- `git status` - Check working tree status
- `git checkout -b feat/<name>` - Create feature branch
- `git add .` - Stage changes
- `git commit -m "message"` - Commit changes
- `ls` - List files
- `find . -name "*.py"` - Find Python files
- `grep -r "pattern" .` - Search for patterns

## Project Structure Commands
- Component creation: Use `/specify` command
- LLD creation: Use `/design` command
- Full workflow: Use `/flow_orchestrate` command