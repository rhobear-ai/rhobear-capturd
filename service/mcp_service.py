"""Run the Captur'd MCP harness as its own HTTP service.

Mounting FastMCP's streamable-HTTP app inside the existing FastAPI app requires its
lifespan to run in the parent's constructor; bolting it on afterwards leaves the session
manager's task group uninitialised and every call 500s. Running it as its own ASGI app
lets uvicorn manage the lifespan properly, and the main service just proxies to it.

Listens on 127.0.0.1:8100 — never exposed directly; auth happens in the Captur'd service.
"""
import sys, os

sys.path.insert(0, "/opt/sunsponge-capture")

from capturd.mcp.server import _build_server  # noqa: E402

server = _build_server()
app = server.http_app(path="/")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("CAPTURD_MCP_PORT", "8100")))
