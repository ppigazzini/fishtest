"""The MCP endpoint: fishtest's read-only tools, served at `MCP_PATH`.

`contracts` states each tool as `tools/list` publishes it, `tools` reads what
each tool returns, `server` answers the protocol, each call held to its
contract, `refusals` words what a contract refuses, and `transport` serves the
server over HTTP. The package imports nothing, so an instance with `MCP_PATH`
unset, which never imports `server`, loads no MCP code.
"""
