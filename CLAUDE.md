## Build & Development Commands
- Dependencies: Use `uv` (e.g., `uv add <package>`, `uv run python ...`)
- Lint & Format: `uv run ruff check --fix .` && `uv run ruff format .`
- Type Check: `uv run mypy src tests scripts migrations`
- Test: `uv run pytest`

## Python & Architecture Style Guidelines
- **Type Hints:** Use strict type hints (`mypy` compliant) for all function signatures, especially domain models and API/LLM outputs.
- **Async/Await:** Use `asyncio` cleanly. Avoid blocking synchronous calls (database, network, LLM) inside async code paths, and run independent I/O calls concurrently where it helps.
- **Pydantic Validation:** Always use Pydantic (v2) for request/response bodies, external API payloads, and LLM inputs and outputs. Never parse raw JSON strings manually when passing data to/from the LLM.
- **Error & Retry Handling:** LLM and API calls must include explicit exception handling and retry logic (e.g., handling rate limits or malformed JSON outputs gracefully).
- **Logging:** Use Python's built-in `logging` module. Never use `print()` statements in application code.
- **Indents:** Use 4 spaces for indents (Python and TypeScript).

## TypeScript Style Guidelines
- **TypeScript:** TypeScript is required. Use strict mode.
- **API Generation:** Use @hey-api for UI SDK generation from an OpenAPI spec.

### Core Rules for Code Generation:
1. **Encapsulation:** If a Pydantic model, database model, or router belongs exclusively to one feature, it **must** live inside that feature's directory (`[..]/features/[feature-name]/`).
2. **Pydantic vs. DB Models:**
    - Keep Pydantic validation/serialization schemas in `schemas.py`.
    - Keep ORM/database persistence models in `models.py`.
    - Never mix request/response validation schemas into the global scope.
3. **Cross-Feature Dependencies:**
    - Features should remain as independent as possible.
    - If one feature needs data owned by another, it should go through the owning feature's service (`features.[other-feature].service`), not query that feature's database tables directly.
4. **Shared Utilities:**
    - Database connection pools, environment configuration (`BaseSettings`), and global middleware belong in `[..]/shared/`.
5. **Type Checking and Formatting:**
    - All code should have type annotations, backend and frontend.
    - All projects should have linting, code formatting, and type checking enabled.
    - The linters and type checkers must ban `Any` (Python: ruff `ANN401` plus mypy `disallow_any_explicit`; TypeScript: `@typescript-eslint/no-explicit-any` and the `no-unsafe-*` rules). Model JSON-shaped data with real types (`pydantic.JsonValue`, `unknown` plus narrowing) instead. Where an untyped third-party boundary truly needs `Any`, use a line-level ignore with a comment explaining why.
    - All backend APIs should produce an OpenAPI-compatible spec.
    - The UI should convert the OpenAPI spec into a type-safe service layer for all network calls.
6. **Testing:**
    - Follow the testing pyramid: many fast unit tests, fewer integration tests, and a small set of end-to-end tests.
    - The project should have end-to-end tests covering key workflows.
7. **Frequent Check-ins**
    - When executing a multi-phase plan, make sure to check in after every phase to show progress.
8. **One Component per File**
    - Each file defines exactly one UI component. Sub-components go in their own files (and folders, per rule 10).
9. **Comments**
    - Comment any unusual code whose purpose is not obvious.
    - Organize files into separate, labeled functional code sections. UI components use the section order defined in rule 12.
    Example:
    // ------------ Render ------------
10. **Self-Contained UI Folders**
    - Each component and page gets its own folder (`components/MyComponent/`, `pages/MyPage/`) holding its component, its tests, its service (rule 13), and a stylesheet only if one is needed (rule 11).
11. **UI Libraries & Styling**
    - Use the TanStack ecosystem wherever possible (e.g., TanStack Router for routing, TanStack Query for data fetching).
    - Use Mantine for UI components.
    - Style with Tailwind CSS utility classes. Only write a `.css` file when Tailwind can't style something correctly.
12. **UI Component Structure**
    - Every UI component file follows this section order:
        1. **Props:** an interface defining all input properties, at the top.
        2. **Initialization:** hooks, queries, router/context access.
        3. **State**
        4. **UI Callbacks:** event handlers.
        5. **Render:** all markup in the component's single return statement, at the bottom.
    - Don't write helper functions that return JSX (e.g., `renderHeader()`). If a piece of markup is big enough to pull out, make it its own component in its own folder.
13. **UI Services**
    - Pure logic functions used by a UI component go in a service module next to it, exported as plain functions (not a class).
    - Example: `MyComponent.tsx` has a `MyComponentService.ts` holding its pure logic functions. Service modules must have unit tests.
14. **Do Not Repeat Yourself**
    - Follow the DRY principle to avoid duplicating logic or concepts in code.
15. **Make Use of Libraries**
    - Complex operations may call for a library. If a concept is complex, check whether a library already exists before building something custom. Off-the-shelf solutions can be cleaner than building everything custom.
