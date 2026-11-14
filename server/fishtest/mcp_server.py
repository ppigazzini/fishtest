"""The MCP server: fishtest's read-only tools behind valgebra-planning's gate.

`build_server` returns a low-level MCP server that lists `TOOLS` and answers a
`tools/call` in four steps: the gate checks the arguments against the tool's
contract, the tool reads the run database in the threadpool, the gate checks
the result against the contract, and the result goes back as structured content
beside its JSON text. A refusal at either check, or a call that names a run the
database does not hold, is a tool error the client can read, so nothing a
contract refuses reaches the database or the client. A call naming a tool the
server does not list is a JSON-RPC error, `-32602`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from mcp import types
from mcp.server import CacheHint
from mcp.server.lowlevel import Server
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from starlette.concurrency import run_in_threadpool
from starlette.routing import Route
from valgebra import ValidationError
from valgebra_planning.contracts import Contract
from valgebra_planning.proxy import Gate

from fishtest.api import ACTIONS_BODY_MAX
from fishtest.mcp_contracts import CONTRACTS, TOOLS, TOOLS_DIGEST
from fishtest.mcp_tools import TOOL_FUNCTIONS, ToolRefusal

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Read-only access to fishtest, the Stockfish testing framework: test runs,"
    " their results and SPRT verdicts, and the event log."
)

# The listing and the server's description change only with a deployment, and
# hold nothing private, so any client or shared cache may keep them an hour.
LISTING_CACHE = CacheHint(ttl_ms=60 * 60 * 1000, scope="public")

LISTING = [
    types.Tool(
        name=tool["name"],
        title=tool["title"],
        description=tool["description"],
        input_schema=tool["inputSchema"],
        output_schema=tool["outputSchema"],
        annotations=types.ToolAnnotations.model_validate(tool["annotations"]),
    )
    for tool in TOOLS
]


def _tool_error(message: str) -> types.CallToolResult:
    text = types.TextContent(type="text", text=message)
    return types.CallToolResult(content=[text], is_error=True)


def _refusal(name: str, contract: Contract, arguments: dict[str, Any]) -> str:
    # The contract's first failure, by path and expectation: what a client
    # changes to be served, without the value it sent, and read no further than
    # the first failure, as /api/actions reads a refused filter.
    try:
        contract.accepts.validate(arguments, fail_fast=True)
    except ValidationError as e:
        where = ".".join(map(str, e.path)) or "the arguments"
        return (
            f"{name} refuses its arguments: at {where}: expected {e.expected}"
            f" [{e.code}]"
        )
    return f"{name} refuses its arguments"


def build_server(rundb: Callable[[], Any]) -> Server[Any]:
    """Return the MCP server; `rundb` returns the run database a call reads."""
    # No policy and no flow rule: every tool reads public data, so the gate
    # holds each call to its contract alone.
    gate = Gate(CONTRACTS, [], [])

    async def list_tools(_ctx: Any, _params: Any) -> types.ListToolsResult:
        return types.ListToolsResult(tools=LISTING)

    async def call_tool(
        _ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        name = params.name
        if name not in CONTRACTS:
            # A protocol error, as the tools specification states: no tool
            # result names a tool this server does not list.
            message = f"Unknown tool: {name:.128}"
            raise MCPError(code=types.INVALID_PARAMS, message=message)
        arguments = dict(params.arguments or {})
        if gate.before(name, arguments) is not None:
            return _tool_error(_refusal(name, CONTRACTS[name], arguments))
        try:
            result = await run_in_threadpool(TOOL_FUNCTIONS[name], rundb(), arguments)
        except ToolRefusal as e:
            return _tool_error(str(e))
        refused = gate.after(name, result)
        if refused is None:
            # A JSON number reads as an infinity, so the contract admits one,
            # and JSON writes neither an infinity nor NaN.
            try:
                text = json.dumps(result, allow_nan=False)
            except ValueError:
                refused = f"{name} returned a number JSON cannot write"
        if refused is not None:
            # A result outside its contract is this server's fault; the log
            # keeps the value, the client gets the tool's name alone.
            logger.error("MCP tool result refused: %s", refused)
            return _tool_error(f"{name} could not answer within its contract")
        content = types.TextContent(type="text", text=text)
        return types.CallToolResult(content=[content], structured_content=result)

    return Server(
        "fishtest",
        version=TOOLS_DIGEST,
        title="Fishtest",
        instructions=INSTRUCTIONS,
        cache_hints={"tools/list": LISTING_CACHE, "server/discover": LISTING_CACHE},
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


# A local address on any port: what a development instance is reached at.
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]")


def transport_security(public_url: str | None) -> TransportSecuritySettings:
    """Return the hosts and origins the endpoint serves.

    A local host, and the host of `public_url` when one is set. A request
    naming another host is refused 421, which stops a page that rebinds its
    own name to a local address; a request from another origin is refused 403.
    A request without an `Origin`, which is every client but a browser, is
    served.
    """
    hosts = [host for local in LOCAL_HOSTS for host in (local, f"{local}:*")]
    origins = [f"http://{host}" for host in hosts]
    if public_url is not None:
        parts = urlsplit(public_url)
        hosts.append(parts.netloc)
        origins.append(f"{parts.scheme}://{parts.netloc}")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def streamable_http_route(
    server: Server[Any], path: str, public_url: str | None
) -> Route:
    """Return the route that serves `server` at `path` over Streamable HTTP.

    Stateless and answering in JSON, so any instance behind the proxy answers
    any request and no session lives in one process. Each request's `Host` and
    `Origin` are checked against `transport_security(public_url)`, and a body
    is held to the size `/api/actions` reads, the largest any tool needs; a
    larger one is answered 413. The route takes POST alone: a stateless server
    sends nothing a client did not ask for, so it opens no event stream for a
    GET and ends no session for a DELETE, and either is answered 405. The
    app's lifespan runs `server.session_manager`.
    """
    app = server.streamable_http_app(
        streamable_http_path=path,
        stateless_http=True,
        json_response=True,
        max_request_body_size=ACTIONS_BODY_MAX,
        transport_security=transport_security(public_url),
    )
    (route,) = app.routes
    if not isinstance(route, Route):
        msg = f"the SDK serves {path} through {type(route).__name__}, not a Route"
        raise TypeError(msg)
    return Route(path, endpoint=route.endpoint, methods=["POST"])
