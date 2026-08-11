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
  WEB_ACTION_CAP    how many proposals may be approved in ONE action (default
                    5). The mechanical form of "at the twelfth in a row a
                    person signs without reading"
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

# How many proposals may enter in ONE action. It is the mechanical form of "at
# the twelfth signature in a row a person signs without reading", and it is a
# knob rather than a constant for the reason every knob here is one: a ceiling
# that is not in the template does not exist.
#
# The default is FIVE, which is the pending queue's own default, and the
# consequence is worth stating rather than discovering: out of the box this
# ceiling never refuses, because PENDING_CAP has already refused the sixth
# proposal at the door. It starts to bite the day the queue is widened — which
# is exactly the day somebody would otherwise approve eleven things in one
# gesture — and the preflight refuses a value it cannot mean.
DEFAULT_ACTION_CAP = 5


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


def action_cap_from_env() -> int:
    """The per-action ceiling. Born optional with a working default in the
    code, like the port and like PENDING_CAP: Unraid does not propagate new
    variables to containers that are already installed."""
    raw = (os.environ.get("WEB_ACTION_CAP") or "").strip()
    if not raw:
        return DEFAULT_ACTION_CAP
    if not raw.isdigit() or int(raw) < 1:
        raise ValueError(f"WEB_ACTION_CAP={raw!r}: a positive whole number of proposals")
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

