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
the tools call can be moved behind them later without being rewritten. A query
in here would quietly close that road, and nothing at runtime would complain.

ZERO NEW DEPENDENCIES, and it is measured rather than hoped: the image already
carries starlette (via fastmcp[server]), uvicorn, python-multipart for the form
bodies, and the standard library covers the rest — `secrets.compare_digest` for
the master, `hmac` and `time` for the session, `html.escape` for every value
that reaches a page. No template engine: the day the pages are too many for
templates written by hand, that day the reason gets written down and one is
added — not before.

WHO GETS IN. One master, from the template. Not a username and a password:
there is one person. The hidden username field on the form is for the password
managers, which key an entry on a user and fill a password-only form wrong, in
silence.

Configuration, all through environment variables:
  WEB_PORT          the port this server listens on (default 9443). It must be
                    one the Funnel CANNOT publish and it must not collide with
                    the MCP port — the preflight refuses both at the edge
  WEB_MASTER_CODE   the master. Read by the preflight, which refuses a missing
                    one, a placeholder and anything under 12 characters; handed
                    to build() by server.py, never read from here
"""
from __future__ import annotations

import hmac
import html
import os
import secrets
import time

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

# One hour of INACTIVITY, sliding: every authenticated request re-issues the
# cookie. Not one hour of session — a page left open on the iPad while the
# batch is read is the normal case, and logging the person out mid-decision
# would teach them to keep a second tab logged in.
SESSION_MAX_IDLE = 3600

SESSION_COOKIE = "codifier_admin"


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


# =====================================================================
# The pages, written by hand
# =====================================================================

_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body { font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 0; padding: 1.5rem; max-width: 60rem; }
header { display: flex; align-items: baseline; gap: 1rem; flex-wrap: wrap;
         border-bottom: 1px solid #8884; padding-bottom: .6rem; margin-bottom: 1.2rem; }
header h1 { font-size: 1.15rem; margin: 0; }
header nav { margin-left: auto; display: flex; gap: .9rem; font-size: .9rem; }
h2 { font-size: 1rem; margin: 1.6rem 0 .6rem; }
a { color: inherit; }
form.inline { display: inline; }
label { display: block; margin: .8rem 0 .2rem; font-size: .85rem; opacity: .75; }
input[type=password], input[type=text], select {
  font: inherit; padding: .45rem .6rem; border: 1px solid #8886; border-radius: 6px;
  background: transparent; color: inherit; min-width: 18rem; max-width: 100%; }
button { font: inherit; padding: .45rem .9rem; border: 1px solid #8886;
         border-radius: 6px; background: #8881; color: inherit; cursor: pointer; }
table { border-collapse: collapse; width: 100%; font-size: .92rem; }
th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #8883;
         vertical-align: top; }
.note { font-size: .85rem; opacity: .7; }
.bad { border-left: 3px solid #c33; padding-left: .8rem; }
.ok { border-left: 3px solid #3a3; padding-left: .8rem; }
"""


def _esc(v) -> str:
    """Every value that reaches a page goes through here. There is no template
    engine — that was the decision — so this is the whole defence, and it is
    one function so that it can be looked for."""
    return html.escape("" if v is None else str(v), quote=True)


def _page(title: str, body: str, *, nav: str = "") -> str:
    return (f"<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>{_esc(title)} — codifier</title><style>{_CSS}</style></head><body>"
            f"<header><h1>{_esc(title)}</h1><nav>{nav}</nav></header>{body}</body></html>")


def _login_page(message: str = "") -> str:
    # The hidden, CONSTANT username field is not decoration. A form with the
    # password alone is the case where 1Password and the Apple keychain fill
    # the wrong thing, or nothing, without saying so: they key an entry on a
    # user. There is one user here, so the field is hidden and its value never
    # changes.
    warn = f"<p class='bad'>{_esc(message)}</p>" if message else ""
    return _page("Sign in", f"""{warn}
<form method="post" action="/login">
  <input type="text" name="username" value="codifier" autocomplete="username"
         hidden readonly>
  <label for="master">Master</label>
  <input id="master" type="password" name="master" autocomplete="current-password"
         required autofocus>
  <p><button type="submit">Sign in</button></p>
</form>
<p class="note">One hour of inactivity ends the session, and so does a restart
of the service.</p>""")


