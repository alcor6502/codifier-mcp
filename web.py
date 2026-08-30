"""
web.py — the administration UI: the second server, in the same process.

WHY IT LIVES HERE AND NOT IN A CONTAINER OF ITS OWN. Two processes on the same
SQLite database do not share the engine's RLock, so a second container is a
closed alley — written down as one in `Decisioni aperte.md` and not reopened
here. One process, one asyncio loop, two `uvicorn.Server`: the MCP app from
`mcp.http_app()` on the MCP port, this one on WEB_PORT.

WHAT IT MAY TOUCH. **The contract towards the engine is the methods of
`Registry` and `Project`, and nothing else: not one line of SQL lives in this
file.** That is the constraint that keeps the other road open — the UI as a
second MCP client, in a container of its own — because a layer that only ever
calls the methods the tools call can be moved behind them later without being
rewritten. A query in here would quietly close that road, and nothing at
runtime would complain.

TWO CLASSES SINCE v4.0.0, and the split is the whole shape of this file.
`Registry` is the ROUTER — `projects.txt` in, one `Project` out — and every
reading and every gesture happens on the `Project`. The page's door is
`by_name()` and not `project(code)`: on the MCP side a code is what proves you
may speak at all, here the person has already proved it with the UI password,
and the NAME is what a URL may carry. The codes stay in the file and in the
instructions of whoever holds them — `registry.projects()` does not hand them
out any more, which is why the deployment page that used to print them is gone
rather than emptied.

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

THE ONE PAGE WITH NO DOOR, and it is written here rather than found: the
closing link. A task posted to a person carries a button, and the ticket in
that URL is the whole credential — no session, no password. It reaches ONE
entry, it can only close it, and what makes that acceptable is that this port
does not answer outside the tailnet. ⚠ A premise, not a detail: publish this
port anywhere else and that page is a hole.

WHERE THE MASTER IS TYPED: ONCE, AT THE DOOR, AND NOWHERE ELSE. Until v7.0.0
every writing gesture asked for it again — the lot, a one-time code, the
profile, a person, their post — on the ground that a session alone is a browser
left open on the iPad. That guard is GONE, on purpose and by decision, and the
reason it went is the one the file already stated against itself: a secret
typed five times an hour is typed without looking, and a password that is typed
without looking defends nothing while costing every gesture. What defends this
UI is the door — the session, signed, eight hours of inactivity, dead on a
restart — plus the fact that the port is not published outside the tailnet.
The one-time codes are untouched: they guard the MCP surface, where the caller
is a chat and not a person, and there the second factor is the whole design.

⚠ THE CONSEQUENCE, WRITTEN WHERE IT IS DECIDED: a live session now reaches every
gesture on this page. Signing out and closing the tab is the whole of the
protection, and the day this port were published anywhere but the tailnet, this
decision would have to be taken again.

WHAT IS NOT HERE ANY MORE. The deployment page: it created projects, rekeyed
them and printed their codes, and all three died with the declarative registry
— a project is now a line in `projects.txt`, written from Unraid by the person
who chooses its codes. What took its place is the codes page, one per project,
where the one-time codes are minted: that is the one thing the design gives to
this UI and to nothing else.


Configuration, all through environment variables:
  WEB_PORT          the port this server listens on (default 9443). It must be
                    one the Funnel CANNOT publish and it must not collide with
                    the MCP port — the preflight refuses both at the edge
  WEB_BASE_URL      where this UI answers from, e.g. http://10.0.0.9:9443 —
                    read in mail.py, and the address the closing link in a
                    posted task is built on. Optional, and without it there is
                    no button and the message is the one sent yesterday: a
                    guessed address in an email is a link that goes somewhere
                    real and wrong
  WEB_UI_PASSWORD   the password of this whole UI. Read by the preflight,
                    which refuses a missing one, a placeholder and anything
                    under 12 characters; handed to build() by server.py, never
                    read from here. It was WEB_MASTER_CODE until v4.0.0, and
                    the name changed because what it opens is the UI, not a
                    "master" level that no longer exists
"""
from __future__ import annotations

import hmac
import html
import logging
import os
import secrets
import time
from collections import deque

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

# EIGHT HOURS of INACTIVITY, sliding: every authenticated request re-issues the
# cookie. Not eight hours of session — a page left open on the iPad while the
# batch is read is the normal case, and logging the person out mid-decision
# would teach them to keep a second tab logged in.
#
# It was one hour while every write retyped the master. When that retype went
# (v7.0.0) this number became the ONLY thing standing between a borrowed
# browser and the corpus, and it went UP rather than down — deliberately. An
# hour spent nothing but the password again on a page that had just refused to
# do anything without it; the guard that is worth having here is a working day
# that ends, plus a port that does not leave the tailnet.
SESSION_MAX_IDLE = 8 * 3600

SESSION_COOKIE = "codifier_admin"

# There is NO ceiling constant here any more, and its absence is a decision.
# It was WEB_ACTION_CAP in the container's template, next to PENDING_CAP —
# two knobs this page's own contract forced to be equal, since an unticked
# proposal is a denied one and a queue has to be answered whole. Two numbers
# that must agree are one number, and it belongs to the PROJECT rather than to
# the container, which is multi-tenant: it is `queue_cap`, asked of the project
# the page already has in hand. A default in this file would be a second
# opinion about a policy that is not this file's.

# How many lines the maintenance page can show, and the ONLY place the number
# lives: the page renders it from here rather than spelling it out a second
# time.
LOG_RING_LINES = 200

# How many minted codes stay on the line at once. Not a policy on minting —
# the engine decides what a code is worth and how long it lives, and pressing
# the button an eleventh time still mints — but a ceiling on what one page
# prints, so that a run cannot grow without end down an iPad. The oldest fall
# off the front, because the one you are about to use is the one you just
# pressed for.
CODE_RUN_MAX = 10

# WHAT THIS PAGE SIGNS, in one place. Every gesture made here goes into the
# history under this name — a person's own name would have to be typed, and a
# field that types a signature is a field that types somebody else's. What the
# history witnessed is that it was done at the admin page, by whoever holds
# the password, and that is what this says.
WEB_SIGNATURE = "web ui"


class LogRing(logging.Handler):
    """The last lines of the service's own log, IN MEMORY.

    A ring and not a file, and it is the whole design rather than a shortcut.
    A file would need a path, a rotation, permissions and a place in the
    template, and it would be a second copy of something the console already
    has; reading the container's log through docker would need the socket,
    which is a hole a page on the LAN must not be able to reach through. A
    `deque` with a `maxlen` cannot grow, cannot be forgotten and dies with the
    process — which is also its honest limit, written on the page: a restart
    empties it.

    It records exactly what the logger emits and nothing more. It has no
    source of its own, so it cannot show anything the console does not already
    show, and LOG_LEVEL governs it for free.
    """

    def __init__(self, lines: int = LOG_RING_LINES):
        super().__init__()
        formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s",
                                      "%Y-%m-%d %H:%M:%S")
        # UTC, and said so on every line. The container's clock is not the
        # reader's, and a time with no zone is the kind of detail that is only
        # ever noticed while something is going wrong.
        formatter.converter = time.gmtime
        self.setFormatter(formatter)
        self.lines: deque[str] = deque(maxlen=lines)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(self.format(record))
        except Exception:                                    # pragma: no cover
            self.handleError(record)


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

