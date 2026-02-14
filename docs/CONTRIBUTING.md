# Contributing to Bollard

Thank you for your interest in contributing to Bollard! We strictly follow a set of global rules to ensure code quality and consistency.

## Environment Setup

We use `uv` for dependency management and running the project.

1.  **Install `uv`**:

    ```bash
    pip install uv
    ```

2.  **Install Dependencies**:
    ```bash
    uv sync
    ```

## Development Workflow

### 1. Linting

We use `ruff` for formatting and linting, and `yina` for additional checks.

- **Format Code**:

  ```bash
  uv run ruff format .
  ```

- **Check for Errors**:

  ```bash
  uv run ruff check . --select E,W,F,I,C,B
  uv run yina lint .
  ```

  Ensure all linting errors are resolved before submitting your changes.

### 2. Testing

We use `pytest` for testing. **Note:** A running Docker or Podman engine is required to run the tests.

- **Run All Tests**:

  ```bash
  uv run pytest
  ```

- **Run Specific Test**:
  ```bash
  uv run pytest tests/test_containers.py
  ```

### 3. Type Checking

We use `mypy` for static type checking.

- **Run Type Checks**:
  ```bash
  uv run mypy .
  ```

## Coding Standards

- **Zero Dependencies**: Do not add external runtime dependencies. The library must rely only on the Python standard library.
- **Docstrings**: Use Google-style docstrings for all modules, classes, and functions.
- **Type Hints**: Use type hints for all function arguments and return values.
- **Logging**: Use `logging` instead of `print`.
- **Naming**: Use descriptive variable names (no single-letter variables unless common convention like `i` in loops).

## Documentation

- Update `docs/README.md` and `docs/ARCHITECTURE.md` if your changes affect the public API or internal architecture.
- Ensure all new features are documented with examples.