# =====================================================================
# The application
# =====================================================================

def build(*, registry, log, master: str):
    """The Starlette application. Handed the engine, the service's own logger
    and the master: a web layer that reached for any of them itself would be a
    second place where the configuration is decided, and a second logger is
    how a refusal stops appearing in the log everybody reads."""
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, RedirectResponse, Response
    from starlette.routing import Route

    # Generated AT BOOT, and nowhere else. Read from the environment it would
    # survive a restart, which is precisely the property this design does not
    # want: a restart invalidates every session, and the cost of that is
    # typing a password once.
    secret = secrets.token_bytes(32)

    def _sign(payload: str) -> str:
        return hmac.new(secret, payload.encode(), "sha256").hexdigest()

    def _issue(response, seen: int | None = None) -> None:
        payload = str(int(time.time() if seen is None else seen))
        response.set_cookie(SESSION_COOKIE, f"{payload}.{_sign(payload)}",
                            httponly=True, samesite="lax", path="/")

    def _session_ok(request) -> bool:
        """A cookie is a session if the signature holds AND the last activity
        it records is inside the hour. Both halves, in that order: an expired
        cookie whose signature is wrong is not a stale session, it is a forged
        one, and the two must not be told apart by how they fail."""
        raw = request.cookies.get(SESSION_COOKIE, "")
        payload, _, mac = raw.partition(".")
        if not payload or not mac:
            return False
        if not secrets.compare_digest(mac, _sign(payload)):
            return False
        try:
            seen = int(payload)
        except ValueError:
            return False
        return 0 <= time.time() - seen <= SESSION_MAX_IDLE

    def _client(request) -> str:
        return request.client.host if request.client else "unknown"

    def _guest(request):
        """Not signed in: the login page, and nothing about what is behind
        it."""
        return HTMLResponse(_login_page(), status_code=401)

    NAV = ("<a href='/'>projects</a>"
           "<form class='inline' method='post' action='/logout'>"
           "<button type='submit'>sign out</button></form>")

    # ---------- routes ----------

    async def home(request):
        if not _session_ok(request):
            return _guest(request)
        # The projects menu. `registry.projects()` is the door codes come out
        # of — the same door `rules_registry` is on the MCP side, and it wants
        # the maintenance code there for the same reason it wants the master
        # here. The code itself never leaves this process: the pages address a
        # project by its NAME, and the name is resolved back to the code on
        # every request.
        data = registry.projects()
        rows = "".join(
            f"<tr><td><a href='/p/{_esc(p['name'])}/'>{_esc(p['name'])}</a></td>"
            f"<td>{_esc(p['active_rules'])}</td>"
            f"<td class='note'>{_esc(p['description'])}</td></tr>"
            for p in data["projects"])
        body = (f"<table><thead><tr><th>Project</th><th>In force</th><th></th></tr>"
                f"</thead><tbody>{rows}</tbody></table>"
                if rows else "<p class='note'>No project in the registry yet.</p>")
        response = HTMLResponse(_page("Projects", body, nav=NAV))
        _issue(response)
        return response

    async def login(request):
        form = await request.form()
        given = (form.get("master") or "").strip()
        if not secrets.compare_digest(given, master):
            # ONE line, and it is a WARNING. Not a traceback: a page that
            # answers a wrong password with a stack trace teaches the person
            # nothing and the log everything. WARNING and not INFO — unlike a
            # wrong admin code, which can only come from one of Alfredo's own
            # chats, this one can come from anything on the LAN, which is the
            # actor the master exists for. It is also the level that survives
            # LOG_LEVEL=WARNING, which is the setting this line most needs to
            # survive.
            log.warning("refused web login: wrong master, from %s", _client(request))
            return HTMLResponse(_login_page("Wrong master."), status_code=401)
        response = RedirectResponse("/", status_code=303)
        _issue(response)
        return response

    async def logout(request):
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    routes = [
        Route("/", home, methods=["GET"]),
        Route("/login", login, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
    ]
    return Starlette(routes=routes)
