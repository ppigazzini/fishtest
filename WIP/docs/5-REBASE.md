
# Rebase process (upstream/master → this repo)

> [!NOTE]
> **Canonical sources:**
> - Hotspot rules (protect glue/api.py, glue/views.py): [3.0-ITERATION-RULES.md](3.0-ITERATION-RULES.md)
> - Protocol contracts: [1-FASTAPI-REFACTOR.md](1-FASTAPI-REFACTOR.md)
> - Current architecture snapshot: [2-ARCHITECTURE.md](2-ARCHITECTURE.md)

Goal: rebase onto the latest upstream `master` while keeping our FastAPI work
mechanical and as close as possible to upstream behavior and to upstream code.

This repo convention:

- Treat `server/fishtest/api.py` and `server/fishtest/views.py` as the
	**upstream behavioral spec** (they get rebased from upstream).
- Treat `server/fishtest/http/api.py` and `server/fishtest/http/views.py` as
	the **mechanical port hotspots** (see [3.0-ITERATION-RULES.md](3.0-ITERATION-RULES.md) for hotspot rules).
- Prefer copy/paste of upstream logic into the http layer, with minimal FastAPI/Starlette
	adaptation (no refactors).

## 0) Pre-flight

1. Ensure working tree is clean:

	 - `git status`

2. Make sure the upstream remote exists and points to the upstream project:

	 - `git remote -v`
	 - If needed: `git remote add upstream <url>`

3. Optional safety:

	 - `git fetch --all --prune`
	 - Create a backup branch/tag before rebasing:
		 - `git branch backup/pre-rebase-$(date +%Y%m%d)`

4. Tooling note (recommended): use `uv run` for repeatable Python invocations.

	- Repo-wide scripts under `WIP/tools/` can be run from the repo root:
		- `uv run python WIP/tools/<script>.py`
	- Server-local tooling (ruff/ty) is typically run from `server/`:
		- `cd server && uv run ruff ...`
		- `cd server && uv run ty ...`

## 1) Fetch upstream and rebase

Recommended command-by-command flow (adjust branch names as needed):

	- Update upstream refs (with pruning):
		- `git fetch -p upstream`

	- Switch to your main FastAPI branch (example name: `fastapi`):
		- `git switch fastapi`

	- Create a fresh working branch for the rebase (example name: `fastapi-rebase`):
		- `git switch -c fastapi-rebase`

	- Rebase on top of upstream `master`:
		- `git rebase upstream/master`

	- If conflicts:
		- Resolve conflicts (prefer upstream changes in spec files).
		- Continue rebase:
			- `git add <files>`
			- `git rebase --continue`
		- If you need to abort:
			- `git rebase --abort`

	- Capture what changed compared to your pre-rebase FastAPI branch:
		- `git diff fastapi > diff_upstream.txt`

	- Use `diff_upstream.txt` as the rebase report:
		- Skim it to identify behavioral deltas in the spec files (`server/fishtest/api.py`, `server/fishtest/views.py`).
		- Apply the same deltas mechanically to the glue hotspots (`server/fishtest/glue/api.py`, `server/fishtest/glue/views.py`).
		- If the rebase introduced non-trivial behavior changes, keep `diff_upstream.txt` in the PR as supporting evidence for what changed upstream.

## 1.1) Rebase `fastapi_server_task_duration_180` (required)

After rebasing onto `upstream/master`, also rebase (or re-apply) the topic branch
`fastapi_server_task_duration_180` so these operational tweaks don’t get dropped:

- `server/fishtest/rundb.py`: `RunDb.task_duration` 1800 → 180
- `server/fishtest/glue/views.py`: force `sprt_batch_size_games = 8`

Typical flow (adjust branch names as needed):

	 - `git switch fastapi_server_task_duration_180`
	 - `git rebase <your-rebased-branch>`
	 - merge/cherry-pick it back into your main FastAPI branch

## 2) Post-rebase: identify what changed upstream

Upstream changes that usually matter for glue parity:

- Schemas/validation: `server/fishtest/schemas.py`
- UI templates: `server/fishtest/templates/*.mak`
- Spec logic: `server/fishtest/api.py`, `server/fishtest/views.py`
- Utility helpers used by spec/glue: `server/fishtest/util.py`

Practical approach:

1. Read the upstream diff (or the rebased commit list) and extract *behavioral
	 deltas*:

	 - New validation constraints?
	 - New/changed template variables?
	 - New error messages?
	 - New fields returned to workers/UI?

2. Decide whether the change affects:

	 - FastAPI HTTP layer API (`server/fishtest/http/api.py`)
	 - FastAPI HTTP layer UI (`server/fishtest/http/views.py`)
	 - Tests (server unit tests and FastAPI HTTP tests)
	 - WIP parity scripts

