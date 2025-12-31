
# Typing in the Fishtest server

This repository uses modern Python typing for the code under `server/fishtest` and `server/tests`.
The goal is to improve readability and catch common mistakes without forcing a heavy refactor of
dynamic database documents and Pyramid request objects.

## Target Python and style

- Target runtime: **modern Python (3.14-style typing syntax)**.
- Use `from __future__ import annotations` in modules so annotations don’t require forward-declaration hacks.
- Prefer:
	- built-in generics: `list[str]`, `dict[str, object]`, `set[str]`
	- PEP 604 unions: `A | B` (instead of `Optional[A]`)
	- `type[...]` for `type` objects: `type[HTTPException]`
- Prefer `collections.abc` (`Mapping`, `Iterable`, `Iterator`, `Callable`, …) over importing from `typing`.
- Avoid importing from `typing` unless it is genuinely required.

## Design principles

- **Runtime behavior must not change.** Typing is a “thin overlay”.
- **Keep document types loose.** MongoDB documents are heterogeneous and evolve over time.
	We generally represent them as `dict[str, object]`/`Mapping[str, object]` rather than trying to encode
	the entire schema into static types.
- **Be strict where it’s cheap.** Pure utility functions (math/stats/string helpers) are typed more precisely.

## Shared aliases

Common aliases live in [server/fishtest/_types.py](server/fishtest/_types.py).

Key ideas:

- DB-ish documents are represented as simple dictionaries:
	- `type DbDoc = dict[str, object]`
	- `type RunDoc = DbDoc`
	- `type TaskDoc = DbDoc`
	- `type WorkerInfo = DbDoc`
- JSON-ish payloads use similar aliases (`JsonDict`, `JsonList`), but are intentionally still `object`-based
	because runtime payloads may include BSON/datetime values.

These aliases intentionally keep the type surface small and avoid pulling in many `typing` constructs.

## Common patterns in server code

### Pyramid request objects

Pyramid’s `request` object is heavily dynamic and application-specific (custom attributes like
`request.rundb`, `request.userdb`, etc.). Instead of a complex Protocol, most handlers take:

- `request: object`

Inside handlers we treat request attributes as dynamic runtime properties.

### MongoDB / PyMongo

- Cursor-returning APIs are typed as `object` when the exact cursor type would force heavy imports.
- When iterating documents, we use `Mapping[str, object]` where possible.

### “All functions typed” convention

Across `server/fishtest` and `server/tests`, all `def` / `async def` functions are expected to have:

- typed parameters (excluding `self`/`cls`), and
- an explicit return annotation (`-> None` when appropriate).

This includes nested helper functions used as closures.

### Mutable default arguments

Avoid mutable defaults like `{}`/`[]`/`set()` in signatures.

Use:

- `param: dict[str, object] | None = None` and then `param = {} if param is None else param`

This is both a correctness improvement and makes typing cleaner.

## Stats modules

The statistics helpers under `server/fishtest/stats/` are typed more precisely because they are mostly
pure functions. Common patterns:

- PDFs are represented as `Sequence[tuple[float, float]]`.
- Scores and Elo-related parameters use `float`.
- Return types are numeric tuples or `float`.

## Tests

`server/tests` follows the same conventions:

- `from __future__ import annotations`
- `unittest.TestCase` methods have `-> None`.
- Helpers return concrete types where practical (e.g., a request stub or `str` run id).

Note: in some environments `pytest` may not be installed; a quick sanity check is always possible via:

- `python -m compileall -q server/fishtest server/tests`

## When to choose `object` vs something more specific

Use `object` when:

- the value is framework-provided and highly dynamic (Pyramid request), or
- a precise type would require heavy dependencies or complex generic types.

Prefer a more specific type when:

- the function is pure and stable, or
- the structure is well-defined (e.g., `Sequence[int]`, `dict[str, bool]`, `tuple[float, float]`).

## Quick checklist for new code

- Add `from __future__ import annotations`.
- Use built-in generics and `collections.abc`.
- Add `-> None` to void functions.
- Avoid mutable defaults.
- For MongoDB docs: start with `Mapping[str, object]` / `dict[str, object]` unless there is a strong reason	to be stricter.
