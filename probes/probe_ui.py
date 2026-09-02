"""A LIVE probe of the admin UI: no suite renders HTML, so the pages are
driven with a real ASGI transport and read back as text.

Run it from the bench, with the engine on PYTHONPATH:
    python probe_ui.py
"""
import asyncio
import logging
import os
import sys
import tempfile

import httpx

# ⚠ The probes live in probes/ and drive the code in the repository ROOT. When
# a script runs from a subdirectory, `sys.path[0]` is THAT subdirectory, not
# the working directory — so `import rules` would fail no matter where it was
# launched from. The root goes on the path explicitly, and the probe finds the
# same modules the suites import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rules
import web

REF = "reference0000001"
ADM = "adminadmin00001"
MASTER = "a-long-enough-password"

_failed = []


def ok(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        _failed.append(name)


async def main():
    root = tempfile.mkdtemp(prefix="probe-ui-")
    with open(os.path.join(root, rules.REGISTRY_FILE), "w", encoding="utf-8") as fh:
        fh.write(f"Palestra | {REF} | {ADM}\n")
    log = logging.getLogger("probe")
    registry = rules.Registry(root)
    app = web.build(registry=registry, log=log, master=MASTER,
                    refusal=rules.RulesError, fault=rules.RulesFault,
                    backup_dir=root)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://probe") as c:
        r = await c.get("/")
        ok("no session: the door, not the projects", r.status_code == 401
           and "Sign in" in r.text)
        ok("and the door says the password is typed once",
           "not again" in r.text and "Eight hours" in r.text, r.text[-300:])

        r = await c.post("/login", data={"master": "wrong"})
        ok("a wrong master is refused", r.status_code == 401
           and "Wrong master" in r.text)

        r = await c.post("/login", data={"master": MASTER},
                         follow_redirects=True)
        ok("the right one opens the session", r.status_code == 200
           and "Palestra" in r.text, r.text[:200])

        # ---- every writing page, WITHOUT a master anywhere in the form ----
        r = await c.get("/p/Palestra/profile")
        ok("the profile page carries no password field",
           r.status_code == 200 and "type='password'" not in r.text
           and 'name="master"' not in r.text)
        r = await c.post("/p/Palestra/profile",
                         data={"brief": "the gym", "specs": "two rules",
                               "queue_cap": "10"})
        ok("and it writes with the session alone", r.status_code == 200
           and ("written" in r.text.lower() or "ok" in r.text.lower()),
           r.text[:300])
        r = await c.get("/p/Palestra/profile")
        ok("the brief is really there", "the gym" in r.text)

        # ---- THE ANAGRAFICA: every consumer, and the whole of one ----
        r = await c.get("/p/Palestra/consumers")
        ok("the consumers page carries no password field",
           "type='password'" not in r.text)
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "create", "who": "Alfredo",
                               "kind": "human"})
        ok("a person is added with the session alone",
           r.status_code == 200 and "Alfredo" in r.text, r.text[:300])
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "post", "who": "Alfredo",
                               "email": "a@example.com"})
        ok("and their post is written from their own card", r.status_code == 200
           and "example.com" in r.text, r.text[:300])

        # ---- THE TWO WAYS IN, and the card that names its subject ----
        r = await c.get("/p/Palestra/consumers")
        ok("the register is a table you can read at a glance",
           "<th>Name</th>" in r.text and "Add a consumer" in r.text
           and "Open its card" in r.text, r.text[:300])
        ok("with no card open until one is asked for",
           "class='veil'" not in r.text)
        ok("and it says at the top who hears about proposals",
           "Nobody is marked" in r.text or "announced to" in r.text)
        r = await c.get("/p/Palestra/consumers?new=1")
        # ⚠ `>Add a consumer<` and not `Add a consumer</h2>`: the heading
        # carries the close link inside it, so the tag does not follow the
        # words. A check written against markup it did not look at fails on a
        # page that is right — which is how a probe teaches you to distrust it.
        ok("the + button opens the creation card",
           "class='veil'" in r.text and ">Add a consumer<" in r.text
           and "cannot be changed afterwards" in r.text,
           (str(r.request.url), "veil" in r.text))
        r = await c.get("/p/Palestra/consumers?edit=Alfredo")
        ok("and the menu opens ONE consumer's card, named in its title",
           "class='veil'" in r.text and ">Alfredo — human<" in r.text,
           (str(r.request.url), "veil" in r.text))
        ok("a person's card carries their address and the proposals tick, in "
           "words — and no brief, which they cannot have",
           "Email address" in r.text and "proposal</b> is waiting" in r.text
           and "Save the post" in r.text and "no brief and no specs" in r.text,
           r.text[:300])

        # ⚠ THE MARK IS CARRIED FORWARD BY A CARD THAT DOES NOT MENTION IT.
        # `set_postbox` clears and re-sets the approver in one transaction, so
        # saving one person's address used to be able to un-mark another.
        _prj4 = registry.by_name("Palestra")
        await c.post("/p/Palestra/consumers",
                     data={"action": "create", "who": "Marta", "kind": "human"})
        await c.post("/p/Palestra/consumers",
                     data={"action": "post", "who": "Alfredo",
                           "email": "a@example.com", "approver": "on"})
        ok("ticking it on one card marks that person",
           (_prj4.approver() or {}).get("name") == "Alfredo", _prj4.approver())
        await c.post("/p/Palestra/consumers",
                     data={"action": "post", "who": "Marta",
                           "email": "m@example.com"})
        ok("and saving ANOTHER person's address leaves the mark where it was",
           (_prj4.approver() or {}).get("name") == "Alfredo", _prj4.approver())
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "post", "who": "Marta",
                               "email": "m@example.com", "approver": "on"})
        ok("ticking it elsewhere MOVES it, because there is at most one",
           (_prj4.approver() or {}).get("name") == "Marta", _prj4.approver())
        await c.post("/p/Palestra/consumers",
                     data={"action": "post", "who": "Marta",
                           "email": "m@example.com"})
        ok("and unticking it on the person who holds it clears it",
           _prj4.approver() is None, _prj4.approver())
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "post", "who": "Marta", "email": ""})
        ok("an emptied address clears that person's, and says so",
           r.status_code == 200 and "no address" in r.text, r.text[:300])

        # A CHAT, WITH A MANDATE — the half the old page could not do at all.
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "create", "who": "Advisory",
                               "kind": "chat",
                               "brief": "# Who\n\nThe **advisor**."})
        ok("a chat is created from the page, with a brief",
           r.status_code == 200 and "Advisory" in r.text, r.text[:300])
        # ⚠ The raw markdown IS on the page — inside the textarea of the
        # editor, which is where it belongs. So what this looks at is the
        # RENDERED half only: asking for "no asterisks anywhere" failed on a
        # page that is correct, which is the shape of a probe that teaches you
        # to ignore it.
        _shown = r.text.split("<div class='md'>")[1].split("</div>")[0] \
            if "<div class='md'>" in r.text else ""
        ok("and the brief comes back RENDERED, not as raw markdown",
           "<b>advisor</b>" in _shown and "**" not in _shown, _shown[:200])
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "amend", "who": "Advisory",
                               "newname": "Advisory",
                               "brief": "# Who\n\nThe **advisor**.",
                               "specs": "- one\n- two"})
        ok("its specs are written from the page", r.status_code == 200
           and "<li>one</li>" in r.text, r.text[:300])
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "amend", "who": "Advisory",
                               "newname": "Advisory",
                               "brief": "# Who\n\nThe **advisor**.",
                               "specs": "- one\n- two"})
        ok("pressing Write with nothing changed is a sentence, not a refusal "
           "that reads like a fault",
           r.status_code == 400 and "already was" in r.text, r.text[:300])
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "amend", "who": "Advisory",
                               "newname": "Adviser",
                               "brief": "# Who\n\nThe **advisor**.",
                               "specs": "- one\n- two"})
        ok("renaming keeps the row and its history",
           r.status_code == 200 and "Adviser" in r.text, r.text[:300])
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "retire", "who": "Adviser",
                               "reason": "the gym has no advisor"})
        ok("a consumer is retired — never deleted", r.status_code == 200
           and "retired" in r.text.lower(), r.text[:300])
        ok("and it is still on the page, under Retired",
           "Adviser" in r.text and "Retired —" in r.text)
        r = await c.get("/p/Palestra/consumers?edit=Adviser")
        ok("with its reason, and the way back, on its own card",
           "no advisor" in r.text and "Bring it back" in r.text, r.text[:300])
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "retire", "who": "Adviser",
                               "reason": "again"})
        ok("retiring a retired one is refused in words",
           r.status_code == 400, r.text[:200])
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "revive", "who": "Adviser"})
        ok("and it is brought back from the same page", r.status_code == 200
           and "is back" in r.text, r.text[:300])
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "create", "who": "two words",
                               "kind": "chat"})
        ok("a name with a space is refused, in the engine's own words",
           r.status_code == 400 and "one word" in r.text.lower(), r.text[:300])
        r = await c.get("/p/Palestra/consumers")
        ok("the register lists every consumer with its counts",
           "<th class='num'>Rules</th>" in r.text
           and "<th class='num'>Open</th>" in r.text, r.text[:300])

        # ---- THE RULES PAGE, ON ALL THREE KINDS ----
        # ⚠ THIS BLOCK EXISTS BECAUSE OF A 500 IN PRODUCTION. `list_rules` has
        # TWO payload shapes — for a SKILL the project's profile comes back as
        # {withheld, note} with no `brief` key — and the page read
        # profile["brief"] unconditionally. Picking any skill returned Internal
        # Server Error, and had done for versions: the suites read prose, and
        # the probes drove the pages that were NEW. A page nobody drives is a
        # page nobody has tried.
        _prj3 = registry.by_name("Palestra")
        _prj3.set_profile(brief="the gym", specs="two rules", actor="web ui")
        for _n, _k in (("Deliberator", "chat"), ("Executor", "skill")):
            _prj3.amend_project("consumer", _n, "create",
                                {"kind": _k, "brief": f"the {_k}"},
                                actor="web ui", on_the_page=True)
        # THE PAGE READS THE PROJECT ONCE. Until 7.1.1 the menu and the pick
        # each asked `project_info` for the same list, so every rules page
        # was two readings of the anagrafica — counted here on the engine's
        # own method, because a page that grows a third reading would not
        # look any different.
        _reads = []
        _orig_info = rules.Project.project_info
        rules.Project.project_info = lambda self_, *a, **k: (
            _reads.append(1), _orig_info(self_, *a, **k))[1]
        try:
            r = await c.get("/p/Palestra/rules?consumer=Deliberator")
        finally:
            rules.Project.project_info = _orig_info
        ok("the rules page reads the anagrafica ONCE, not once per widget",
           len(_reads) == 1, f"project_info called {len(_reads)} times")
        ok("the rules page opens for a CHAT, with the project's brief",
           r.status_code == 200 and "the gym" in r.text, r.status_code)
        r = await c.get("/p/Palestra/rules?consumer=Executor")
        ok("and for a SKILL — the shape that used to be a 500",
           r.status_code == 200, r.status_code)
        ok("which says the profile is WITHHELD, in the engine's own words, "
           "instead of showing a project brief a skill must not receive",
           "No project brief here" in r.text and "runs one job" in r.text
           and "the gym" not in r.text, r.text[:400])
        ok("and it still shows the skill's OWN mandate, which is not withheld",
           "the skill" in r.text, r.text[:400])
        r = await c.get("/p/Palestra/rules")
        ok("with no consumer named, the page opens on one it can READ — never "
           "on a person, whose rules_list is refused",
           r.status_code == 200 and "not an audience" not in r.text,
           r.text[:300])
        ok("and the menu offers no person at all: a choice whose only outcome "
           "is a refusal is not a choice",
           "<option value='Alfredo'" not in r.text,
           # ⚠ The detail is the OPTIONS, not the page: handing r.text to a
           # failure prints the whole stylesheet and buries the one line that
           # says what went wrong.
           [f"<option{x[:40]}" for x in r.text.split("<option")[1:6]])
        r = await c.get("/p/Palestra/rules?consumer=Alfredo")
        ok("asked for a person by hand it shows somebody it CAN read — and "
           "SAYS so, because a silent substitution is a page believed about "
           "the wrong subject",
           r.status_code == 200 and "is a person" in r.text
           and "Showing" in r.text, r.text[:400])

        r = await c.get("/p/Palestra/codes")
        ok("the codes page carries no password field",
           "type='password'" not in r.text)
        r = await c.post("/p/Palestra/codes", data={"minutes": "5"})
        ok("a one-time code is minted with the session alone",
           r.status_code == 200 and "Copy it now" in r.text, r.text[:300])

        # ---- the run: press the button three times, copy once ----
        import re as _re

        def _run_field(text):
            m = _re.search(r"name='run' value='([^']*)'", text)
            return m.group(1) if m else None

        def _printed(text):
            m = _re.search(r"<code class='run'>([^<]*)</code>", text)
            return m.group(1) if m else None

        first = _run_field(r.text)
        ok("the first code is carried in the hidden field",
           first and len(first.split()) == 1, first)
        r2 = await c.post("/p/Palestra/codes", data={"minutes": "5", "run": first})
        second = _run_field(r2.text)
        r3 = await c.post("/p/Palestra/codes", data={"minutes": "5", "run": second})
        third = _run_field(r3.text)
        ok("three presses, three codes on the line",
           third and len(third.split()) == 3, third)
        ok("and they are all different",
           third and len(set(third.split())) == 3, third)
        printed = _printed(r3.text)
        ok("printed separated by TWO spaces, so one drag takes the lot",
           printed == "  ".join(third.split()), repr(printed))
        ok("and the page says so in the plural",
           "All 3 of them, in one drag" in r3.text, r3.text[:400])
        r4 = await c.get("/p/Palestra/codes")
        ok("a fresh GET starts an empty line — nothing is kept server-side",
           _printed(r4.text) is None and _run_field(r4.text) == "",
           _run_field(r4.text))
        r5 = await c.post("/p/Palestra/codes",
                          data={"minutes": "nonsense", "run": third})
        ok("a bad number keeps the line and mints nothing",
           r5.status_code == 400 and _run_field(r5.text) == third,
           _run_field(r5.text))
        # THE CODES ON THE LINE ARE REAL, and this is the half a page test
        # cannot fake: each one is looked up in the database it was minted in.
        # `_auth_row` raises on anything that is not live and unspent, so
        # three silent returns is three usable codes.
        _prj = registry.by_name("Palestra")
        _live = True
        for _c in third.split():
            try:
                rules._auth_row(_prj, _c)
            except Exception as exc:          # noqa: BLE001 - the probe reports it
                _live = False
                print(f"        {_c}: {exc}")
        ok("every code on the line is live in the database it was minted in",
           _live, third)

        r = await c.get("/p/Palestra/batch")
        ok("the lot page carries no password field",
           "type='password'" not in r.text)

        # ---- THE LOG: every entry, both ends, and the two gestures ----
        _prj2 = registry.by_name("Palestra")
        _prj2.amend_project("consumer", "Coach", "create", {"kind": "chat"},
                            actor="probe", on_the_page=True)
        _prj2.amend_project("consumer", "Runner", "create", {"kind": "skill"},
                            actor="probe", on_the_page=True)
        a = _prj2.task_add("Coach", "the squat rack wobbles",
                           "third bolt on the left upright", "Runner")
        b = _prj2.task_add("Runner", "log yesterday's session",
                           "five sets, the last one short", "Coach", urgent=True)
        d = _prj2.task_add("Coach", "book the physio",
                           "the one on the high street", "Alfredo")

        r = await c.get("/p/Palestra/tasks")
        ok("the log page opens", r.status_code == 200 and "the log" in r.text.lower())
        ok("and it shows every open entry, bodies and all",
           all(x in r.text for x in (a["id"], b["id"], d["id"],
                                     "third bolt on the left upright")),
           r.status_code)
        ok("grouped by desk by default: Coach carries two",
           r.text.index("Coach — 2 open") > 0, "no such heading")
        ok("and the urgent one is marked", "URGENT" in r.text)

        r = await c.get("/p/Palestra/tasks?by=sender")
        ok("grouped by sender instead", "Runner — 1 open" in r.text
           and "Alfredo — 1 open" in r.text, r.text[:200])

        r = await c.get("/p/Palestra/tasks?q=physio")
        ok("and filtered by what is in them",
           d["id"] in r.text and a["id"] not in r.text)

        # closing one, from the page, with the session alone
        r = await c.post("/p/Palestra/tasks",
                         data={"id": a["id"], "action": "complete",
                               "outcome": "bolt tightened, rack solid",
                               "by": "owner", "show": "open", "q": ""})
        ok("an entry is completed from the page", r.status_code == 200
           and "completed" in r.text and "bolt tightened" in r.text, r.text[:300])
        ok("and it leaves the open view", a["id"] not in r.text.split("<h2>")[-1]
           or "Coach — 1 open" in r.text, "still listed as open")

        r = await c.get("/p/Palestra/tasks?show=all")
        ok("but it is there when the closed are shown",
           a["id"] in r.text and "bolt tightened" in r.text)

        r = await c.post("/p/Palestra/tasks",
                         data={"id": b["id"], "action": "drop",
                               "reason": "the session was never finished",
                               "by": "owner", "show": "open", "q": ""})
        ok("an entry is dropped, with its reason", r.status_code == 200
           and "dropped" in r.text and "never finished" in r.text, r.text[:300])

        r = await c.post("/p/Palestra/tasks",
                         data={"id": d["id"], "action": "amend",
                               "title": "book the physio for Tuesday",
                               "body": "the one on the high street",
                               "consumer": "Runner",
                               "by": "owner", "show": "open", "q": ""})
        ok("and one is corrected and handed to another desk",
           r.status_code == 200 and "corrected" in r.text
           and "Runner" in r.text, r.text[:300])
        got = _prj2.task_get([d["id"]])["tasks"][0]
        ok("the correction really landed in the database",
           got["title"] == "book the physio for Tuesday"
           and got["owner"] == "Runner", got)

        r = await c.post("/p/Palestra/tasks",
                         data={"id": a["id"], "action": "complete",
                               "outcome": "again", "by": "owner",
                               "show": "open", "q": ""})
        ok("closing a closed entry is refused, in words",
           r.status_code == 400 and "closed is closed" in r.text, r.text[:300])
        r = await c.post("/p/Palestra/tasks",
                         data={"id": "TK-9999", "action": "complete",
                               "outcome": "x", "by": "owner", "show": "open",
                               "q": ""})
        ok("and so is an entry that does not exist",
           r.status_code == 400 and "no such task" in r.text, r.text[:300])
        r = await c.get("/p/Palestra/tasks?by=nonsense")
        # ⚠ The apostrophes come back as &#x27;, because every value that
        # reaches a page goes through `_esc` — including a refusal. Looking for
        # the raw sentence here would have failed on a page that is CORRECT,
        # which is the shape of a probe that teaches you to ignore it.
        ok("a group_by nobody asked for is a sentence, not a stack trace",
           r.status_code == 200 and "group_by is" in r.text
           and "&#x27;owner&#x27; or &#x27;sender&#x27;" in r.text,
           [x for x in r.text.split("<p") if "group_by" in x][:1])

        r = await c.post("/p/Palestra/backup", data={})
        ok("the backup still works", r.status_code == 200
           and "Quiescent" in r.text, r.text[:200])

        # ---- and the door is still a door ----
        c.cookies.clear()
        for path in ("/p/Palestra/profile", "/p/Palestra/consumers",
                     "/p/Palestra/codes", "/p/Palestra/batch",
                     "/p/Palestra/tasks"):
            r = await c.get(path)
            ok(f"without the cookie {path} is the login page",
               r.status_code == 401, r.status_code)
        r = await c.post("/p/Palestra/consumers",
                         data={"action": "create", "who": "Nobody"})
        ok("and a write without the cookie is refused", r.status_code == 401)

    registry.close()
    print(f"\n{'FAILED: ' + ', '.join(_failed) if _failed else 'all green'}")
    return 1 if _failed else 0


sys.exit(asyncio.run(main()))
