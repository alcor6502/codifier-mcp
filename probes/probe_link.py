"""A LIVE probe of the closing link: the ticket, the message it rides in, and
the page it lands on. No suite renders HTML and none composes a message, so
both are driven for real here — the mail through a Mailer whose delivery is
replaced, the page through an ASGI transport.

Run it from the bench, with the engine on PYTHONPATH.
"""
import asyncio
import logging
import os
import re
import sys
import tempfile
import time

import httpx

# ⚠ The probes live in probes/ and drive the code in the repository ROOT. When
# a script runs from a subdirectory, `sys.path[0]` is THAT subdirectory, not
# the working directory — so `import rules` would fail no matter where it was
# launched from. The root goes on the path explicitly, and the probe finds the
# same modules the suites import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mail as mailmod
import rules
import web

REF = "reference0000001"
ADM = "adminadmin00001"
MASTER = "a-long-enough-password"
# WHAT THE DEPLOYMENT DECLARES, and what must come out of it: the Funnel's
# public https host, and the admin page on its own port over http. The second
# is DERIVED from the first since v7.0.1 — nobody types it.
DECLARED = "https://svc-a2.tail1234.ts.net"
BASE = "http://svc-a2.tail1234.ts.net:9443"

_failed = []


def ok(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        _failed.append(name)


async def main():
    # ---- the derivation itself, before anything is composed ----
    os.environ["WEB_PORT"] = "9443"
    got = web.ui_base_url(DECLARED)
    ok("the page's address is derived from BASE_URL and the UI's port",
       got == BASE, got)
    ok("and it is http, because that port carries no certificate",
       got.startswith("http://") and not got.startswith("https://"), got)
    ok("the Funnel's port is NOT what comes out: the UI's own port is what "
       "keeps the link inside the tailnet", ":443" not in got, got)
    os.environ["WEB_PORT"] = "9444"
    ok("a different WEB_PORT moves the address with it, in one place",
       web.ui_base_url(DECLARED) == "http://svc-a2.tail1234.ts.net:9444",
       web.ui_base_url(DECLARED))
    os.environ["WEB_PORT"] = "9443"
    ok("no BASE_URL, no address — and therefore no button",
       web.ui_base_url("") == "" and web.ui_base_url("   ") == "",
       repr(web.ui_base_url("")))
    ok("a BASE_URL that already carries a port loses it: the UI's port is the "
       "one that answers", web.ui_base_url("https://svc-a2.tail1234.ts.net:443")
       == BASE, web.ui_base_url("https://svc-a2.tail1234.ts.net:443"))

    root = tempfile.mkdtemp(prefix="probe-link-")
    with open(os.path.join(root, rules.REGISTRY_FILE), "w", encoding="utf-8") as fh:
        fh.write(f"Palestra | {REF} | {ADM}\n")
    log = logging.getLogger("probe")
    registry = rules.Registry(root)
    prj = registry.by_name("Palestra")
    prj.amend_project("consumer", "Alfredo", "create", {"kind": "human"},
                      actor="probe", on_the_page=True)
    prj.set_postbox({"Alfredo": "a@example.com"}, "Alfredo")
    prj.amend_project("consumer", "Coach", "create", {"kind": "chat"},
                      actor="probe", on_the_page=True)
    t = prj.task_add("Alfredo", "sign the gym waiver",
                     "the paper one, at the desk", "Coach")

    # ---- the message, composed for real, delivery replaced ----
    sent = {}
    mailer = mailmod.Mailer(host="smtp.example", sender="from@example",
                            base_url=BASE, log=log)
    mailer._deliver = lambda msg: sent.setdefault("msg", msg)
    went = mailmod.task_opened(mailer, prj, t["id"], "Alfredo", "Coach",
                               "sign the gym waiver", "the paper one, at the desk")
    ok("the task is posted", went and "msg" in sent, went)
    html = "".join(p.get_content() for p in sent["msg"].walk()
                   if p.get_content_type() == "text/html")
    text = "".join(p.get_content() for p in sent["msg"].walk()
                   if p.get_content_type() == "text/plain")
    m = re.search(r'href="([^"]+)"', html)
    ok("the message carries a button", m is not None, html[:300])
    url = (m.group(1) if m else "").replace("&amp;", "&")
    ok("and it points at this container's own address", url.startswith(BASE), url)
    ok("the plain-text half carries the same link, so neither can drift",
       url in text.replace("&amp;", "&"), text[-300:])
    ok("the footnote says what the button does and does not ask for",
       "asks for no password" in text, text[-300:])

    path = url[len(BASE):]
    transport = httpx.ASGITransport(app=web.build(
        registry=registry, log=log, master=MASTER,
        refusal=rules.RulesError, fault=rules.RulesFault, backup_dir=root))
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://probe") as c:
        r = await c.get(path)
        ok("the link opens WITHOUT a session", r.status_code == 200
           and t["id"] in r.text, r.status_code)
        ok("and it shows the entry it is for, body and all",
           "the paper one, at the desk" in r.text)
        ok("with no menu to pages it cannot open",
           "/rules" not in r.text and "/people" not in r.text, r.text[:400])

        # A GET NEVER CLOSES: a mail client that prefetches links must not be
        # able to close a task by looking at the message.
        got = prj.task_get([t["id"]])["tasks"][0]
        ok("fetching the link changes nothing — a prefetching mail client "
           "must not be able to close a task", got["status"] == "pending", got)

        token = path.split("k=")[1]
        r = await c.post(path.split("?")[0],
                         data={"k": token, "action": "complete",
                               "outcome": "signed at the desk"})
        ok("the entry is closed from the link", r.status_code == 200
           and "completed" in r.text, r.text[:300])
        got = prj.task_get([t["id"]])["tasks"][0]
        ok("and the closure is really in the database, signed by the desk",
           got["status"] == "completed" and got["outcome"] == "signed at the desk",
           got)

        r = await c.post(path.split("?")[0],
                         data={"k": token, "action": "complete",
                               "outcome": "again"})
        ok("the SAME link a second time finds it closed — single-use without a "
           "table", r.status_code == 400 and "closed is closed" in r.text,
           r.text[:300])

        # ---- a ticket that is not one ----
        t2 = prj.task_add("Alfredo", "second entry", "b", "Coach")
        good = prj.task_link(t2["id"], 14)
        p2 = f"/p/Palestra/t/{t2['id']}"
        r = await c.get(f"{p2}?k={good['token']}")
        ok("a fresh ticket opens its own entry", r.status_code == 200
           and t2["id"] in r.text)
        # ⚠ FLIPPED, not set to a constant. `token[:-1] + "0"` is the same
        # token one time in sixteen — a probe that passes fifteen runs out of
        # sixteen and fails the other one teaches you to re-run it, which is
        # the worst thing a check can teach. Caught here by that very failure.
        _bad = good["token"][:-1] + ("1" if good["token"][-1] == "0" else "0")
        r = await c.get(f"{p2}?k={_bad}")
        ok("a doctored signature is refused, saying nothing useful",
           r.status_code == 403 and "not valid" in r.text, r.text[:200])
        r = await c.get(f"{p2}?k=")
        ok("and so is no ticket at all", r.status_code == 403, r.status_code)
        # THE TICKET FOR ONE ENTRY DOES NOT OPEN ANOTHER, which is the whole
        # of what the signature buys.
        r = await c.get(f"/p/Palestra/t/{t['id']}?k={good['token']}")
        ok("a ticket does not travel to another entry", r.status_code == 403,
           r.status_code)
        # An expired one says so, and says the entry is untouched.
        exp = f"{int(time.time()) - 60}.x"
        forged = prj._task_ticket(t2["id"], int(time.time()) - 60)
        r = await c.get(f"{p2}?k={forged}")
        ok("an EXPIRED ticket is told apart from a forged one, in words",
           r.status_code == 403 and "expired" in r.text and "untouched" in r.text,
           r.text[:300])
        got = prj.task_get([t2["id"]])["tasks"][0]
        ok("and nothing it touched moved", got["status"] == "pending", got)

    # ---- and with no base url, the message is the one sent yesterday ----
    sent.clear()
    quiet = mailmod.Mailer(host="smtp.example", sender="from@example", log=log)
    quiet._deliver = lambda msg: sent.setdefault("msg", msg)
    t3 = prj.task_add("Alfredo", "third entry", "c", "Coach")
    mailmod.task_opened(quiet, prj, t3["id"], "Alfredo", "Coach", "third entry", "c")
    html = "".join(p.get_content() for p in sent["msg"].walk()
                   if p.get_content_type() == "text/html")
    ok("no WEB_BASE_URL, no button — and no guessed address either",
       "href=" not in html and "asks for no password" not in html, html[:300])

    registry.close()
    print(f"\n{'FAILED: ' + ', '.join(_failed) if _failed else 'all green'}")
    return 1 if _failed else 0


sys.exit(asyncio.run(main()))
