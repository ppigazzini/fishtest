# FastAPI references (for this project)

Date: 2026-01-28

Curated **web-only** references and a short project-focused synthesis for the fishtest FastAPI refactor.

## Canonical web references (FastAPI)

- Bigger applications (APIRouter + include_router): https://fastapi.tiangolo.com/tutorial/bigger-applications/
- Dependencies overview: https://fastapi.tiangolo.com/tutorial/dependencies/
- Dependencies in path operation decorators: https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-in-path-operation-decorators/
- Handling errors + exception handlers: https://fastapi.tiangolo.com/tutorial/handling-errors/
- Middleware: https://fastapi.tiangolo.com/tutorial/middleware/
- Request forms: https://fastapi.tiangolo.com/tutorial/request-forms/
- Request files / UploadFile: https://fastapi.tiangolo.com/tutorial/request-files/
- Lifespan events: https://fastapi.tiangolo.com/advanced/events/
- Behind a proxy / root_path: https://fastapi.tiangolo.com/advanced/behind-a-proxy/
- Testing: https://fastapi.tiangolo.com/tutorial/testing/

## Synthetic report — what matters for this project

### 1) Router structure (lean app composition)
- Use `APIRouter` modules to keep UI vs API routes isolated.
- Prefer explicit `include_router(...)` in app assembly to keep the routing graph visible.

Snippet (Bigger Applications):
```python
from fastapi import FastAPI
from .routers import items, users

app = FastAPI()
app.include_router(users.router)
app.include_router(items.router)
```

### 2) Dependencies as the plumbing layer
- Use `Annotated` to define reusable dependency aliases for sessions, auth, CSRF, and DB handles.
- Use decorator dependencies for side-effect checks to keep handler signatures lean.

Snippet (Dependencies + decorator deps):
```python
from typing import Annotated
from fastapi import Depends

CommonDep = Annotated[dict, Depends(get_common)]

@router.post("/items", dependencies=[Depends(check_csrf)])
async def create_item(common: CommonDep):
    ...
```

### 3) Middleware usage (only when truly cross-cutting)
- Keep middleware minimal and order explicit.
- Prefer request-level dependencies for per-route checks (CSRF/auth) over global middleware.

Snippet (Middleware):
```python
from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"])
```

### 4) File/form handling (UI and worker uploads)
- Use `UploadFile` for large files and keep file I/O off the event loop.
- Prefer explicit limits in `request.form(...)` at the Starlette layer for DOS protection.

Snippet (Request files):
```python
from fastapi import File, UploadFile

@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    ...
```

### 5) Lifespan for startup/shutdown
- Use lifespan to manage DB clients, caches, and background schedulers.

Snippet (Lifespan):
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

app = FastAPI(lifespan=lifespan)
```

### 6) Error shaping for UI vs worker APIs
- Keep UI errors HTML and worker errors JSON with consistent error shapes.
- Prefer `HTTPException` + custom handlers instead of ad-hoc error responses.

### 7) Proxy awareness
- Ensure redirects and URL generation use correct scheme/host behind nginx.
- Use FastAPI’s `root_path` guidance when deploying behind a reverse proxy.

## Quick “use this when…” cheatsheet

- Split/organize routes: `APIRouter` + `include_router` → Bigger Applications.
- Replace request shim plumbing: `Depends(...)` + `Annotated` → Dependencies.
- Side-effect checks without unused params: decorator deps → Dependencies in Decorators.
- Keep cross-cutting concerns centralized: `app.add_middleware(...)` → Middleware.
- Uploads + forms: `UploadFile` / `Form` → Request Files + Request Forms.
- Deterministic startup/shutdown: `lifespan` → Events.
- Reverse proxy correctness: `root_path` + proxy headers → Behind a Proxy.
- Endpoint-level tests: TestClient patterns → Testing.
