"""The MCP server: fishtest's read-only tools behind valgebra-planning's gate.

`build_server` returns a low-level MCP server that lists `TOOLS` and answers a
`tools/call` in four steps: the gate checks the arguments against the tool's
contract, the tool reads the run database in the threadpool, the gate checks
the result against the contract, and the result goes back as structured content
beside its JSON text. A refusal at either check, or a call that names a run the
database does not hold, is a tool error the client can read, so nothing a
contract refuses reaches the database or the client.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from starlette.concurrency import run_in_threadpool
from valgebra_planning.proxy import Gate

from fishtest.mcp_contracts import CONTRACTS, TOOLS
from fishtest.mcp_tools import TOOL_FUNCTIONS, ToolRefusal

logger = logging.getLogger(__name__)

INSTRUCTIONS = (
    "Read-only access to fishtest, the Stockfish testing framework: test runs,"
    " their results and SPRT verdicts, and the event log."
)

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
        arguments = dict(params.arguments or {})
        refused = gate.before(name, arguments)
        if refused is not None:
            return _tool_error(refused)
        try:
            result = await run_in_threadpool(TOOL_FUNCTIONS[name], rundb(), arguments)
        except ToolRefusal as e:
            return _tool_error(str(e))
        refused = gate.after(name, result)
        if refused is not None:
            # A result outside its contract is this server's fault; the log
            # keeps the value, the client gets the tool's name alone.
            logger.error("MCP tool result refused: %s", refused)
            return _tool_error(f"{name} could not answer within its contract")
        text = types.TextContent(type="text", text=json.dumps(result))
        return types.CallToolResult(content=[text], structured_content=result)

    return Server(
        "fishtest",
        title="Fishtest",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
