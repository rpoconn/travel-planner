## Build & Development Commands
- Dependencies: Use `uv` (e.g., `uv add <package>`, `uv run python ...`)
- Lint & Format: `uv run ruff check --fix .` && `uv run ruff format .`
- Test: `uv run pytest`

## Python & Architecture Style Guidelines
- **Type Hints:** Use strict type hints (`mypy` compliant) for all function signatures, especially agent state and tool outputs.
- **Async/Await:** Use `asyncio` cleanly. Agentic loops often require asynchronous LLM streaming or concurrent tool execution; avoid blocking synchronous calls inside agent steps.
- **Pydantic Validation:** Always use Pydantic (v2) for structuring LLM inputs, outputs, and tool schemas. Never parse raw JSON strings manually when passing data to/from Claude.
- **Error & Retry Handling:** LLM and API calls must include explicit exception handling and retry logic (e.g., handling rate limits or malformed JSON outputs gracefully).
- **Logging:** Use Python's built-in `logging` module. Never use `print()` statements in application code.
- **Typescript:** Typescript is required. use strict mode.
- **API Generation:** Use @hey-api for UI SDK generation from an open-api spec.
- **Indents**: Use 4 spaces for indents



### Core Rules for Code Generation:
1. **Encapsulation:** If a Pydantic model, database model, or router belongs exclusively to a domain (e.g., chats or messages), it **must** live inside that feature's directory (`src/features/[feature-name]/`).
2. **Pydantic vs. DB Models:** 
   - Keep Pydantic validation/serialization schemas in `schemas.py`.
   - Keep ORM/database persistence models in `models.py`.
   - Never mix request/response validation schemas into the global scope.
3. **Cross-Feature Dependencies:**
   - Features should remain as independent as possible. 
   - If `messages` needs to verify a `chat_session` exists, it should import from `features.chat_sessions.service`, not query the chat database table directly.
4. **Shared Utilities:**
   - Database connection pools, environment configuration (`BaseSettings`), and global middleware belong in `src/shared/`.
5. **Type checking and formatting:**
   - All code should have type annotations, backend and frontend.
   - All projects should have liniting, codeformatting, and type checking enabled.
   - All backend apis should product an open api compatible spec.
   - the UI should convert the openapi spec into a type safe compatible service layer for all network calls.
6. **Testing:**
   - All code should have unit testing.
   - the project should have some end to end testing covering key workflows.
   - Follow testing pyramid guidelines.  
7. **Frequent Checkins**
   - when executing a multiple phase plan, make sure to checkin after every phase to show progress.
8. **Single Component Files**
   - UI compoennts should have a single file
9. **Comments**
   - Make sure to comment any unusual code that is not obvious
   - Make sure to orgnaize files into separate functional code sections. Example: state / setup / markup / event listeners, etc...
10. **Self-Contained UI Folders**
   - Each component and page gets its own folder (`components/Composer/`, `pages/ChatPage/`) holding its component, styles, and tests.
11. **Routing / Query / UI Toolkit**
   - Use the tanstack ecosystem for all UI needs.