# The whole of the styling, and the ONLY place it lives. No framework and no
# build step: a stylesheet fetched from a CDN is a third party in the page that
# approves rules, and one compiled by a toolchain is a file nobody can read
# against the code. It is a constant in the module that writes the pages, which
# means the diff that changes the look is the diff that changes the page.
#
# The palette is declared TWICE and nowhere else: once for light, once inside
# the dark-scheme query. `color-scheme: light dark` stays, and it is not
# decoration — it is what makes the form controls, the scrollbars and the
# focus ring follow the system instead of staying white on a dark page.
# `light-dark()` would say the same thing in half the lines and is not used:
# the two blocks work on every browser that ever reached this page, and this
# page is read from an iPad.
#
# The sizes are the iPad's, not the desk's. 16px on the fields because Safari
# ZOOMS the page when it focuses anything smaller, and a page that jumps when
# you tap the master is a page you mistype the master into; 2.75rem of height
# on everything tappable, which is the 44 points Apple asks for and roughly
# the width of a thumb.
_CSS = """
:root {
  color-scheme: light dark;
  --bg: #fdfdfb; --fg: #1b1c1a; --muted: #62635e; --line: #dcdcd5;
  --field: #ffffff; --raised: #f2f2ec; --accent: #2d5c86;
  --bad: #9d2f27; --bad-bg: #fbeceb; --good: #2c6739; --good-bg: #ebf4ee;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #16171a; --fg: #e7e7e2; --muted: #9a9b95; --line: #33353b;
    --field: #1e2025; --raised: #1e2025; --accent: #8fb8e2;
    --bad: #e79088; --bad-bg: #2b1b19; --good: #85c495; --good-bg: #172318;
  }
}
* { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body { font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
       margin: 0 auto; padding: 1.25rem 1.25rem 4rem; max-width: 62rem;
       background: var(--bg); color: var(--fg); }
header { display: flex; align-items: baseline; gap: .75rem 1.25rem;
         flex-wrap: wrap; border-bottom: 1px solid var(--line);
         padding-bottom: .7rem; margin-bottom: 1.4rem; }
header h1 { font-size: 1.2rem; font-weight: 600; margin: 0;
            letter-spacing: -.01em; }
header nav { margin-left: auto; display: flex; align-items: center;
             gap: .35rem; flex-wrap: wrap; font-size: .9rem; }
header nav a, header nav button {
  display: inline-block; padding: .4rem .6rem; border-radius: 6px;
  border: 1px solid transparent; background: transparent; color: var(--muted);
  text-decoration: none; font-size: .9rem; min-height: 0; }
header nav a:hover, header nav button:hover {
  color: var(--fg); background: var(--raised); }
h2 { font-size: .95rem; font-weight: 600; text-transform: uppercase;
     letter-spacing: .06em; color: var(--muted); margin: 2rem 0 .6rem; }
p { margin: .7rem 0; }
a { color: var(--accent); }
form.inline { display: inline; }
label { display: block; margin: 1.1rem 0 .3rem; font-size: .85rem;
        color: var(--muted); }
/* A label that WRAPS a tick is not a field label, it is the thing being read:
   in the lot page it carries the rule's title. Told apart with :has(), and a
   browser that does not know :has() falls back to the small grey line this
   page has always had — worse, never broken. */
label:has(input[type=checkbox]) { font-size: 1rem; color: var(--fg);
                                  margin: 0 0 .35rem; }
input[type=password], input[type=text], select {
  font: inherit; font-size: 1rem; padding: .55rem .7rem; min-height: 2.75rem;
  border: 1px solid var(--line); border-radius: 8px; background: var(--field);
  color: inherit; width: 100%; max-width: 24rem; }
input:focus-visible, select:focus-visible, button:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px; }
input[type=checkbox] { width: 1.15rem; height: 1.15rem; margin-right: .35rem;
                       vertical-align: -.15rem; accent-color: var(--accent); }
button { font: inherit; font-size: 1rem; padding: .55rem 1rem;
         min-height: 2.75rem; border: 1px solid var(--line); border-radius: 8px;
         background: var(--raised); color: inherit; cursor: pointer; }
button:hover { border-color: var(--muted); }
table { border-collapse: collapse; width: 100%; font-size: .92rem;
        margin: .5rem 0; }
th, td { text-align: left; padding: .5rem .6rem; border-bottom: 1px solid var(--line);
         vertical-align: top; overflow-wrap: anywhere; }
th { font-size: .78rem; text-transform: uppercase; letter-spacing: .05em;
     color: var(--muted); font-weight: 600; }
tr:last-child td { border-bottom: none; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
       font-size: .9em; background: var(--raised); border-radius: 4px;
       padding: .1rem .3rem; }
/* The minted codes, on one line, separated by TWO SPACES so that one drag
   takes the lot. `pre-wrap` and not the default: HTML collapses runs of
   whitespace, so without it the two spaces that make the run readable — and
   that survive the paste — would arrive as one. `break-all` because the run
   is long and an iPad is narrow. */
/* One entry of the log: a card, so that where one ends and the next begins is
   readable on a phone as well as at the desk — the two gestures under it act
   on THAT entry, and a run of them with no edge is a page you close the wrong
   one from. */
div.entry { border: 1px solid var(--line); border-radius: 8px;
            padding: .6rem .8rem; margin: .6rem 0; background: var(--raised); }
div.entry form { margin: .3rem 0; }
div.entry details { margin-top: .4rem; }
code.run { white-space: pre-wrap; overflow-wrap: break-all;
           display: block; padding: .5rem .6rem; line-height: 1.7; }
/* Rule bodies are prose, and they used to run off the side of an iPad: a
   horizontal scrollbar inside a page you read with a thumb is a paragraph you
   do not read. */
pre { font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: .9rem; line-height: 1.5; white-space: pre-wrap;
      overflow-wrap: anywhere; background: var(--raised); border-radius: 8px;
      padding: .7rem .8rem; margin: .5rem 0; }
article { border: 1px solid var(--line); border-radius: 10px;
          padding: .9rem 1rem; margin: .9rem 0; }
.note { font-size: .85rem; color: var(--muted); }
.bad { border-left: 3px solid var(--bad); background: var(--bad-bg);
       color: var(--bad); padding: .6rem .8rem; border-radius: 0 8px 8px 0; }
.ok { border-left: 3px solid var(--good); background: var(--good-bg);
      color: var(--good); padding: .6rem .8rem; border-radius: 0 8px 8px 0; }
.bad code, .ok code { background: #8881; }
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
<p class="note">Typed once, here, and not again: every page behind this one
takes the session and nothing else. Eight hours of inactivity end it, and so
does a restart of the service.</p>""")


# =====================================================================
# The application
# =====================================================================

