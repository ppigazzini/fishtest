# FastAPI references (for this project)

Date: 2026-02-11

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

### 8) Templates (FastAPI wrappers)
- FastAPI re-exports Starlette `Jinja2Templates` in `fastapi.templating`.
- `TemplateResponse` is called with keyword args: `request=`, `name=`, `context=`.
- `request` must be in the template context for `url_for` to work.

## Dependency injection internals (M11-critical)

### How `Depends()` works

**The `Depends` dataclass** (from `fastapi/params.py`):
```python
@dataclass(frozen=True)
class Depends:
    dependency: Optional[Callable[..., Any]] = None
    use_cache: bool = True
    scope: Union[Literal["function", "request"], None] = None
```

**Resolution pipeline**:
1. `get_dependant()` introspects endpoint signature via `inspect.Signature`
2. For each parameter: if `Annotated[Type, Depends(fn)]` or `= Depends(fn)` → recursively builds a dependency tree
3. `solve_dependencies()` walks the tree depth-first, resolving sub-deps before parents
4. **Caching**: per-request cache keyed by `(callable, scopes)`. Same dep used twice = called once. Set `use_cache=False` for fresh computation each time.
5. **Sync callables**: auto-run via `run_in_threadpool()` — no `async` needed.

**Magic injectables** (no `Depends` needed):
- `Request`, `WebSocket`, `HTTPConnection`, `Response`, `BackgroundTasks`, `SecurityScopes`
- Declaring `request: Request` in any function (endpoint or dependency) gets the live request injected.

### `Annotated` type aliases — the key pattern for M11

```python
# Define once in a deps/types module
from typing import Annotated
from fastapi import Depends

CurrentUser = Annotated[User, Depends(get_current_active_user)]
DBSession = Annotated[Session, Depends(get_session)]

# Use everywhere — clean signatures
@app.get("/items/")
async def read_items(user: CurrentUser, db: DBSession):
    ...
```

When `Depends(dependency=None)` → the type annotation itself becomes the callable:
`Annotated[SomeClass, Depends()]` uses `SomeClass` as both type and factory.

### Session access in dependencies

```python
from starlette.requests import Request

def get_session(request: Request) -> dict:
    return request.session  # dict from SessionMiddleware

def get_current_user(request: Request) -> Optional[User]:
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=401)
    return lookup_user(username)

CurrentUser = Annotated[User, Depends(get_current_user)]
```

### Guard-only dependencies (no return value)

```python
# On a single route
@app.post("/actions/", dependencies=[Depends(verify_csrf)])

# On all routes in the app
app = FastAPI(dependencies=[Depends(verify_token)])

# On a router group
admin = APIRouter(dependencies=[Depends(require_admin)])
```

### Generator dependencies for cleanup

Replace Pyramid's `request.add_finished_callback()`:
```python
def get_db():
    db = create_session()
    try:
        yield db     # injected into endpoint
    finally:
        db.close()   # runs after response
```

### Dependency overrides for testing

```python
app.dependency_overrides[get_db] = lambda: mock_db
```

### What FastAPI adds beyond Starlette

- **Request/Response**: Nothing — re-exports Starlette's classes unchanged.
- **Extra response classes**: `UJSONResponse`, `ORJSONResponse`.
- **Middleware**: All re-exported from Starlette. One FastAPI-original: `AsyncExitStackMiddleware` for generator dep cleanup.
- **`@app.middleware("http")`**: Syntactic sugar for `BaseHTTPMiddleware`.

### Key transformations for M11 (Pyramid → FastAPI)

| Pyramid Pattern | FastAPI Replacement |
|---|---|
| `self.request` in view class | `request: Request` parameter |
| `self.request.session["user"]` | `user: CurrentUser` via `Depends()` reading `request.session` |
| `self.request.rundb` (tween-attached) | `rundb: RunDbDep` via `Depends()` reading `request.state` |
| `self.request.route_url(...)` | `request.url_for(...)` (Starlette builtin) |
| `self.request.params` (GET+POST) | Explicit `Query()` / `Form()` / `Body()` params |
| `self.request.matchdict["id"]` | `id: str` path parameter |
| `self.request.json_body` | `data: dict = Body()` or Pydantic model |
| View class `__init__(self, request)` | Free function with typed params |
| Pyramid tweens | `app.add_middleware()` or `@app.middleware("http")` |
| `config.add_request_method(fn)` | `Depends(fn)` replaces the request method entirely |

## Quick "use this when…" cheatsheet
- Replace request shim plumbing: `Depends(...)` + `Annotated` → Dependencies.
- Side-effect checks without unused params: decorator deps → Dependencies in Decorators.
- Keep cross-cutting concerns centralized: `app.add_middleware(...)` → Middleware.
- Uploads + forms: `UploadFile` / `Form` → Request Files + Request Forms.
- Deterministic startup/shutdown: `lifespan` → Events.
- Reverse proxy correctness: `root_path` + proxy headers → Behind a Proxy.
- Endpoint-level tests: TestClient patterns → Testing.

## Lessons from the bloated draft (what to adopt vs avoid)

What worked (small, safe patterns):
- Keep `app.include_router(...)` ordering explicit and documented to preserve legacy behavior.
- Use `Annotated` aliases for shared dependencies to keep handler signatures consistent and testable.
- Keep a single internal shim boundary when legacy signatures must be preserved (avoid multiple layers of request wrappers).
- Feature‑flagged session migration plan (dual-read → dual-write → flip) to avoid mass session invalidation.

What to avoid:
- Splitting routes into many small modules when it harms readability (the draft increased hop count without clarity gains).
- Helper/pipeline/context layering that obscures the request flow; prefer linear handler code with only essential helpers.
- Middleware that hides business‑level control flow (session commit/forget, CSRF decisions are clearer when explicit at route/dependency level).
- Duplicative dependency helpers with slightly different names (centralize in one module to avoid drift).
- Wholesale copying large example projects; prefer small, reviewed cherry‑picks (dependencies, lifespan, router inclusion).