## 3) Update HTTP layer code (mechanical port only)

See [3.0-ITERATION-RULES.md](3.0-ITERATION-RULES.md) for hotspot rules and two-step landing strategy.

Rule: when upstream changes a behavior in `server/fishtest/api.py` or
`server/fishtest/views.py`, apply the same logic to the HTTP layer with the smallest
possible framework adaptations.

### 3.1 HTTP API parity

- File: `server/fishtest/http/api.py`
- Typical work:
	- Update request parsing / validation to match new schemas.
	- Preserve worker-style error shaping (`/api/...: ...` + `duration`).
	- Keep blocking work off the event loop (threadpool).

### 3.2 HTTP UI parity

- File: `server/fishtest/http/views.py`
- Typical work:
	- Mirror form validation logic and error messages.
	- Ensure every template variable added upstream is passed in the context.
	- Keep Jinja2 rendering off the event loop (threadpool).

## 4) Update tests

### 4.1 Spec/unit tests

- Many tests import the Pyramid-era spec modules as behavior reference.
- If upstream adds a new unit test, keep it and update any harness stubs if
	required.

### 4.2 FastAPI HTTP tests

- Files: `server/tests/test_http_api.py`, `server/tests/test_http_users.py`
- For each upstream behavior change, add/adjust a FastAPI test that asserts the
	same externally-visible behavior (status code, JSON shape, redirects, HTML).

## 5) Run parity checks

This repo keeps a few helper scripts under `WIP/tools/` used during the port.
After rebasing, run the relevant ones (route coverage / spot checks) to confirm
glue still matches the spec.

Recommended (from repo root):

	- `uv run python WIP/tools/parity_check_api_routes.py`
	- `uv run python WIP/tools/parity_check_views_routes.py`
	- `uv run python WIP/tools/parity_check_api_ast.py`
	- `uv run python WIP/tools/parity_check_views_ast.py`
	- `uv run python WIP/tools/parity_check_hotspots_similarity.py`
	- `server/.venv/bin/python WIP/tools/parity_check_urls_dict.py`
	- (optional inventory) `uv run python WIP/tools/parity_check_views_no_renderer.py`

Template parity (verify Jinja2 vs legacy Mako parity unchanged):

	- `server/.venv/bin/python WIP/tools/compare_template_parity.py`
	- `server/.venv/bin/python WIP/tools/template_context_coverage.py`

One-liner (stop on first failure):

	- `uv run python WIP/tools/parity_check_api_routes.py \
	  && uv run python WIP/tools/parity_check_views_routes.py \
	  && uv run python WIP/tools/parity_check_api_ast.py \
	  && uv run python WIP/tools/parity_check_views_ast.py \
	  && uv run python WIP/tools/parity_check_hotspots_similarity.py`

Guideline:

- If a parity script reports drift, fix the HTTP layer first (mechanical port), then
	adjust the script only if upstream behavior genuinely changed.

## 6) Validate locally

At minimum:

- Run server unit tests.
- Run FastAPI tests if FastAPI deps are installed (they skip cleanly otherwise).

Keep validation scoped:

- Only lint/type-check new glue support modules and new tests.
- Do not reformat the mechanical-port hotspots unless explicitly decided.

### 6.1 Scoped lint/type/format checks (recommended)

Prefer running these on *only the files you changed*.

Example (changed HTTP hotspot + parity scripts):

	- `cd server`
	- `uv run ruff check fishtest/http/views.py \
	  ../WIP/tools/parity_check_api_routes.py \
	  ../WIP/tools/parity_check_views_routes.py \
	  ../WIP/tools/parity_check_api_ast.py \
	  ../WIP/tools/parity_check_views_ast.py \
	  ../WIP/tools/parity_check_hotspots_similarity.py \
	  ../WIP/tools/parity_check_views_no_renderer.py`

	- `uv run ruff format --check fishtest/http/views.py \
	  ../WIP/tools/parity_check_api_routes.py \
	  ../WIP/tools/parity_check_views_routes.py \
	  ../WIP/tools/parity_check_api_ast.py \
	  ../WIP/tools/parity_check_views_ast.py \
	  ../WIP/tools/parity_check_hotspots_similarity.py \
	  ../WIP/tools/parity_check_views_no_renderer.py`

Or use the bundled lint scripts:

	- `bash WIP/tools/lint_http.sh`
	- `bash WIP/tools/lint_tools.sh`

## 7) Record the rebase outcome

1. If upstream changed behavior, write a short note (or attach a diff file) in
	 `WIP/docs/` describing:

	 - What upstream changed
	 - Which HTTP layer files were updated to match
	 - Which tests were added/updated

	Practical option:
	- Use `diff_upstream.txt` as the attachment/report for the PR/review.

2. Prefer one small "sync" commit for the HTTP layer/test parity updates.