def build(*, registry, log, master: str, refusal, fault,
          backup_dir: str = ""):
    """The Starlette application. Handed the engine, the service's own logger
    and the master: a web layer that reached for any of them itself would be a
    second place where the configuration is decided, and a second logger is how
    a refusal stops appearing in the log everybody reads.

    `refusal` and `fault` are the engine's two classes, handed in for the same
    reason `make_tool` is handed them on the MCP side: which exception is a
    designed refusal and which is a genuine fault is the one thing neither the
    engine nor this file can know on its own. A refusal becomes a sentence on
    the page; a fault is left to rise, with its traceback, at ERROR.

    ⚠ BOTH, and not just the first, because `fault` is a SUBCLASS of `refusal`:
    caught with one name this file would answer a broken registry — a file that
    does not parse, a database from another generation — with a polite `no
    project called that`, which is the sentence for a typo. Every place that
    catches here catches the fault FIRST and re-raises it, in the same order
    make_tool uses."""
    from starlette.applications import Starlette
    from starlette.responses import HTMLResponse, RedirectResponse, Response
    from starlette.routing import Route

    # Generated AT BOOT, and nowhere else. Read from the environment it would
    # survive a restart, which is precisely the property this design does not
    # want: a restart invalidates every session, and the cost of that is
    # typing a password once.
    secret = secrets.token_bytes(32)

    # The ring is hung on the logger the service handed in — not on a logger
    # of its own, which is how a line stops appearing in the log everybody
    # reads. An older one is taken OFF first: the service calls build() once,
    # the probes call it many times, and two rings on one logger is every line
    # twice with nothing to say so.
    for _stale in [h for h in log.handlers if isinstance(h, LogRing)]:
        log.removeHandler(_stale)
    ring = LogRing()
    log.addHandler(ring)

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
           "<a href='/maintenance'>maintenance</a>"
           "<form class='inline' method='post' action='/logout'>"
           "<button type='submit'>sign out</button></form>")

    # ---------- routes ----------

    async def home(request):
        if not _session_ok(request):
            return _guest(request)
        # The projects menu, and it is EVERYTHING this page can say about a
        # project without opening it: what the registry file declares plus
        # what the router found on disk. No codes — `projects()` stopped
        # handing them out in v4.0.0 — and no counts, because a count is a
        # query per project on a menu nobody reads for counts.
        #
        # `born_empty` is on it because it is the signature of one specific
        # accident: a folder renamed without its registry line, which serves a
        # project that answers every call with an empty corpus.
        data = registry.projects()
        rows = "".join(
            f"<tr><td><a href='/p/{_esc(p['name'])}/'>{_esc(p['name'])}</a></td>"
            f"<td class='note'>{_esc(p['slug'])}.db · schema {_esc(p['schema'])}</td>"
            f"<td class='bad'>{'born empty this boot' if p['born_empty'] else ''}</td>"
            f"</tr>" for p in data["projects"])
        body = ((f"<table><thead><tr><th>Project</th><th>File</th><th></th></tr>"
                 f"</thead><tbody>{rows}</tbody></table>"
                 f"<p class='note'>{_esc(data['count'])} served, from "
                 f"{_esc(data['registry'])} — the file is edited from Unraid, and "
                 f"a project is created by adding a line to it. This page has no "
                 f"button that writes there, by decision: the registry is "
                 f"declarative.</p>")
                if rows else
                (f"<p class='note'>No project is served. Add a line to "
                 f"{_esc(data['registry'])} — <code>name | reference code | admin "
                 f"code</code> — and the database is created on the next "
                 f"read.</p>"))
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

    def _open(name: str):
        """Name to PROJECT, resolved on EVERY request, or None.

        `by_name` is the router's door for this page and only for this page:
        the codes never leave the file, so they are never in a URL, in a
        cookie or in a page, and a screenshot of the browser and a link sent
        to somebody are both harmless. Resolved every time rather than held,
        because the registry is re-read when its file changes and a Project
        kept in a closure would outlive the line that declared it.

        ⚠ The FAULT is re-raised before the refusal is swallowed. `by_name`
        re-reads the registry, so what comes out of here is not only "no such
        name": it is also a file that does not parse and a database from
        another generation. Those are faults, and answering them with a 404
        would tell whoever is looking that they typed the name wrong."""
        try:
            return registry.by_name(name)
        except fault:
            raise
        except refusal:
            return None

    def _no_project(name: str):
        return HTMLResponse(_page("Not found",
                                  f"<p class='bad'>No project called "
                                  f"{_esc(name)} is served. The registry file is "
                                  f"what decides, and it is edited from "
                                  f"Unraid.</p>", nav=NAV), status_code=404)

    def _project_nav(name: str) -> str:
        n = _esc(name)
        return (f"<a href='/p/{n}/batch'>lot</a><a href='/p/{n}/rules'>rules</a>"
                f"<a href='/p/{n}/tasks'>log</a>"
                f"<a href='/p/{n}/profile'>profile</a>"
                f"<a href='/p/{n}/people'>people</a>"
                f"<a href='/p/{n}/codes'>codes</a>"
                f"<a href='/p/{n}/status'>state</a><a href='/'>projects</a>"
                "<form class='inline' method='post' action='/logout'>"
                "<button type='submit'>sign out</button></form>")

    def _consumer_picker(name: str, prj, chosen: str, where: str) -> str:
        """The consumer is a MENU and not a text field, and the list comes from
        the engine. Typed by hand it would be one more place a name can be
        spelt wrong, and the engine's refusal for an unknown consumer is not
        the answer to a typo — it is the answer to asking about somebody who
        does not exist.

        Since v4.0.0 `project_info` returns the LIVE ones only, so a retired
        consumer cannot be picked here at all: the menu offers what still
        exists rather than offering a name that every call behind it would
        refuse."""
        opts = "".join(
            f"<option value='{_esc(n)}'{' selected' if n == chosen else ''}>"
            f"{_esc(n)}</option>" for n in _consumers(prj))
        return (f"<form method='get' action='/p/{_esc(name)}/{where}'>"
                f"<label for='consumer'>Consumer</label>"
                f"<select id='consumer' name='consumer'>{opts}</select> "
                f"<button type='submit'>show</button></form>")

    def _consumers(prj) -> list[str]:
        return [c["name"] for c in prj.project_info()["consumers"]]

    async def project_home(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        # ONE call, and it carries the three counts a caller cannot work out
        # from the payload — the queue among them. The old page asked a second
        # method for the length of the waiting list, which was a second reading
        # of a number this one already knows.
        info = prj.project_info()
        waiting = info["counts"]["proposed"]
        # `domains` is a LIST of dicts and not a mapping: code, gloss, why it
        # exists, and how many rules are in force in it. It was a mapping until
        # v4.0.0 and this page read it as one — nothing said otherwise, because
        # no suite renders a page.
        body = (f"<p><a href='/p/{_esc(name)}/batch'>The lot</a> — "
                f"{waiting} proposal{'' if waiting == 1 else 's'} waiting. "
                f"{_esc(info['counts']['rules_in_force'])} in force · "
                f"{_esc(info['counts']['tasks_open'])} open tasks.</p>"
                f"<h2>Consumers</h2><p>"
                + " · ".join(f"{_esc(c['name'])} <span class='note'>{_esc(c['kind'])}"
                             f"</span>" for c in info["consumers"])
                + "</p><h2>Groups</h2><p class='note'>"
                + (" · ".join(f"{_esc(g['name'])} ({_esc(', '.join(g['members']))})"
                              for g in info["groups"]) or "None.")
                + "</p><h2>Domains</h2><p class='note'>"
                + " · ".join(f"{_esc(d['code'])} {_esc(d['description'])} "
                             f"[{_esc(d['rules_in_force'])}]"
                             for d in info["domains"])
                + "</p><p class='note'>Everything here is ALIVE: a retired "
                  "consumer, group or domain is not in these lists — it is on "
                  f"the <a href='/p/{_esc(name)}/status'>state</a> page, which "
                  "is where a revive finds its target.</p>"
                + f"<form method='post' action='/p/{_esc(name)}/backup'>"
                  f"<p><button type='submit'>VACUUM INTO a quiescent copy of "
                  f"this project</button></p></form>"
                  f"<p class='note'>No password, and that is a decision rather "
                  f"than an omission: a backup changes nothing and the copy "
                  f"lands on the server's disk, not in this browser. It is per "
                  f"PROJECT because a project is a folder — the file, its -wal "
                  f"and its -shm — and copying one of the three is a corrupt "
                  f"backup.</p>")
        response = HTMLResponse(_page(name, body, nav=_project_nav(name)))
        _issue(response)
        return response

    # ---------- the lot ----------

    def _proposal_html(d: dict) -> str:
        sup = d.get("supersedes")
        # BOTH HALVES of the move, where the decision is taken. The engine
        # hands the victim back as ID AND current title, so what is read here
        # is what is being retired and not an ID to go and look up.
        sup_html = (f"<p class='bad'>Approving this also RETIRES "
                    f"{_esc(sup['id'])} — {_esc(sup['title'])} — in the same "
                    f"transaction, so there is no window in which both are in "
                    f"force.</p>") if sup else ""
        # THREE ROWS, and the third is the one that used to be missing. The
        # perimeter as DECLARED is what the proposer wrote; the consumers it
        # EFFECTIVELY reaches are that perimeter expanded a moment ago, because
        # a group is a label and a chat can have filled it since; and what
        # already binds that same audience is the commonest thing worth
        # catching at this door — a rule that says again what is already in
        # force. All three are computed by the engine: a page that worked out
        # an audience by itself would be a second reading of the corpus.
        dec = d["declared"]
        perimeter = dec["reach"]
        if dec["groups"]:
            perimeter += " · groups: " + ", ".join(dec["groups"])
        if dec["exceptions"]:
            perimeter += " · plus: " + ", ".join(dec["exceptions"])
        already = "".join(f"<li>{_esc(x)}</li>" for x in d["already_bound_by"])
        already_html = (f"<p class='bad'>Already binding every one of them:</p>"
                        f"<ul>{already}</ul>") if already else ""
        return (f"<article><label><input type='checkbox' name='approve' "
                f"value='{_esc(d['id'])}'> <b>{_esc(d['id'])}</b> · "
                f"{_esc(d['title'])} <span class='note'>{_esc(d['type'])} · "
                f"proposed by {_esc(d['proposed_by'])} ({_esc(d['source'])}) · "
                f"{_esc(d['proposed_at'])}</span></label>"
                f"<p class='note'>why: {_esc(d.get('reason'))}</p>"
                f"{sup_html}"
                f"<pre>{_esc(d['body'])}</pre>"
                f"<p class='note'>declared: {_esc(perimeter)}</p>"
                f"<p class='note'>reaches {_esc(d['reaches_count'])}: "
                f"{_esc(', '.join(d['reaches']) or 'nobody')}</p>"
                f"{already_html}"
                # ONE REASON PER PROPOSAL, and it is not a form nicety: the
                # engine refuses the whole decision if an unticked rule has no
                # sentence to go with it. A single field for all of them would
                # file the same excuse against proposals that were refused for
                # different reasons, which is exactly what the proposer reads.
                f"<label for='why-{_esc(d['id'])}'>If you leave it unticked it "
                f"is DENIED — say why</label>"
                f"<input id='why-{_esc(d['id'])}' type='text' "
                f"name='reason:{_esc(d['id'])}'></article>")

    def _lot_page(name: str, prj, *, message: str = "", good: str = "",
                  status: int = 200):
        current = prj.batch()
        head = (f"<p class='bad'>{_esc(message)}</p>" if message else "") + \
               (f"<p class='ok'>{_esc(good)}</p>" if good else "")
        if not current["pending"]:
            return HTMLResponse(_page(f"{name} — the lot",
                                      head + "<p class='note'>Nothing is waiting.</p>",
                                      nav=_project_nav(name)), status_code=status)
        # The WHOLE pending batch, side by side. That is where three proposals
        # saying the same thing become visible as what they are, and it is why
        # ticking does not break the lot: it completes it.
        blocks = "".join(_proposal_html(d) for d in current["pending"])
        cap = current["queue_cap"]
        ceiling = ("<p class='note'>No ceiling on this project: every proposal "
                   "in the queue may be approved in one go.</p>" if cap is None
                   else f"<p class='note'>At most {_esc(cap)} ticks in one "
                        f"action — the ceiling is policy of this project, and "
                        f"the point of it is that at the twelfth signature in a "
                        f"row a person signs without reading.</p>")
        body = (f"{head}<form method='post' action='/p/{_esc(name)}/batch'>"
                f"<input type='hidden' name='digest' value='{_esc(current['digest'])}'>"
                f"{blocks}"
                f"<p class='note'>The digest covers what you are LOOKING AT — all "
                f"{_esc(current['count'])} of them — not what you tick. If a "
                f"proposal arrives while you read, this comes back refused and "
                f"you read again.</p>"
                f"{ceiling}"
                f"<p class='note'>{_esc(current['contract'])}</p>"
                f"<p><button type='submit'>Approve the ticked, deny the rest</button></p>"
                f"</form>")
        return HTMLResponse(_page(f"{name} — the lot", body, nav=_project_nav(name)),
                            status_code=status)

    async def batch_page(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        response = _lot_page(name, prj)
        _issue(response)
        return response

    async def batch_action(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        form = await request.form()
        # NO MASTER HERE any more, and what took its place is not nothing: the
        # DIGEST. The guard this page needs is not "is this the right person" —
        # the session answered that at the door — it is "is this the queue you
        # read", and a password never answered that. It travels in the hidden
        # field and `decide()` compares it inside the transaction.
        seen = (form.get("digest") or "").strip()
        ticked = [i.strip() for i in form.getlist("approve") if i.strip()]
        # One reason per proposal, carried on a field named after the ID it
        # belongs to. The page collects them ALL and hands them over: which of
        # them are needed is the engine's to say, because "unticked" is a fact
        # about this post and "denied costs a sentence" is a law of the corpus.
        denials = {k.split(":", 1)[1]: (v or "").strip()
                   for k, v in form.items()
                   if isinstance(k, str) and k.startswith("reason:")}
        # ONE CALL, ONE TRANSACTION, and the whole of the old dance is gone
        # with it. There used to be four checks here — stale digest, an ID
        # from outside the lot, the ceiling, then deny-before-approve — and
        # every one of them was a second copy of a law that lives in the
        # engine. `decide()` holds all four, refuses before it writes
        # anything, and records the yes and the no as a single decision: a
        # page that re-decided them would be a page that can disagree with
        # the corpus about what just happened.
        try:
            verdict = prj.decide(seen, ticked, denials)
        except fault:
            raise
        except refusal as e:
            # A designed refusal of the engine becomes a sentence on the page.
            # It is the same conversion the MCP side does in make_tool, for the
            # same reason: without it a missing reason or a stale digest
            # arrives as a 500, which teaches the person nothing and the log a
            # traceback that is not a fault. The page comes back as it is NOW,
            # because a refusal here always means "read it again".
            log.info("refused web decision: %s", e)
            return _lot_page(name, prj, status=400, message=str(e))
        return HTMLResponse(_page(f"{name} — done",
                                  _verdict_html(name, verdict),
                                  nav=_project_nav(name)))

    def _verdict_html(name: str, verdict) -> str:
        out = []
        for a in verdict["approved"]:
            line = (f"<p class='ok'>In force: <b>{_esc(a['id'])}</b>. It stays "
                    f"until somebody ends it — retire it, or supersede it with "
                    f"an heir.")
            if a.get("retired"):
                line += (f" It retired {_esc(a['retired'])} in the same "
                         f"transaction, so there was no moment in which both "
                         f"were in force.")
            out.append(line + "</p>")
        for d in verdict["denied"]:
            out.append(f"<p>Denied <b>{_esc(d['id'])}</b> — {_esc(d['reason'])}. "
                       f"The refusal and its reason are on the noticeboard of "
                       f"whoever filed it: silence became an answer.</p>")
        if not verdict["approved"] and not verdict["denied"]:
            out.append("<p class='note'>Nothing was decided.</p>")
        out.append(f"<p class='note'>Decision {_esc(verdict['decision'])}.</p>")
        out.append(f"<p><a href='/p/{_esc(name)}/batch'>Back to the lot</a></p>")
        return "".join(out)

    # ---------- the one-time codes: the second factor, minted here ----------
    #
    # The deployment page is GONE, and it is worth saying what left with it:
    # creating a project and regenerating its pair were machinery for a
    # registry that lived in a table. From v4.0.0 the registry is a declarative
    # FILE, edited from Unraid — a project is a line, its codes are chosen by
    # the person who writes it — so those two buttons had nothing left to call,
    # and `projects()` no longer hands a code out to print.
    #
    # What this page does instead is the one thing the design says belongs to
    # the UI and to nothing else: minting the one-time codes that every
    # MODIFICATION of something that already exists asks for, on top of the
    # admin code. It sits on the PROJECT because that is what it is — a row in
    # that project's database, so a code belongs to its project by
    # construction rather than by a check — and behind the session, which since
    # v7.0.0 is the whole of what this page asks. What keeps a minted code
    # small is not a password in front of it: it is that it lives minutes and
    # buys ONE gesture.
    #
    # It is copied by hand from here into the chat that needs it. That is not
    # a limitation to route around: the point of a code somebody has to go and
    # fetch is the breath it forces — it guards against haste, not malice.

    def _codes_html(name: str, prj, *, run=(), minted=None,
                    message: str = "") -> str:
        """`run` is what has been minted SINCE THIS PAGE WAS OPENED, in order,
        the newest last. Three codes is the button pressed three times, and
        they stay on the page together so that one drag takes all three —
        which is the gesture this page exists for: a chat that needs three
        modifications needs three codes, and fetching them one page-load at a
        time was the whole of the tedium.

        It is carried in a HIDDEN FIELD and in no other state: no server-side
        basket that would outlive the tab and hold cleartext codes for
        whoever asks next, and no cookie. The field holds exactly what is
        already printed on the page in front of the person — so it adds no
        exposure — and it dies when the page is left, which is why the GET
        starts an empty run rather than restoring one."""
        data = prj.auth_codes()
        head = f"<p class='bad'>{_esc(message)}</p>" if message else ""
        if run:
            # SHOWN ONCE, and once is all they are good for. What the database
            # keeps is a hash, so these are not values that can be read back
            # from anywhere — leaving the page without copying them costs
            # another minting, which is cheap and is the whole reason this is
            # not treated as a secret to store.
            head += (f"<p class='ok'>{'Copy it now' if len(run) == 1 else f'All {len(run)} of them, in one drag'}"
                     f" — not shown again, and they cannot be read back:</p>"
                     f"<p><code class='run'>{_esc('  '.join(run))}</code></p>")
        if minted:
            head += (f"<p class='note'>The last one is good until "
                     f"{_esc(minted['expires_at'])} "
                     f"({_esc(minted['minutes'])} minutes), for ONE gesture on "
                     f"<b>{_esc(minted['project'])}</b>. A refused gesture rolls "
                     f"it back and does not spend it. Press the button again for "
                     f"another, and it joins the line above.</p>")
        live = "".join(f"<tr><td>#{_esc(r['code_id'])}</td>"
                       f"<td class='note'>minted {_esc(r['minted_at'])}</td>"
                       f"<td>expires {_esc(r['expires_at'])}</td></tr>"
                       for r in data["live"])
        spent = "".join(f"<tr><td>#{_esc(r['code_id'])}</td>"
                        f"<td class='note'>minted {_esc(r['minted_at'])}</td>"
                        f"<td class='note'>spent {_esc(r['spent_at'])}</td>"
                        f"<td>{_esc(r['spent_action'])}</td></tr>"
                        for r in data["spent"])
        return (head
                + "<form method='post' action='/p/" + _esc(name) + "/codes'>"
                  "<input type='hidden' name='run' value='"
                + _esc(" ".join(run)) + "'>"
                  "<label for='minutes'>Minutes it lives (blank = "
                + _esc(data["default_minutes"]) + ")</label>"
                  "<input id='minutes' type='text' name='minutes' inputmode='numeric'>"
                  "<p><button type='submit'>Mint"
                + (" another" if run else " a one-time code") + "</button>"
                + ("&nbsp;&nbsp;<a href='/p/" + _esc(name) + "/codes'>start a "
                   "fresh line</a>" if run else "") + "</p></form>"
                + f"<h2>Live — {_esc(data['count_live'])}</h2>"
                + (f"<table><tbody>{live}</tbody></table>" if live
                   else "<p class='note'>None. A gesture that needs one is "
                        "refused until somebody mints it, and the refusal says "
                        "so.</p>")
                + "<h2>Spent — the audit</h2>"
                + (f"<table><tbody>{spent}</tbody></table>" if spent
                   else "<p class='note'>None yet.</p>")
                + "<p class='note'>The spent rows are not rubbish: they are the "
                  "record of every structural gesture this project has had, "
                  "with what spent them. Nothing here shows a code — the "
                  "database keeps hashes.</p>")

    async def codes_page(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        response = HTMLResponse(_page(f"{name} — one-time codes",
                                      _codes_html(name, prj),
                                      nav=_project_nav(name)))
        _issue(response)
        return response

    async def codes_mint(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        form = await request.form()
        # NO MASTER HERE any more (v7.0.0). Minting is a gesture with a
        # ceiling of its own — the code lives minutes and buys ONE gesture —
        # and the password in front of it bought nothing that the session and
        # the expiry did not already buy. What it cost was real: a code was
        # never minted without typing the master, and three codes meant typing
        # it three times.
        # WHAT THE PAGE ALREADY SHOWS, handed back so the next one can join it.
        # It is split on whitespace and filtered, and it is never parsed for
        # meaning: this field decides what gets PRINTED and nothing else, so
        # the worst a doctored one can do is print rubbish to the person who
        # doctored it. A code is spent against the hash in the database, which
        # is not reachable from here.
        run = [c for c in (form.get("run") or "").split() if c][-CODE_RUN_MAX:]
        raw = (form.get("minutes") or "").strip()
        if raw and not raw.isdigit():
            return HTMLResponse(
                _page(f"{name} — one-time codes",
                      _codes_html(name, prj, run=run, message=(
                          f"{raw!r} is not a whole number of minutes. Nothing "
                          f"was minted.")),
                      nav=_project_nav(name)), status_code=400)
        try:
            minted = prj.mint_auth_code(int(raw) if raw else 0)
        except fault:
            raise
        except refusal as e:
            log.info("refused web minting: %s", e)
            return HTMLResponse(
                _page(f"{name} — one-time codes",
                      _codes_html(name, prj, run=run, message=str(e)),
                      nav=_project_nav(name)), status_code=400)
        # The MINTING is logged and the code is not: what a log is for here is
        # answering "who let that gesture through", and the answer is the row,
        # not the secret.
        log.info("one-time code minted for %s, %s minutes", name, minted["minutes"])
        run = (run + [minted["auth_code"]])[-CODE_RUN_MAX:]
        return HTMLResponse(_page(f"{name} — one-time codes",
                                  _codes_html(name, prj, run=run, minted=minted),
                                  nav=_project_nav(name)))

    async def project_backup(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        # NO MASTER HERE, and it is written down rather than left to be
        # noticed. `VACUUM INTO` is a READING: it changes nothing, and the file
        # it produces lands on the server's disk. The session is the whole of
        # what this needs. `test_surface` pins the exception BY NAME, so
        # putting the master back is a decision somebody has to take on purpose
        # rather than a line that drifts back in.
        try:
            out = prj.backup(backup_dir)
        except fault:
            raise
        except refusal as e:
            log.info("refused web backup: %s", e)
            return HTMLResponse(_page(f"{name} — backup",
                                      f"<p class='bad'>{_esc(e)}</p>",
                                      nav=_project_nav(name)), status_code=400)
        # Logged, and therefore visible on the maintenance page: a copy taken
        # off-site is the kind of thing you want to be able to date afterwards.
        log.info("backup written: %s — %s bytes", out["backup"], out["bytes"])
        return HTMLResponse(_page(
            f"{name} — backup",
            f"<p class='ok'>Quiescent copy written: {_esc(out['backup'])} — "
            f"{_esc(out['bytes'])} bytes. It opens without recovery, and it is "
            f"the one to take off-site.</p>"
            f"<p class='note'>{_esc(out['note'])}</p>"
            f"<p><a href='/p/{_esc(name)}/'>Back to the project</a></p>",
            nav=_project_nav(name)))

    # ---------- maintenance: what needs no secret and no project ----------
    #
    # What is left here is the LOG, and that is the whole page: a reading of a
    # reading, with no secret in it and no project behind it. The backup left
    # for the project pages when a backup became per-project — a project is a
    # folder now, and a copy of one of them is a gesture on one — and the
    # deployment page left the service altogether with the declarative
    # registry. A page where the master would defend nothing is a page where
    # retyping it teaches the habit of typing it without looking, which is the
    # habit the lot exists to prevent.

    def _maintenance_html(*, message: str = "", good: str = "") -> str:
        head = ((f"<p class='bad'>{_esc(message)}</p>" if message else "")
                + (f"<p class='ok'>{_esc(good)}</p>" if good else ""))
        # NEWEST FIRST. A log is read from a browser to answer "what just
        # happened", and that answer is at the bottom of a file and at the top
        # of a page. The number of lines is READ from the ring, never spelled
        # out here: two places that agree today are two places.
        seen = list(ring.lines)
        tail = ("<pre>" + _esc("\n".join(reversed(seen))) + "</pre>" if seen
                else "<p class='note'>Nothing logged since the service "
                     "started.</p>")
        return (head
                + f"<h2>The log — the last {len(seen)} of "
                  f"{_esc(ring.lines.maxlen)} lines</h2>"
                + "<p class='note'>Newest first. In memory and nowhere else: "
                  "the oldest line falls out when the ring is full, and a "
                  "restart empties it. These are the service's OWN lines and "
                  "only those — the startup line is printed before this buffer "
                  "exists, the preflight runs in another process, and fastmcp "
                  "keeps its records on a logger of its own. For those three "
                  "the console is still the place. LOG_LEVEL decides what "
                  "arrives here exactly as it decides what reaches the "
                  "console.</p>"
                + tail)

    async def maintenance_page(request):
        if not _session_ok(request):
            return _guest(request)
        response = HTMLResponse(_page("Maintenance", _maintenance_html(), nav=NAV))
        _issue(response)
        return response

    # ---------- the consultation: it reads, and only reads ----------

    def _read_page(request, render):
        """The shape every read page shares: a session, and the project
        resolved from the name in the URL. Written once so that adding a page
        cannot add a page that forgot the session."""
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        try:
            body, title = render(name, prj)
        except fault:
            raise
        except refusal as e:
            log.info("refused web read: %s", e)
            body, title = f"<p class='bad'>{_esc(e)}</p>", name
        response = HTMLResponse(_page(title, body, nav=_project_nav(name)))
        _issue(response)
        return response

    def _pick(request, prj) -> str:
        """The consumer asked for, or the first one there is. Never empty: the
        readings that want a consumer refuse without one, and a page that
        opened on a refusal would teach that it is broken."""
        wanted = (request.query_params.get("consumer") or "").strip()
        names = _consumers(prj)
        return wanted if wanted in names else (names[0] if names else "")

    async def rules_page(request):
        def render(name, prj):
            consumer = _pick(request, prj)
            picker = _consumer_picker(name, prj, consumer, "rules")
            if not consumer:
                return picker + "<p class='note'>No consumer in this project.</p>", name
            data = prj.list_rules(consumer)
            # The PROJECT leads and then the consumer, which is the order
            # `rules_list` puts them in and the order a person builds the
            # picture in: where am I, who am I, what binds me. Empty is not an
            # error — a project with no profile yet is a legitimate state.
            profile = data["profile"]
            who = data["consumer"]
            head = (f"<p class='ok'>{_esc(profile['brief'])}</p>" if profile["brief"]
                    else "<p class='note'>This project has no brief.</p>")
            head += (f"<p class='ok'>{_esc(who['brief'])}</p>" if who["brief"]
                     else "<p class='note'>This consumer has no brief.</p>")
            # The rules come in SHORT form — no bodies — because that is what
            # `rules_list` serves and this page must show what a chat reads,
            # not a richer version of it. The body is one click away.
            rows = "".join(
                f"<tr><td><a href='/p/{_esc(name)}/rule/{_esc(r['id'])}"
                f"?consumer={_esc(consumer)}'>{_esc(r['id'])}</a></td>"
                f"<td>{_esc(r['title'])}</td>"
                f"<td class='note'>{_esc(r['reach'])}"
                + (f" · {_esc(r.get('reaches_you'))}" if r.get("reaches_you") else "")
                + "</td></tr>" for r in data["rules"])
            # The DESK, in the short form the engine serves — id, title,
            # urgent, age. It closes the page for the same reason it closes
            # `rules_list`: post that dies in a queue nobody opens is post
            # nobody answers.
            desk = data["desk"]
            tasks = "".join(
                f"<li>{_esc(t['id'])} · {_esc(t['title'])} "
                f"<span class='note'>{'URGENT · ' if t['urgent'] else ''}"
                f"{_esc(t['age_days'])} days old</span></li>"
                for t in desk["open"])
            return (picker + head
                    + f"<p class='note'>{_esc(data['count'])} in force, widest "
                      f"first — what comes first binds everyone."
                    + (" ⚠ truncated" if data.get("truncated") else "")
                    + "</p>"
                    + (f"<table><tbody>{rows}</tbody></table>" if rows
                       else "<p class='note'>Nothing in force for this consumer.</p>")
                    + f"<h2>Desk — {_esc(desk['open_count'])} open</h2>"
                    + (f"<ul>{tasks}</ul>" if tasks
                       else "<p class='note'>Nothing open.</p>"),
                    f"{name} — rules for {consumer}")
        return _read_page(request, render)

    async def rule_page(request):
        def render(name, prj):
            rid = request.path_params["rule"]
            consumer = _pick(request, prj)
            out = [f"<p class='note'>Read as <b>{_esc(consumer)}</b>.</p>"]
            # ONE call for the rule AND its story. `history=True` is an
            # argument and not a second method since v4.0.0 — the story of a
            # rule is an attribute of the rule, and the diff between one
            # version and the one before it is computed on read. The arbitrary
            # A-to-B comparison died with it, on purpose: what anybody ever
            # wanted was N against N−1.
            data = prj.get_rules([rid], consumer, history=True)
            for f in data["rules"]:
                mark = f" — <b>{_esc(f['status'])}</b>"
                if f.get("superseded_by"):
                    mark += f", superseded by {_esc(f['superseded_by'])}"
                if f.get("supersedes"):
                    mark += f", superseding {_esc(f['supersedes'])}"
                out.append(f"<p>{_esc(f['id'])} · {_esc(f['title'])}{mark}</p>"
                           f"<pre>{_esc(f['body'])}</pre>")
                # The PERIMETER, read off the payload the page is already
                # holding: a page that asked a second method for it would be a
                # second reading of the same row.
                perimeter = f["reach"]
                if f.get("groups"):
                    perimeter += " · groups: " + ", ".join(f["groups"])
                if f.get("exceptions"):
                    perimeter += " · plus: " + ", ".join(f["exceptions"])
                out.append(f"<p class='note'>{_esc(perimeter)} — reaches "
                           f"{_esc(f['reaches_count'])}</p>")
                out.append(f"<p class='note'>why: {_esc(f['reason'])} · "
                           f"proposed by {_esc(f['proposed_by'])} "
                           f"({_esc(f['source'])})</p>")
                if f.get("cited_by"):
                    out.append(f"<p class='note'>cited by "
                               f"{_esc(', '.join(f['cited_by']))}</p>")
                # THE HISTORY, as dated GESTURES: when, what verb, whose hand,
                # and only the fields that differ from the version before.
                rows = ""
                for g in f.get("history", []):
                    # The changed fields, one per line, and the audience
                    # movement in NAMES: who joined and who left is the half
                    # of a perimeter change a person can actually check.
                    what = "\n".join(f"{k}: {v}" for k, v in g["changed"].items())
                    if g.get("joined"):
                        what += "\njoined: " + ", ".join(g["joined"])
                    if g.get("left"):
                        what += "\nleft: " + ", ".join(g["left"])
                    rows += (f"<tr><td>v{_esc(g['version'])}</td>"
                             f"<td>{_esc(g['timestamp'])}</td>"
                             f"<td>{_esc(g['action'])}</td>"
                             f"<td>{_esc(g['actor'])}</td>"
                             f"<td class='note'><pre>{_esc(what)}</pre>"
                             f"{_esc(g.get('reason') or '')}</td></tr>")
                if rows:
                    out.append("<h2>History</h2><table><thead><tr><th></th>"
                               "<th>when</th><th>what</th><th>hand</th>"
                               "<th>what changed</th>"
                               f"</tr></thead><tbody>{rows}</tbody></table>")
            for missing in data.get("not_found", []):
                out.append(f"<p class='bad'>{_esc(missing)} was never defined in "
                           f"this project — a broken citation, or another "
                           f"project's ID.</p>")
            return "".join(out), f"{name} — {rid}"
        return _read_page(request, render)

    # ---------- the profile: what is FUNDATIVE has no tool ----------
    #
    # The project's brief, its specs and its queue_cap arrive here in 5.0.0,
    # and they arrive from `project_amend`, which stopped carrying them. Not
    # because that tool was insecure — it asked for the admin code — but
    # because these three are what everything else is read against: the brief
    # is the project's identity and the specs are the facts of the day. A chat
    # may SUGGEST the wording; the change is a person's, made at this page.
    #
    # It is the same shape as the lot and the minting, and deliberately not a
    # new one: session, then refusals as sentences.

    # ---------- the people: because they are people ----------
    #
    # A chat and a skill are machinery and a tool manages them. A person is
    # not, and the whole of their row lives here: created, given an address,
    # marked as the one who hears about the proposals, retired. Alfredo,
    # naming it: "i consumer human si gestiscono tutti sulla UI, perché sono
    # human."
    #
    # It calls `prj.amend_project(..., on_the_page=True)` — the SAME method the
    # tool calls, with the flag that lifts the refusal keeping chats away from
    # people. Not a second road: every guard in there (a name is one word, a
    # retired name is still taken, a retirement that would empty a rule) holds
    # unduplicated. A page with its own copy of those rules would be a page
    # with one of them out of step.

    def _people_html(name: str, prj, message: str = "", ok_msg: str = "") -> str:
        people = [c for c in prj.project_info()["consumers"] if c["kind"] == "human"]
        now = prj.approver()
        head = (f"<p class='bad'>{_esc(message)}</p>" if message else "") + \
               (f"<p class='ok'>{_esc(ok_msg)}</p>" if ok_msg else "")
        act = f"/p/{_esc(name)}/people"

        add = (f"<h2>Add a person</h2>"
               f"<form method='post' action='{act}'>"
               f"<input type='hidden' name='action' value='add'>"
               f"<label for='pname'>Their name — ONE WORD, no spaces. It is "
               f"quoted by hand in chat instructions and in scheduled prompts, "
               f"and a space is the character nobody sees when it is "
               f"wrong.</label>"
               f"<input id='pname' name='who' required>"
               f"<p><button type='submit'>Add</button></p></form>"
               f"<p class='note'>A person receives tasks and no rules, and has "
               f"no brief and no specs: they already know who they are and "
               f"what they have to do. Give them an address below, or their "
               f"desk stays silent.</p>")

        if not people:
            return head + add + ("<h2>The post</h2><p class='note'>Nobody yet. "
                                 "Proposals entering the queue notify no one, "
                                 "and are seen by opening the lot page.</p>")

        # A HEADER ROW, and the "nobody" option OUT of the table. The first
        # version had neither: the hint about clearing an address sat in the
        # email column of a fake row whose name was an em dash, so it read as a
        # label belonging to a person called "—", and the third column had no
        # title at all — a bare radio next to the word "proposals", which says
        # what it is only to somebody who already knows.
        #
        # Alfredo, looking at it: "non è chiarissimo". A page nobody can read
        # at a glance is a page where the wrong box gets filled in a hurry, and
        # this one writes addresses.
        rows = "".join(
            f"<tr><td>{_esc(c['name'])}</td>"
            f"<td><input name='email:{_esc(c['name'])}' "
            f"value='{_esc(prj.postbox(c['name'])['email'] if c.get('posted_to') else '')}' "
            f"placeholder='no address — nothing is posted to them'></td>"
            f"<td><input type='radio' name='who' "
            f"value='{_esc(c['name'])}'"
            + (" checked" if now and now["name"] == c["name"] else "")
            + "></td></tr>" for c in people)

        post = (f"<h2>The post</h2>"
                f"<form method='post' action='{act}'>"
                f"<input type='hidden' name='action' value='post'>"
                f"<table><thead><tr><th>Person</th><th>Their email address</th>"
                f"<th>Gets the proposals</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>"
                f"<p><label><input type='radio' name='who' value=''"
                + ("" if now else " checked")
                + "> <b>Nobody</b> gets the proposals — they wait in the queue "
                  "in silence, and are seen by opening the lot page.</label></p>"
                f"<p class='note'>Addresses and the mark are written in ONE "
                f"gesture, because they are one question: who gets an email, "
                f"and where. An address box left EMPTY clears that person's "
                f"address — what this page shows is what gets written. The mark "
                f"GRANTS NOTHING: what opens this page is the password, and "
                f"this only says which desk hears that a proposal is "
                f"waiting.</p>"
                f"<p><button type='submit'>Write</button></p></form>")

        opts = "".join(f"<option value='{_esc(c['name'])}'>{_esc(c['name'])}"
                       "</option>" for c in people)
        gone = (f"<h2>Retire a person</h2>"
                f"<form method='post' action='{act}'>"
                f"<input type='hidden' name='action' value='retire'>"
                f"<select name='who'>{opts}</select>"
                f"<label for='why'>Why — it is the sentence whoever finds the "
                f"dead row in six months will read</label>"
                f"<input id='why' name='reason' required>"
                f"<p><button type='submit'>Retire</button></p></form>"
                f"<p class='note'>The row stays and so does every pointer at "
                f"it: the history reads. A desk with open post on it is "
                f"refused — close those first, or hand them over. And the name "
                f"stays TAKEN, because an ID is never reused.</p>")

        return head + add + post + gone

    async def people_page(request):
        def render(name, prj):
            return _people_html(name, prj), f"{name} — people"
        return _read_page(request, render)

    async def people_action(request):
        """The three gestures of this page, told apart by a hidden field —
        add, post, retire. One route, because they are one subject, and one
        shape each: session, then refusals as sentences."""
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        form = await request.form()

        def say(message="", ok_msg="", status=200):
            return HTMLResponse(
                _page(f"{name} — people", _people_html(name, prj, message, ok_msg),
                      nav=_project_nav(name)), status_code=status)

        what = (form.get("action") or "").strip()
        if what not in ("add", "post", "retire"):
            return say(message="Unknown action. Nothing was written.", status=400)
        try:
            if what == "add":
                v = prj.amend_project(
                    "consumer", (form.get("who") or "").strip(), "create",
                    {"kind": "human"}, actor="web ui", on_the_page=True)
                return say(ok_msg=f"{v['name']} — {v['note']}")
            if what == "retire":
                v = prj.amend_project(
                    "consumer", (form.get("who") or "").strip(), "retire", {},
                    reason=(form.get("reason") or "").strip(), actor="web ui",
                    on_the_page=True)
                return say(ok_msg=f"{v['name']} is retired. {v['note']}")
            # THE WHOLE PICTURE, exactly as the form showed it: every box on
            # the page travels, so what you see is what gets written and a
            # cleared box clears the address. A form that sent only what
            # changed would need the page to know what changed, which is a
            # second opinion about the state.
            addresses = {k.split(":", 1)[1]: (v or "").strip()
                         for k, v in form.items() if k.startswith("email:")}
            v = prj.set_postbox(addresses, (form.get("who") or "").strip())
            said = "; ".join(v["changed"]) if v["changed"] else "no address moved"
            log.info("postbox written on %s: %s · approver %s",
                     name, said, v["approver"])
            return say(ok_msg=f"{said}. {v['note']}")
        except fault:
            raise
        except refusal as e:
            log.info("refused web people: %s", e)
            return say(message=str(e), status=400)

    # ---------- the log: every entry, both ends, and the two gestures --------
    #
    # WHY THIS PAGE EXISTS, and it is not convenience. Until v7.0.0 the only
    # cross view of the log was `tasks_overview` on the MCP surface: one desk
    # per block, pending only, capped, and behind the admin code — so the
    # person who owns the project read their own log through a chat, one desk
    # at a time, and could close an entry only by asking a chat to do it. The
    # two gestures a log needs — close it, correct it — were reachable from
    # everywhere except the page where the person actually is.
    #
    # THE ENTRIES CARRY THEIR BODIES, and nothing here is capped. A page that
    # showed nine of eleven and said so is a page you cannot work from, and an
    # entry you must open to read is an entry you answer from its title.
    #
    # ⚠ WHAT THE PAGE SIGNS. Every gesture from here is signed `web ui`, the
    # same signature the other pages write, and it goes into the history where
    # it stays. It is the honest one: what the history witnessed is that
    # somebody closed this at the admin page, and the project has one person
    # who can be there. A menu of names would let the page write somebody
    # else's signature, which is the one thing a signature must not allow.
    #
    # ⚠ AND IT PASSES `admin=True`, like every other gesture on this page: the
    # engine's own guard is "closing somebody else's task takes the admin
    # code", and the code is what a CHAT presents to prove it is allowed. The
    # person at this page has already presented the password at the door.

    def _tasks_html(name: str, prj, *, by: str = "owner", show: str = "open",
                    query: str = "", message: str = "", ok_msg: str = "") -> str:
        board = prj.task_board(by, show, query)
        act = f"/p/{_esc(name)}/tasks"
        head = ((f"<p class='bad'>{_esc(message)}</p>" if message else "")
                + (f"<p class='ok'>{_esc(ok_msg)}</p>" if ok_msg else ""))
        # The view is a GET form, so a view is a URL: it can be bookmarked, it
        # survives a reload, and the POST that follows carries the same three
        # values back so that acting on an entry leaves you where you were
        # rather than at the top of everything.
        def _opt(val, cur, label):
            return (f"<option value='{_esc(val)}'{' selected' if val == cur else ''}>"
                    f"{_esc(label)}</option>")
        controls = (
            f"<form method='get' action='{act}'>"
            f"<label for='by'>Group by</label>"
            f"<select id='by' name='by'>{_opt('owner', by, 'whose desk it is on')}"
            f"{_opt('sender', by, 'who sent it')}</select>"
            f"<label for='show'>Show</label>"
            f"<select id='show' name='show'>{_opt('open', show, 'open only')}"
            f"{_opt('all', show, 'open and closed')}</select>"
            f"<label for='q'>Containing (blank = everything)</label>"
            f"<input id='q' name='q' value='{_esc(query)}'>"
            f"<p><button type='submit'>Show</button></p></form>")

        def _carry() -> str:
            return (f"<input type='hidden' name='by' value='{_esc(by)}'>"
                    f"<input type='hidden' name='show' value='{_esc(show)}'>"
                    f"<input type='hidden' name='q' value='{_esc(query)}'>")

        people = _consumers(prj)

        def _entry(d: dict) -> str:
            flags = ("<b class='bad'> URGENT</b>" if d.get("urgent") else "") \
                + (f"<span class='note'> · {_esc(d['stale'])}</span>"
                   if d.get("stale") else "")
            who = (f"<p class='note'>{_esc(d['kind'])} · from "
                   f"{_esc(d['created_by'])} · on {_esc(d['owner'])}'s desk · "
                   f"opened {_esc(d['created_at'])}</p>")
            body = f"<p>{_esc(d['body'])}</p>"
            if d["status"] != "pending":
                said = d.get("outcome") or d.get("reason_dropped") or ""
                return (f"<div class='entry'><b>{_esc(d['id'])}</b> "
                        f"{_esc(d['title'])}{flags}{who}{body}"
                        f"<p class='note'>{_esc(d['status'])} on "
                        f"{_esc(d['closed_at'])} — {_esc(said)}</p></div>")
            opts = "".join(
                f"<option value='{_esc(n)}'"
                f"{' selected' if n == d['owner'] else ''}>{_esc(n)}</option>"
                for n in people)
            # TWO FORMS AND NOT ONE WITH TWO BUTTONS, because each needs its own
            # required field: `outcome` completes it and `reason` drops it, the
            # engine takes exactly one of the two, and a single form could not
            # demand the right one. ⚠ There is no undo — closed is closed — so
            # the field that must be filled IS the confirmation, and it is
            # meant to be: a bare button next to an entry is a mis-tap that
            # writes history.
            close = (f"<form method='post' action='{act}'>{_carry()}"
                     f"<input type='hidden' name='id' value='{_esc(d['id'])}'>"
                     f"<input type='hidden' name='action' value='complete'>"
                     f"<input name='outcome' required "
                     f"placeholder='what came of it — this is the closure'>"
                     f"<p><button type='submit'>Complete</button></p></form>"
                     f"<form method='post' action='{act}'>{_carry()}"
                     f"<input type='hidden' name='id' value='{_esc(d['id'])}'>"
                     f"<input type='hidden' name='action' value='drop'>"
                     f"<input name='reason' required "
                     f"placeholder='why it will not be done'>"
                     f"<p><button type='submit'>Drop</button></p></form>")
            amend = (f"<details><summary>Correct it, or hand it to another "
                     f"desk</summary>"
                     f"<form method='post' action='{act}'>{_carry()}"
                     f"<input type='hidden' name='id' value='{_esc(d['id'])}'>"
                     f"<input type='hidden' name='action' value='amend'>"
                     f"<label>Title</label>"
                     f"<input name='title' value='{_esc(d['title'])}'>"
                     f"<label>Body</label>"
                     f"<textarea name='body' rows='4'>{_esc(d['body'])}</textarea>"
                     f"<label>Desk</label>"
                     f"<select name='consumer'>{opts}</select>"
                     f"<p><button type='submit'>Write the correction</button></p>"
                     f"</form>"
                     f"<p class='note'>Only what differs is written, and the "
                     f"hand-over is named in the history, which keeps both "
                     f"desks. Urgency is not here on purpose: it belongs to "
                     f"whoever opened the entry.</p></details>")
            return (f"<div class='entry'><b>{_esc(d['id'])}</b> "
                    f"{_esc(d['title'])}{flags}{who}{body}{close}{amend}</div>")

        blocks = "".join(
            f"<h2>{_esc(g['group'])} — {_esc(g['open'])} open"
            + (f", {_esc(g['closed'])} closed" if g["closed"] else "") + "</h2>"
            + "".join(_entry(d) for d in g["entries"])
            for g in board["groups"])
        if not board["groups"]:
            blocks = ("<p class='note'>Nothing to show. With <i>open only</i> "
                      "that is the good outcome: no desk owes anything.</p>")
        tally = (f"<p class='note'>{_esc(board['count'])} shown — "
                 f"{_esc(board['open'])} open, {_esc(board['closed'])} closed — "
                 f"grouped by {_esc(board['group_by'])}. Nothing here is "
                 f"capped: this is the whole log, minus what the prune "
                 f"archived.</p>")
        return head + controls + tally + blocks

    async def tasks_page(request):
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        q = request.query_params
        by = (q.get("by") or "owner").strip()
        show = (q.get("show") or "open").strip()
        query = (q.get("q") or "").strip()
        try:
            body = _tasks_html(name, prj, by=by, show=show, query=query)
        except fault:
            raise
        except refusal as e:
            log.info("refused web tasks: %s", e)
            body = _tasks_html(name, prj, message=str(e))
        response = HTMLResponse(_page(f"{name} — the log", body,
                                      nav=_project_nav(name)))
        _issue(response)
        return response

    async def tasks_action(request):
        """Close an entry, drop it, or correct it — the three gestures of a
        log, told apart by a hidden field, all three landing back on the view
        they were made from."""
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        form = await request.form()
        by = (form.get("by") or "owner").strip()
        show = (form.get("show") or "open").strip()
        query = (form.get("q") or "").strip()
        tid = (form.get("id") or "").strip()

        def say(message="", ok_msg="", status=200):
            return HTMLResponse(
                _page(f"{name} — the log",
                      _tasks_html(name, prj, by=by, show=show, query=query,
                                  message=message, ok_msg=ok_msg),
                      nav=_project_nav(name)), status_code=status)

        what = (form.get("action") or "").strip()
        if what not in ("complete", "drop", "amend"):
            return say(message="Unknown action. Nothing was written.", status=400)
        try:
            if what == "amend":
                v = prj.task_amend(tid, WEB_SIGNATURE,
                                   title=(form.get("title") or "").strip(),
                                   body=(form.get("body") or "").strip(),
                                   consumer=(form.get("consumer") or "").strip(),
                                   admin=True)
                moved = (f" — handed over from {v['reassigned_from']}"
                         if v.get("reassigned_from") else "")
                log.info("task amended on the page: %s%s", v["id"], moved)
                return say(ok_msg=f"{v['id']} corrected, on {v['owner']}'s "
                                  f"desk{moved}.")
            v = prj.task_close(
                tid, WEB_SIGNATURE,
                outcome=(form.get("outcome") or "").strip() if what == "complete" else "",
                reason=(form.get("reason") or "").strip() if what == "drop" else "",
                admin=True)
            log.info("task closed on the page: %s — %s", v["id"], v["status"])
            return say(ok_msg=f"{v['id']} is {v['status']}: "
                              f"{v['outcome'] or v['reason']}")
        except fault:
            raise
        except refusal as e:
            log.info("refused web tasks: %s", e)
            return say(message=str(e), status=400)

    # ---------- the closing link: the one page that has no session ----------
    #
    # WHAT IT IS. The mail that says a task landed on a person's desk carries a
    # button; this is where it lands. It asks for no password — the ticket in
    # the URL is the credential — and it can close exactly the one entry it
    # names, with the words the person types here.
    #
    # ⚠ WHY THAT IS ACCEPTABLE, and it is a PREMISE rather than a detail. The
    # ticket travels in cleartext through a mail relay run by somebody else, so
    # it is worth precisely as much as reaching this port is: the UI does not
    # answer outside the tailnet, and the Funnel cannot publish this port —
    # the preflight refuses the three it could. The day that changes, this page
    # is a hole and has to be taken out or given a second factor.
    #
    # ⚠ AND IT IS SINGLE-USE WITHOUT BEING SINGLE-USE. Nothing here spends a
    # ticket, because nothing has to: `closed is closed`, so the second visit
    # finds the entry closed and gets the refusal the engine already has. A
    # reusable ticket for an object that accepts one gesture is single-use in
    # fact — which is what let this be built with no table, and therefore with
    # no schema generation, on a register that is loaded.
    #
    # The GET is a form and not a gesture: a link that closed something by
    # being FETCHED would be closed by the first mail client that prefetches
    # links, and nobody would ever know which one.

    def _ticket_html(name: str, prj, tid: str, token: str, seen: dict, *,
                     message: str = "") -> str:
        act = f"/p/{_esc(name)}/t/{_esc(tid)}"
        head = f"<p class='bad'>{_esc(message)}</p>" if message else ""
        if seen["status"] != "pending":
            return (head + f"<p class='ok'>{_esc(seen['id'])} — "
                    f"{_esc(seen['title'])}</p>"
                    f"<p class='note'>Already closed. Closed is closed: it is "
                    f"not reopened and not amended. If the work came back, open "
                    f"a new entry and cite this one.</p>")
        return (head
                + f"<div class='entry'><b>{_esc(seen['id'])}</b> "
                  f"{_esc(seen['title'])}"
                  f"<p class='note'>{_esc(seen['kind'])} · on "
                  f"{_esc(seen['owner'])}'s desk</p>"
                  f"<p>{_esc(seen['body'])}</p></div>"
                + f"<form method='post' action='{act}'>"
                  f"<input type='hidden' name='k' value='{_esc(token)}'>"
                  f"<input type='hidden' name='action' value='complete'>"
                  f"<label for='out'>What came of it</label>"
                  f"<input id='out' name='outcome' required autofocus>"
                  f"<p><button type='submit'>Complete it</button></p></form>"
                + f"<form method='post' action='{act}'>"
                  f"<input type='hidden' name='k' value='{_esc(token)}'>"
                  f"<input type='hidden' name='action' value='drop'>"
                  f"<label for='why'>Or why it will not be done</label>"
                  f"<input id='why' name='reason' required>"
                  f"<p><button type='submit'>Drop it</button></p></form>"
                + f"<p class='note'>There is no undo — the words you type ARE "
                  f"the confirmation. It is signed {_esc(seen['owner'])}, "
                  f"because this link was sent to that desk.</p>")

    def _ticket(request, prj, name: str, token: str):
        """Resolve the ticket or answer with the refusal, as a page. Returns
        (seen, None) or (None, response), so the two callers cannot each grow
        their own idea of what a bad ticket looks like."""
        tid = request.path_params["task"]
        try:
            return prj.check_task_link(tid, token), None
        except fault:
            raise
        except refusal as e:
            log.info("refused a task link on %s: %s", name, e)
            return None, HTMLResponse(
                _page(f"{name} — the entry",
                      f"<p class='bad'>{_esc(e)}</p>", nav=""),
                status_code=403)

    async def ticket_page(request):
        # NO SESSION HERE, ON PURPOSE — see the block above. `test_surface`
        # pins this exception by name, so removing the guard from a page is a
        # decision somebody takes rather than a line that drifts.
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        token = (request.query_params.get("k") or "").strip()
        seen, refused = _ticket(request, prj, name, token)
        if refused is not None:
            return refused
        # NAV EMPTY, and it is not a decoration missing. Whoever arrives here
        # arrived from an inbox with a ticket for ONE entry; a menu would offer
        # them the rules, the people and the lot, every one of which would then
        # answer with the login page. A door that shows doors it will not open
        # is a door that looks broken.
        return HTMLResponse(_page(f"{name} — {seen['id']}",
                                  _ticket_html(name, prj, seen["id"], token, seen),
                                  nav=""))

    async def ticket_action(request):
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        form = await request.form()
        token = (form.get("k") or "").strip()
        seen, refused = _ticket(request, prj, name, token)
        if refused is not None:
            return refused
        what = (form.get("action") or "").strip()
        if what not in ("complete", "drop"):
            return HTMLResponse(
                _page(f"{name} — {seen['id']}",
                      _ticket_html(name, prj, seen["id"], token, seen,
                                   message="Unknown action. Nothing was written."),
                      nav=""), status_code=400)
        try:
            # SIGNED WITH THE DESK'S OWN NAME, and that is what the link is:
            # it was posted to that desk and to no other, so the closure is
            # theirs. It also means the engine's ordinary guard passes on its
            # own merits — the owner may always close their own entry — rather
            # than this page reaching for `admin=True`, which would make a
            # ticket in an inbox worth an administrator.
            v = prj.task_close(
                seen["id"], seen["owner"],
                outcome=(form.get("outcome") or "").strip() if what == "complete" else "",
                reason=(form.get("reason") or "").strip() if what == "drop" else "")
        except fault:
            raise
        except refusal as e:
            log.info("refused a task link closure on %s: %s", name, e)
            return HTMLResponse(
                _page(f"{name} — {seen['id']}",
                      _ticket_html(name, prj, seen["id"], token, seen,
                                   message=str(e)), nav=""), status_code=400)
        log.info("task closed from a link: %s — %s", v["id"], v["status"])
        return HTMLResponse(_page(
            f"{name} — {v['id']}",
            f"<p class='ok'>{_esc(v['id'])} is {_esc(v['status'])}.</p>"
            f"<p>{_esc(v['outcome'] or v['reason'])}</p>"
            f"<p class='note'>Signed {_esc(v['by'])}. Nothing else on this "
            f"register is reachable from here, and this link is now spent: "
            f"closed is closed.</p>", nav=""))

    def _profile_html(name: str, prj, message: str = "", ok_msg: str = "") -> str:
        prof = prj.profile()
        cap = prof["queue_cap"]
        head = (f"<p class='bad'>{_esc(message)}</p>" if message else "") + \
               (f"<p class='ok'>{_esc(ok_msg)}</p>" if ok_msg else "")
        return (head
                + f"<form method='post' action='/p/{_esc(name)}/profile'>"
                  "<label for='brief'>Brief — the project's identity: whose it "
                  "is, how it works, what it is for. It leads every "
                  "<code>rules_list</code>, so it is read at the top of every "
                  "chat of this project.</label>"
                  f"<textarea id='brief' name='brief' rows='10'>"
                  f"{_esc(prof['brief'] or '')}</textarea>"
                  "<label for='specs'>Specs — the living facts. True today and "
                  "false tomorrow without anybody having decided anything, "
                  "which is why they are a second field and not more of the "
                  "brief.</label>"
                  f"<textarea id='specs' name='specs' rows='10'>"
                  f"{_esc(prof['specs'] or '')}</textarea>"
                  "<label for='cap'>Queue ceiling — blank is unlimited, 0 "
                  "closes the queue to new proposals, N is N. It is also the "
                  "ceiling on one turn of the lot page: at the twelfth "
                  "signature in a row a person signs without reading.</label>"
                  f"<input id='cap' name='queue_cap' value="
                  f"'{_esc('' if cap is None else cap)}'>"
                  "<p><button type='submit'>Write the profile</button></p>"
                  "</form>"
                  "<p class='note'>Citations are checked here like everywhere "
                  "else: an ID in round brackets has to resolve to a rule in "
                  "force, or nothing is written. There is no tool for this "
                  "page, and that is the decision — what is fundative has "
                  "none, the way what is catastrophic has none.</p>"
                + (f"<p class='note'>Last written {_esc(prof['updated_at'])}.</p>"
                   if prof["updated_at"] else
                   "<p class='note'>Never written: this project has no profile "
                   "yet, which is a legitimate state and not a fault.</p>"))

    async def profile_page(request):
        def render(name, prj):
            return _profile_html(name, prj), f"{name} — profile"
        return _read_page(request, render)

    async def profile_action(request):
        """The write half. `queue_cap` blank means UNLIMITED here and not
        `leave it`, because this form always carries all three fields: what the
        page shows is what gets written, or a form would silently keep a value
        the person had just cleared."""
        if not _session_ok(request):
            return _guest(request)
        name = request.path_params["project"]
        prj = _open(name)
        if prj is None:
            return _no_project(name)
        form = await request.form()
        raw = (form.get("queue_cap") or "").strip()
        if raw and not raw.isdigit():
            return HTMLResponse(
                _page(f"{name} — profile",
                      _profile_html(name, prj, message=(
                          f"{raw!r} is not a whole number: blank is unlimited, "
                          f"0 closes the queue, N is N. Nothing was written.")),
                      nav=_project_nav(name)), status_code=400)
        try:
            v = prj.set_profile(brief=form.get("brief") or "",
                                specs=form.get("specs") or "",
                                queue_cap=int(raw) if raw else None)
        except fault:
            raise
        except refusal as e:
            log.info("refused web profile: %s", e)
            return HTMLResponse(
                _page(f"{name} — profile",
                      _profile_html(name, prj, message=str(e)),
                      nav=_project_nav(name)), status_code=400)
        said = ", ".join(v["changed"]) if v["changed"] else "nothing"
        log.info("profile written on %s: %s", name, said)
        return HTMLResponse(_page(
            f"{name} — profile",
            _profile_html(name, prj, ok_msg=f"Written: {said}. Every chat of "
                                            f"this project reads it from the "
                                            f"next rules_list."),
            nav=_project_nav(name)))

    async def status_page(request):
        def render(name, prj):
            st = prj.status()
            counts = "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>"
                             for k, v in st["counted"].items())
            out = [f"<h2>Counted</h2><table><tbody>{counts}</tbody></table>",
                   f"<p class='note'>Queue ceiling: "
                   f"{_esc('none' if st['queue_cap'] is None else st['queue_cap'])}"
                   f" — policy of this project, changed with the admin code and "
                   f"a one-time code.</p>"]

            def findings(title, rows, render_row, empty="Nothing."):
                if not rows:
                    return f"<h2>{title}</h2><p class='note'>{empty}</p>"
                items = "".join(f"<li>{render_row(r)}</li>" for r in rows)
                return f"<h2>{title}</h2><ul>{items}</ul>"

            # THE RETIRED, and this page is the only place they are readable:
            # `project_info` shows the live alone, on purpose, but a retired
            # name is still a name TAKEN — a create is refused pointing at
            # something the caller cannot see, and a revive needs a target.
            ret = st["retired"]
            retired_rows = ([f"domain {_esc(r['code'])} — {_esc(r['reason'])}"
                             for r in ret["domains"]]
                            + [f"consumer {_esc(r['name'])} ({_esc(r['kind'])}) — "
                               f"{_esc(r['reason'])}" for r in ret["consumers"]]
                            + [f"group {_esc(r['name'])} — {_esc(r['reason'])}"
                               for r in ret["groups"]])
            out.append(findings("Retired — the names still taken", retired_rows,
                                lambda r: r, "None: every name is free."))
            out.append(findings(
                "Citations pointing nowhere", st["dangling_citations"],
                lambda r: (f"{_esc(r['in'])} ({_esc(r['field'])}) cites "
                           f"{_esc(r['cites'])} — {_esc(r['state'])}")))
            out.append(findings(
                "Overlaps that formed later", st["overlaps"],
                lambda r: f"{_esc(r['rule'])} · {_esc(r['consumer'])} — {_esc(r['note'])}"))
            out.append(findings(
                "Stray audience rows", st["stray_audience_rows"],
                lambda r: f"{_esc(r['rule'])} — {_esc(r['rows'])} rows: {_esc(r['note'])}"))
            out.append(findings("Domains with no rule", st["domains_with_no_rules"],
                                lambda r: _esc(r)))
            out.append(findings("Consumers no rule reaches",
                                st["consumers_no_rule_reaches"], lambda r: _esc(r)))
            out.append(f"<p class='note'>{_esc(st['note'])}</p>")
            return "".join(out), f"{name} — state"
        return _read_page(request, render)

    routes = [
        Route("/", home, methods=["GET"]),
        Route("/login", login, methods=["POST"]),
        Route("/logout", logout, methods=["POST"]),
        Route("/maintenance", maintenance_page, methods=["GET"]),
        Route("/p/{project}/", project_home, methods=["GET"]),
        Route("/p/{project}/backup", project_backup, methods=["POST"]),
        Route("/p/{project}/batch", batch_page, methods=["GET"]),
        Route("/p/{project}/batch", batch_action, methods=["POST"]),
        Route("/p/{project}/codes", codes_page, methods=["GET"]),
        Route("/p/{project}/codes", codes_mint, methods=["POST"]),
        Route("/p/{project}/profile", profile_page, methods=["GET"]),
        Route("/p/{project}/profile", profile_action, methods=["POST"]),
        Route("/p/{project}/people", people_page, methods=["GET"]),
        Route("/p/{project}/people", people_action, methods=["POST"]),
        Route("/p/{project}/rules", rules_page, methods=["GET"]),
        Route("/p/{project}/rule/{rule}", rule_page, methods=["GET"]),
        Route("/p/{project}/status", status_page, methods=["GET"]),
        Route("/p/{project}/tasks", tasks_page, methods=["GET"]),
        Route("/p/{project}/tasks", tasks_action, methods=["POST"]),
        Route("/p/{project}/t/{task}", ticket_page, methods=["GET"]),
        Route("/p/{project}/t/{task}", ticket_action, methods=["POST"]),
    ]
    return Starlette(routes=routes)