def build(*, registry, log, master: str, action_cap: int, refusal,
          backup_dir: str = ""):
    """The Starlette application. Handed the engine, the service's own logger,
    the master and the ceiling: a web layer that reached for any of them itself
    would be a second place where the configuration is decided, and a second
    logger is how a refusal stops appearing in the log everybody reads.

    `refusal` is the engine's designed-refusal class, handed in for the same
    reason `make_tool` is handed it on the MCP side: which exception is a
    refusal and which is a genuine fault is the one thing neither the engine
    nor this file can know on its own. A refusal becomes a sentence on the
    page; a fault is left to rise, with its traceback, at ERROR."""
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

    NAV = ("<a href='/'>projects</a><a href='/admin'>deployment</a>"
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

    # ---------- a project ----------

    def _code_of(name: str) -> str | None:
        """Name to code, resolved on EVERY request. The code is the door and it
        stays in this process: it is never put in a URL, in a cookie or in a
        page, so a screenshot of the browser and a link sent to somebody are
        both harmless."""
        for p in registry.projects()["projects"]:
            if p["name"] == name:
                return p["code"]
        return None

    def _no_project(name: str):
        return HTMLResponse(_page("Not found",
                                  f"<p class='bad'>No project called "
                                  f"{_esc(name)}.</p>", nav=NAV), status_code=404)

    def _project_nav(name: str) -> str:
        n = _esc(name)
        return (f"<a href='/p/{n}/batch'>lot</a><a href='/p/{n}/rules'>rules</a>"
                f"<a href='/p/{n}/pending'>pending</a>"
                f"<a href='/p/{n}/status'>state</a><a href='/'>projects</a>"
                "<form class='inline' method='post' action='/logout'>"
                "<button type='submit'>sign out</button></form>")

    def _consumer_picker(name: str, code: str, chosen: str, where: str) -> str:
        """The consumer is a MENU and not a text field, and the list comes from
        the registry. Typed by hand it would be one more place a name can be
        spelt wrong, and the engine's refusal for an unknown consumer is not
        the answer to a typo — it is the answer to asking about somebody who
        does not exist."""
        names = [c["name"] for c in registry.project_info(code)["consumers"]]
        opts = "".join(
            f"<option value='{_esc(n)}'{' selected' if n == chosen else ''}>"
            f"{_esc(n)}</option>" for n in names)
        return (f"<form method='get' action='/p/{_esc(name)}/{where}'>"
                f"<label for='consumer'>Consumer</label>"
                f"<select id='consumer' name='consumer'>{opts}</select> "
                f"<button type='submit'>show</button></form>")

    def _consumers(code: str) -> list[str]:
        return [c["name"] for c in registry.project_info(code)["consumers"]]

    async def project_home(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        code = _code_of(name)
        if code is None:
            return _no_project(name)
        info = registry.project_info(code)
        waiting = len(registry.pending(code)["waiting"])
        body = (f"<p><a href='/p/{_esc(name)}/batch'>The lot</a> — "
                f"{waiting} proposal{'' if waiting == 1 else 's'} waiting.</p>"
                f"<h2>Consumers</h2><p>"
                + " · ".join(_esc(c["name"]) for c in info["consumers"])
                + f"</p><h2>Domains</h2><p class='note'>"
                + " · ".join(f"{_esc(d)} {_esc(t)}" for d, t in info["domains"].items())
                + "</p>")
        response = HTMLResponse(_page(name, body, nav=_project_nav(name)))
        _issue(response)
        return response

    # ---------- the lot ----------

    def _proposal_html(d: dict) -> str:
        sup = d.get("supersedes")
        # BOTH HALVES of the move, where the decision is taken. The engine
        # hands the field back EXPANDED — the victim's ID with its current
        # title, and a mark when the victim is no longer in force — so what is
        # read here is what is being retired and not an ID to go and look up.
        sup_html = (f"<p class='bad'>Approving this also RETIRES "
                    f"{_esc(sup)} — one transaction, no window in which both "
                    f"are in force.</p>") if sup else ""
        return (f"<article><label><input type='checkbox' name='approve' "
                f"value='{_esc(d['id'])}'> <b>{_esc(d['id'])}</b> · "
                f"{_esc(d['title'])} <span class='note'>{_esc(d['type'])} · "
                f"{_esc(d['permanence'])} · v{_esc(d['version'])} · "
                f"proposed by {_esc(d['source'])}</span></label>"
                f"<p class='note'>why: {_esc(d.get('reason'))}</p>"
                f"{sup_html}"
                f"<pre>{_esc(d['body'])}</pre>"
                f"<p class='note'>perimeter: "
                f"{_esc(', '.join(d['scopes']) or 'none')}</p></article>")

    def _lot_page(name: str, code: str, *, message: str = "", good: str = "",
                  status: int = 200):
        current = registry.batch(code)
        head = (f"<p class='bad'>{_esc(message)}</p>" if message else "") + \
               (f"<p class='ok'>{_esc(good)}</p>" if good else "")
        if not current["ids"]:
            return HTMLResponse(_page(f"{name} — the lot",
                                      head + "<p class='note'>Nothing is waiting.</p>",
                                      nav=_project_nav(name)), status_code=status)
        # The WHOLE pending batch, side by side. That is where three proposals
        # saying the same thing become visible as what they are, and it is why
        # ticking does not break the lot: it completes it.
        blocks = "".join(_proposal_html(d) for d in current["proposals"])
        body = (f"{head}<form method='post' action='/p/{_esc(name)}/batch'>"
                f"<input type='hidden' name='digest' value='{_esc(current['digest'])}'>"
                f"{blocks}"
                f"<p class='note'>The digest covers what you are LOOKING AT — all "
                f"{current['count']} of them — not what you tick. If a proposal "
                f"arrives while you read, this comes back refused and you read "
                f"again.</p>"
                f"<label for='reason'>Reason for the ones you leave unticked "
                f"(they are denied)</label>"
                f"<input id='reason' type='text' name='reason'>"
                f"<label for='master'>Master — once for this action, never once "
                f"per rule</label>"
                f"<input id='master' type='password' name='master' "
                f"autocomplete='current-password' required>"
                f"<p><button type='submit'>Approve the ticked, deny the rest</button></p>"
                f"</form>")
        return HTMLResponse(_page(f"{name} — the lot", body, nav=_project_nav(name)),
                            status_code=status)

    async def batch_page(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        code = _code_of(name)
        if code is None:
            return _no_project(name)
        response = _lot_page(name, code)
        _issue(response)
        return response

    async def batch_action(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        code = _code_of(name)
        if code is None:
            return _no_project(name)
        form = await request.form()
        # The master, ONCE for the action and never once per rule: four rules
        # are not four passwords, and a password typed four times is typed
        # without looking — which is the very defect the lot was invented to
        # avoid.
        if not secrets.compare_digest((form.get("master") or "").strip(), master):
            log.warning("refused web approval: wrong master, from %s", _client(request))
            return _lot_page(name, code, status=401,
                             message="Wrong master. Nothing was changed.")
        seen = (form.get("digest") or "").strip()
        ticked = [i.strip() for i in form.getlist("approve") if i.strip()]
        reason = (form.get("reason") or "").strip()
        current = registry.batch(code)
        # THE SAME CONTRACT AS rules_approve, and checked BEFORE anything is
        # written: what came back must be the batch that was read. Checked
        # here rather than left to approve() because the denials happen first,
        # and denying on a stale reading would refuse proposals nobody saw.
        if seen != current["digest"]:
            log.info("refused web approval: stale digest, from %s", _client(request))
            return _lot_page(name, code, status=409, message=(
                "The batch changed after you read it — something was proposed, "
                "approved or denied meanwhile. Nothing was changed. This is the "
                "page as it is now: read it again."))
        unknown = [i for i in ticked if i not in current["ids"]]
        if unknown:
            return _lot_page(name, code, status=400, message=(
                f"Not in this batch: {', '.join(unknown)}. Nothing was changed."))
        if len(ticked) > action_cap:
            return _lot_page(name, code, status=400, message=(
                f"{len(ticked)} ticked and the ceiling for one action is "
                f"{action_cap}. Nothing was changed: do it in more than one "
                f"pass, which is the point of the ceiling."))
        rest = [i for i in current["ids"] if i not in ticked]
        if not ticked and not rest:
            return _lot_page(name, code, status=400,
                             message="Nothing to do.")
        try:
            # DENY FIRST. approve() takes no list — it approves the whole
            # pending batch — so the unticked have to leave the queue before
            # it is called. The other order would let in exactly the ones the
            # gesture meant to keep out.
            denied = registry.deny(code, rest, reason)["denied"] if rest else []
            verdict = None
            if ticked:
                again = registry.batch(code)
                if again["ids"] != sorted(ticked):
                    # Somebody proposed in the moment between the denials and
                    # the approval. Nothing is approved: what enters must be
                    # what was read, and this is the one place where saying so
                    # costs a half-done action rather than a wrong one.
                    log.info("web approval stopped after the denials: the batch "
                             "moved again, from %s", _client(request))
                    return _lot_page(name, code, status=409, message=(
                        f"Denied {', '.join(denied) or 'nothing'}, and then a "
                        f"proposal arrived: NOTHING was approved, because what "
                        f"enters has to be what you read. Here is the batch now."))
                verdict = registry.approve(code, again["digest"])
        except refusal as e:
            # A designed refusal of the engine becomes a sentence on the page.
            # It is the same conversion the MCP side does in make_tool, for the
            # same reason: without it a wrong reason or an empty batch arrives
            # as a 500, which teaches the person nothing and the log a
            # traceback that is not a fault.
            log.info("refused web approval: %s", e)
            return _lot_page(name, code, status=400, message=str(e))
        return HTMLResponse(_page(f"{name} — done",
                                  _verdict_html(name, verdict, denied, reason),
                                  nav=_project_nav(name)))

    def _verdict_html(name: str, verdict, denied, reason: str) -> str:
        out = []
        if verdict:
            out.append(f"<p class='ok'>In force: <b>"
                       f"{_esc(', '.join(verdict['approved']))}</b> — provisional, "
                       f"until {_esc(verdict['expires_at'])}. Staying costs a "
                       f"decision; going is free.</p>")
            for s in verdict["superseded"]:
                out.append(f"<p class='ok'>Retired {_esc(s['retired'])}, pointing at "
                           f"{_esc(s['by'])} — the same transaction.</p>")
            for s in verdict["supersede_skipped"]:
                out.append(f"<p class='bad'>{_esc(s['id'])} was to retire "
                           f"{_esc(s['target'])}, and did not: {_esc(s['why'])}. "
                           f"Somebody else had already retired it, and that is "
                           f"not rewritten behind their back.</p>")
        if denied:
            out.append(f"<p>Denied: <b>{_esc(', '.join(denied))}</b> — "
                       f"{_esc(reason)}. The refusal and its reason are on the "
                       f"noticeboard of whoever filed them: silence became an "
                       f"answer.</p>")
        out.append(f"<p><a href='/p/{_esc(name)}/batch'>Back to the lot</a></p>")
        return "".join(out)

    # ---------- the master operations: the deployment page ----------
    #
    # Create, the registry index (codes included), rekey and backup — the
    # operations that left the MCP surface in v3.0.0 so that no master-level
    # secret would ever travel in a conversation. Same contract as the lot:
    # a session to see the page, the master RETYPED once per action to act.

    def _receipt_html(name: str, code: str, key: str) -> str:
        """TWO secrets, TWO destinations, shown ONCE — and the destinations
        are written on the page, not left to prose elsewhere: sending a
        secret to the wrong home is the mistake you make once and pay for
        twice."""
        return (f"<p class='ok'>Project <b>{_esc(name)}</b>.</p>"
                f"<table><tbody>"
                f"<tr><td>project code</td><td><code>{_esc(code)}</code></td>"
                f"<td class='note'>goes at the TOP of the project's "
                f"instructions</td></tr>"
                f"<tr><td>architect key</td><td><code>{_esc(key)}</code></td>"
                f"<td class='note'>goes in the PASSWORD MANAGER — it never "
                f"enters a conversation</td></tr>"
                f"</tbody></table>"
                f"<p class='bad'>Shown ONCE: neither can be read back. Leaving "
                f"this page without copying BOTH costs another rekey.</p>"
                f"<p><a href='/admin'>Back to the deployment page</a></p>")

    async def admin_page(request):
        if not _session_ok(request):
            return _guest(request)
        data = registry.projects()
        rows = "".join(
            f"<tr><td>{_esc(p['name'])}</td><td><code>{_esc(p['code'])}</code></td>"
            f"<td>{_esc(p['active_rules'])}</td>"
            f"<td class='note'>{_esc(p['created'])}</td></tr>"
            for p in data["projects"])
        opts = "".join(f"<option value='{_esc(p['name'])}'>{_esc(p['name'])}</option>"
                       for p in data["projects"])
        body = (
            "<h2>The registry — codes included</h2>"
            + (f"<table><thead><tr><th>Project</th><th>Code</th><th>In force</th>"
               f"<th>Created</th></tr></thead><tbody>{rows}</tbody></table>"
               if rows else "<p class='note'>No project yet.</p>")
            + "<p class='note'>The architect keys are NOT here: they exist as "
              "hashes. Recovering one is a rekey, which regenerates the pair.</p>"
            + "<h2>Create a project</h2>"
              "<form method='post' action='/admin/create'>"
              "<label for='cname'>Name</label>"
              "<input id='cname' type='text' name='name' required>"
              "<label for='cdesc'>Description (optional)</label>"
              "<input id='cdesc' type='text' name='description'>"
              "<label for='cmaster'>Master — once for this action</label>"
              "<input id='cmaster' type='password' name='master' "
              "autocomplete='current-password' required>"
              "<p><button type='submit'>Create — the receipt shows the two "
              "secrets once</button></p></form>"
              "<p class='note'>Born empty: domains, consumers and rules are the "
              "Architect's seeding, done with the key on the receipt.</p>"
            + ("<h2>Rekey</h2>"
               "<form method='post' action='/admin/rekey'>"
               f"<label for='rproj'>Project</label>"
               f"<select id='rproj' name='project'>{opts}</select>"
               "<label for='rmaster'>Master — once for this action</label>"
               "<input id='rmaster' type='password' name='master' "
               "autocomplete='current-password' required>"
               "<p><button type='submit'>Regenerate the pair</button></p></form>"
               "<p class='note'>Code AND key, always together: the old pair "
               "dies the moment you click.</p>" if rows else "")
            + "<h2>Backup</h2>"
              "<form method='post' action='/admin/backup'>"
              "<label for='bmaster'>Master — once for this action</label>"
              "<input id='bmaster' type='password' name='master' "
              "autocomplete='current-password' required>"
              "<p><button type='submit'>VACUUM INTO a quiescent copy</button></p>"
              "</form>")
        response = HTMLResponse(_page("Deployment", body, nav=NAV))
        _issue(response)
        return response

    async def admin_create(request):
        if not _session_ok(request):
            return _guest(request)
        form = await request.form()
        # The master, RETYPED for the action, compared in constant time —
        # inline, not delegated: the guard is pinned as written, and a
        # session alone is a browser left open on the iPad.
        if not secrets.compare_digest((form.get("master") or "").strip(), master):
            log.warning("refused web create: wrong master, from %s",
                        _client(request))
            return HTMLResponse(
                _page("Deployment", "<p class='bad'>Wrong master. Nothing was "
                                    "changed.</p>", nav=NAV), status_code=401)
        try:
            out = registry.create_project((form.get("name") or "").strip(),
                                          description=(form.get("description")
                                                       or "").strip())
        except refusal as e:
            log.info("refused web create: %s", e)
            return HTMLResponse(_page("Deployment",
                                      f"<p class='bad'>{_esc(e)}</p>", nav=NAV),
                                status_code=400)
        return HTMLResponse(_page("Deployment — receipt",
                                  _receipt_html(out["created"], out["code"],
                                                out["architect_key"]), nav=NAV))

    async def admin_rekey(request):
        if not _session_ok(request):
            return _guest(request)
        form = await request.form()
        # The master, RETYPED for the action, compared in constant time —
        # inline, not delegated: the guard is pinned as written, and a
        # session alone is a browser left open on the iPad.
        if not secrets.compare_digest((form.get("master") or "").strip(), master):
            log.warning("refused web rekey: wrong master, from %s",
                        _client(request))
            return HTMLResponse(
                _page("Deployment", "<p class='bad'>Wrong master. Nothing was "
                                    "changed.</p>", nav=NAV), status_code=401)
        name = (form.get("project") or "").strip()
        code = _code_of(name)
        if code is None:
            return _no_project(name)
        try:
            out = registry.rekey_project(code)
        except refusal as e:
            log.info("refused web rekey: %s", e)
            return HTMLResponse(_page("Deployment",
                                      f"<p class='bad'>{_esc(e)}</p>", nav=NAV),
                                status_code=400)
        return HTMLResponse(_page("Deployment — receipt",
                                  _receipt_html(out["project"], out["code"],
                                                out["architect_key"]), nav=NAV))

    async def admin_backup(request):
        if not _session_ok(request):
            return _guest(request)
        form = await request.form()
        # The master, RETYPED for the action, compared in constant time —
        # inline, not delegated: the guard is pinned as written, and a
        # session alone is a browser left open on the iPad.
        if not secrets.compare_digest((form.get("master") or "").strip(), master):
            log.warning("refused web backup: wrong master, from %s",
                        _client(request))
            return HTMLResponse(
                _page("Deployment", "<p class='bad'>Wrong master. Nothing was "
                                    "changed.</p>", nav=NAV), status_code=401)
        try:
            out = registry.backup(backup_dir)
        except refusal as e:
            log.info("refused web backup: %s", e)
            return HTMLResponse(_page("Deployment",
                                      f"<p class='bad'>{_esc(e)}</p>", nav=NAV),
                                status_code=400)
        body = (f"<p class='ok'>Quiescent copy written: "
                f"<code>{_esc(out['backup'])}</code> — {_esc(out['bytes'])} "
                f"bytes. It opens without recovery, and it is the one to take "
                f"off-site.</p><p><a href='/admin'>Back</a></p>")
        return HTMLResponse(_page("Deployment", body, nav=NAV))

    # ---------- the consultation: it reads, and only reads ----------

    def _read_page(request, render):
        """The shape every read page shares: a session, a project, and the
        code resolved from the name. Written once so that adding a page cannot
        add a page that forgot the session."""
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        code = _code_of(name)
        if code is None:
            return _no_project(name)
        try:
            body, title = render(name, code)
        except refusal as e:
            log.info("refused web read: %s", e)
            body, title = f"<p class='bad'>{_esc(e)}</p>", name
        response = HTMLResponse(_page(title, body, nav=_project_nav(name)))
        _issue(response)
        return response

    def _pick(request, code: str) -> str:
        """The consumer asked for, or the first one there is. Never empty: the
        readings that want a consumer refuse without one, and a page that
        opened on a refusal would teach that it is broken."""
        wanted = (request.query_params.get("consumer") or "").strip()
        names = _consumers(code)
        return wanted if wanted in names else (names[0] if names else "")

    async def rules_page(request):
        def render(name, code):
            consumer = _pick(request, code)
            picker = _consumer_picker(name, code, consumer, "rules")
            if not consumer:
                return picker + "<p class='note'>No consumer in this project.</p>", name
            data = registry.list_rules(code, consumer)
            # The BRIEF leads, exactly as it does in rules_list: "you are
            # so-and-so, and these are your rules" is one round trip, which is
            # the reason the field exists at all. Empty is not an error.
            brief = (f"<p class='ok'>{_esc(data['brief'])}</p>" if data["brief"]
                     else "<p class='note'>This consumer has no brief.</p>")
            legend = " · ".join(f"{_esc(d)} {_esc(t)}"
                                for d, t in data["domains"].items())
            rows = "".join(
                f"<tr><td><a href='/p/{_esc(name)}/rule/{_esc(r['id'])}"
                f"?consumer={_esc(consumer)}'>{_esc(r['id'])}</a></td>"
                f"<td><pre>{_esc(r['body'])}</pre></td></tr>"
                for r in data["rules"])
            return (picker + brief
                    + (f"<p class='note'>{legend}</p>" if legend else "")
                    + f"<p class='note'>{data['count']} in force, widest first — "
                      f"what comes first binds everyone. "
                      f"{data['outside_your_scope']} are outside this perimeter.</p>"
                    + (f"<table><tbody>{rows}</tbody></table>" if rows
                       else "<p class='note'>Nothing in force for this consumer.</p>"),
                    f"{name} — rules for {consumer}")
        return _read_page(request, render)

    async def rule_page(request):
        def render(name, code):
            rid = request.path_params["rule"]
            consumer = _pick(request, code)
            out = [f"<p class='note'>Read as <b>{_esc(consumer)}</b>.</p>"]
            data = registry.get_rules(code, [rid], consumer)
            for f in data["found"]:
                mark = (f" — <b>{_esc(f['status'])}</b>" if f.get("status") else "")
                if f.get("superseded_by"):
                    mark += f", superseded by {_esc(f['superseded_by'])}"
                out.append(f"<p>{_esc(f['id'])}{mark}</p><pre>{_esc(f['body'])}</pre>")
            for f in data["not_yours"]:
                out.append(f"<p class='bad'>{_esc(f['id'])} exists and is not this "
                           f"consumer's: it is held by "
                           f"{_esc(', '.join(f['held_by']) or 'nobody')}.</p>")
            for f in data["never_defined"]:
                out.append(f"<p class='bad'>{_esc(f)} was never defined in this "
                           f"project — a broken citation, or another project's "
                           f"code.</p>")
            # THE HISTORY, which is where the state, the perimeter and the why
            # live version by version. It is perimeter-free on purpose: what a
            # rule has BEEN is not addressed to a consumer.
            versions = []
            try:
                versions = registry.history(code, rid)["versions"]
            except refusal as e:
                out.append(f"<p class='note'>{_esc(e)}</p>")
            if versions:
                rows = "".join(
                    f"<tr><td>v{_esc(v['version'])}</td><td>{_esc(v['ts'])}</td>"
                    f"<td>{_esc(v['action'])}</td><td>{_esc(v['status'])} · "
                    f"{_esc(v['permanence'])}</td><td>{_esc(v['scopes'])}</td>"
                    f"<td class='note'>{_esc(v['reason'])}</td></tr>"
                    for v in versions)
                out.append("<h2>History</h2><table><thead><tr><th></th><th>when</th>"
                           "<th>what</th><th>state</th><th>perimeter</th><th>why</th>"
                           f"</tr></thead><tbody>{rows}</tbody></table>")
                # The expiry, read from the method born in C5: the page used
                # to DECLARE that no method of Registry handed this out, and
                # the declaration was the gap's honest form. The deciding
                # still happens on the expiring queue; this is a reading.
                exp = registry.expiry(code, rid)
                if exp["permanence"] == "permanent":
                    out.append("<p class='note'>Permanent: no expiry — "
                               "somebody promised to notice when it goes "
                               "stale.</p>")
                elif exp["expires_at"]:
                    gone = (exp["status"] == "active" and not exp["in_force"])
                    out.append(f"<p class='note'>Expires "
                               f"{_esc(exp['expires_at'])}"
                               + (" — <b>already expired</b>: it has left the "
                                  "lists on its own" if gone else "")
                               + ". Renewal lives on the pending page, where "
                                 "the reason is in front of you.</p>")
                latest = versions[-1]["version"]
                a = int(request.query_params.get("a") or max(1, latest - 1))
                b = int(request.query_params.get("b") or latest)
                if latest > 1:
                    diff = registry.compare(code, rid, a, b)
                    out.append(f"<h2>v{_esc(a)} to v{_esc(b)}</h2>"
                               + (f"<pre>{_esc(diff['diff'])}</pre>" if not diff["identical"]
                                  else "<p class='note'>Identical.</p>"))
            return "".join(out), f"{name} — {rid}"
        return _read_page(request, render)

    async def pending_page(request):
        def render(name, code):
            consumer = (request.query_params.get("consumer") or "").strip()
            picker = _consumer_picker(name, code, consumer, "pending")
            data = registry.pending(code, consumer if consumer in _consumers(code) else "")
            def table(title, rows, why=False):
                if not rows:
                    return f"<h2>{title}</h2><p class='note'>None.</p>"
                trs = "".join(
                    f"<tr><td><a href='/p/{_esc(name)}/rule/{_esc(r['id'])}'>"
                    f"{_esc(r['id'])}</a></td><td>{_esc(r.get('title'))}</td>"
                    f"<td class='note'>{_esc(r.get('expires_at') or '')}</td>"
                    f"<td class='note'>{_esc(r.get('reason') or r.get('denied_reason') or '')}"
                    f"</td></tr>" for r in rows)
                return f"<h2>{title}</h2><table><tbody>{trs}</tbody></table>"
            # The expiring queue is per CONSUMER in the engine, and it is the
            # only list that carries the WHY — it is the one read in order to
            # decide, and that decision is undecidable without the reason in
            # front of you. Without a consumer chosen the engine returns none,
            # and the page says so rather than showing an empty table that
            # reads like good news.
            #
            # And it is where the corpus is GOVERNED: renewal and promotion
            # act from here, since v3.0.0 — the reason is on the row you
            # tick, which is the whole argument for the queue being the one
            # place the date is published. Same contract as the lot: master
            # once per action, ceiling per action.
            expiring = data["expiring_within_30_days"]
            if expiring:
                trs = "".join(
                    f"<tr><td><label><input type='checkbox' name='rule' "
                    f"value='{_esc(r['id'])}'> "
                    f"<a href='/p/{_esc(name)}/rule/{_esc(r['id'])}'>"
                    f"{_esc(r['id'])}</a></label></td><td>{_esc(r.get('title'))}</td>"
                    f"<td class='note'>{_esc(r.get('expires_at') or '')}</td>"
                    f"<td class='note'>{_esc(r.get('reason') or '')}</td></tr>"
                    for r in expiring)
                exp_html = (
                    f"<h2>Expiring within 30 days</h2>"
                    f"<form method='post' action='/p/{_esc(name)}/pending'>"
                    f"<input type='hidden' name='consumer' value='{_esc(consumer)}'>"
                    f"<table><tbody>{trs}</tbody></table>"
                    f"<p class='note'>Renewal is where the corpus is governed, "
                    f"and the question is not \"is it still true?\" but \"would "
                    f"I file this today, for the reason it was filed for?\" — "
                    f"the reason is on the row. Promotion is rare and "
                    f"deliberate: a permanent rule is one you promise to "
                    f"notice when it goes stale.</p>"
                    f"<label for='pmaster'>Master — once for this action</label>"
                    f"<input id='pmaster' type='password' name='master' "
                    f"autocomplete='current-password' required>"
                    f"<p><button type='submit' name='action' value='renew'>"
                    f"Renew the ticked</button> "
                    f"<button type='submit' name='action' value='promote'>"
                    f"Promote the ticked to PERMANENT</button></p></form>")
            else:
                exp_html = ("<h2>Expiring within 30 days</h2>"
                            "<p class='note'>None.</p>")
            tail = ("" if consumer else
                    "<p class='note'>The expiring queue is per consumer: choose "
                    "one above to see it.</p>")
            return (picker + table("Waiting", data["waiting"])
                    + table("Denied", data["denied"])
                    + exp_html + tail,
                    f"{name} — pending")
        return _read_page(request, render)

    async def pending_action(request):
        """Renew or promote the ticked rules: the write half of the pending
        page. Same shape as the lot's action — session, master retyped once,
        ceiling per action, refusals as sentences — because a second shape
        would be a second thing to get wrong."""
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        code = _code_of(name)
        if code is None:
            return _no_project(name)
        form = await request.form()
        consumer = (form.get("consumer") or "").strip()
        back = (f"<p><a href='/p/{_esc(name)}/pending"
                + (f"?consumer={_esc(consumer)}" if consumer else "")
                + "'>Back to the pending page</a></p>")
        def say(msg, cls, status):
            return HTMLResponse(_page(f"{name} — pending",
                                      f"<p class='{cls}'>{msg}</p>{back}",
                                      nav=_project_nav(name)), status_code=status)
        if not secrets.compare_digest((form.get("master") or "").strip(), master):
            log.warning("refused web renewal: wrong master, from %s",
                        _client(request))
            return say("Wrong master. Nothing was changed.", "bad", 401)
        action = (form.get("action") or "").strip()
        ticked = [i.strip() for i in form.getlist("rule") if i.strip()]
        if action not in ("renew", "promote"):
            return say("Unknown action. Nothing was changed.", "bad", 400)
        if not ticked:
            return say("Nothing ticked, nothing done.", "bad", 400)
        if len(ticked) > action_cap:
            return say(f"{len(ticked)} ticked and the ceiling for one action "
                       f"is {action_cap}. Nothing was changed: do it in more "
                       f"than one pass, which is the point of the ceiling.",
                       "bad", 400)
        try:
            if action == "renew":
                v = registry.renew(code, ticked)
                whys = "".join(f"<li>{_esc(rid)} <span class='note'>— why it "
                               f"exists: {_esc(why)}</span></li>"
                               for rid, why in v["reasons"].items())
                return HTMLResponse(_page(
                    f"{name} — renewed",
                    f"<p class='ok'>Renewed <b>{_esc(', '.join(v['renewed']))}"
                    f"</b> until {_esc(v['expires_at'])} — let in again, which "
                    f"is what staying costs.</p><ul>{whys}</ul>{back}",
                    nav=_project_nav(name)))
            v = registry.promote(code, ticked)
            return HTMLResponse(_page(
                f"{name} — promoted",
                f"<p class='ok'>Permanent: <b>{_esc(', '.join(v['promoted']))}"
                f"</b>. No expiry, it never leaves on its own — you promised "
                f"to notice when it goes stale.</p>{back}",
                nav=_project_nav(name)))
        except refusal as e:
            log.info("refused web renewal: %s", e)
            return say(_esc(e), "bad", 400)

    async def status_page(request):
        def render(name, code):
            st = registry.status(code)
            def dl(d):
                return "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
                               for k, v in d.items())
            return ("<h2>Rules</h2><table><tbody>" + dl(st["rules"])
                    + "</tbody></table><h2>By domain</h2><table><tbody>"
                    + dl(st["by_domain"]) + "</tbody></table>"
                    + "<h2>By consumer</h2><table><tbody>"
                    + dl(st["by_consumer"]) + "</tbody></table>"
                    + "<h2>Database</h2><table><tbody>" + dl(st["database"])
                    + "</tbody></table>", f"{name} — state")
        return _read_page(request, render)

    routes = [
        Route("/", home, methods=["GET"]),
        Route("/login", login, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/admin", admin_page, methods=["GET"]),
        Route("/admin/create", admin_create, methods=["POST"]),
        Route("/admin/rekey", admin_rekey, methods=["POST"]),
        Route("/admin/backup", admin_backup, methods=["POST"]),
        Route("/p/{project}/", project_home, methods=["GET"]),
        Route("/p/{project}/batch", batch_page, methods=["GET"]),
        Route("/p/{project}/batch", batch_action, methods=["POST"]),
        Route("/p/{project}/rules", rules_page, methods=["GET"]),
        Route("/p/{project}/rule/{rule}", rule_page, methods=["GET"]),
        Route("/p/{project}/pending", pending_page, methods=["GET"]),
        Route("/p/{project}/pending", pending_action, methods=["POST"]),
        Route("/p/{project}/status", status_page, methods=["GET"]),
    ]
    return Starlette(routes=routes)
