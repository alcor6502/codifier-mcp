"""
web.py — the administration UI: the second server, in the same process.

WHY IT LIVES HERE AND NOT IN A CONTAINER OF ITS OWN. Two processes on the same
SQLite database do not share the engine's RLock, so a second container is a
closed alley — written down as one in `Decisioni aperte.md` and not reopened
here. One process, one asyncio loop, two `uvicorn.Server`: the MCP app from
`mcp.http_app()` on the MCP port, this one on WEB_PORT.

WHAT IT MAY TOUCH. **The contract towards the engine is the methods of
`Registry`, and nothing else: not one line of SQL lives in this file.** That is
the constraint that keeps the other road open — the UI as a second MCP client,
in a container of its own — because a layer that only ever calls the methods
the tools call can be moved behind them later without being rewritten. A single
`SELECT` in here would quietly close that road, and nothing would go red.

ZERO NEW DEPENDENCIES, and it is measured rather than hoped: the image already
carries starlette (via fastmcp[server]), uvicorn, python-multipart for the form
bodies, and the standard library covers the rest — `hashlib` and
`secrets.compare_digest` for the master, `hmac` and `time` for the session,
`html.escape` for every value that reaches a page. No template engine: the day
the pages are too many for templates written by hand, that day the reason gets
written down and one is added — not before.

Configuration, all through environment variables:
  WEB_PORT          the port this server listens on (default 9443). It must be
                    one the Funnel CANNOT publish and it must not collide with
                    the MCP port — the preflight refuses both at the edge
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------------
# Configuration, resolved HERE and read from here by everybody.
#
# The preflight validates these at the edge and the service reads them at
# boot, and the two must not be able to disagree — the same reason the CIDR
# filter and the log level are resolved in one expression in the engine. Which
# is also why nothing at the top of this module imports starlette: the
# preflight has to be able to run, and to report, on an image where the web
# stack is missing or broken, and an import at module level would turn that
# into a traceback instead of a red line with a name on it. `build()` does the
# import, and `build()` is only ever called by a process that is about to
# serve.
# ---------------------------------------------------------------------

DEFAULT_PORT = 9443

# The only three ports Tailscale Funnel can publish (docs, validated
# 2026-Jan-20, verified again 2026-Aug-10). The UI is unpublishable because
# the preflight REFUSES these three, not because a constant in the code put it
# out of reach: a constant would close the door on a second product on the
# same machine, and the guarantee did not have to be paid for with that.
FUNNEL_PORTS = (443, 8443, 10000)


def port_from_env() -> int:
    """The port this server listens on. Born optional with a working default
    in the code: Unraid does not propagate new variables to containers that
    are already installed."""
    raw = (os.environ.get("WEB_PORT") or "").strip()
    if not raw:
        return DEFAULT_PORT
    if not raw.isdigit() or not (1 <= int(raw) <= 65535):
        raise ValueError(f"WEB_PORT={raw!r}: a whole port number between 1 and 65535")
    return int(raw)


def build(*, registry, log):
    """The Starlette application. Handed the engine and the service's own
    logger: a web layer that reached for either of them itself would be a
    second place where the configuration is decided, and a second logger is
    how a refusal stops appearing in the log everybody reads."""
    from starlette.applications import Starlette

    # C4 grain 1 is the TRANSPORT: two servers, one loop, and the MCP surface
    # unmoved. The routes arrive in the grains that follow, and until they do
    # every path here answers 404 — a door that is not built is better than a
    # door that is open.
    return Starlette(routes=[])
