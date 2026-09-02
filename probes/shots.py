"""Screenshots of the admin UI, light and dark, on an iPad-width viewport.

Not a check — a LOOK. The suites read text and the probes drive the pages;
neither can say whether 17px, the cards and the contrasts sit well on a real
screen, and that judgement is Alfredo's.
"""
import asyncio
import glob
import logging
import os
import sys
import tempfile
import threading

import uvicorn
from playwright.async_api import async_playwright

# ⚠ The probes live in probes/ and drive the code in the repository ROOT. When
# a script runs from a subdirectory, `sys.path[0]` is THAT subdirectory, not
# the working directory — so `import rules` would fail no matter where it was
# launched from. The root goes on the path explicitly, and the probe finds the
# same modules the suites import.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rules
import web

REF, ADM, MASTER = "reference0000001", "adminadmin00001", "a-long-enough-password"


def chromium():
    """Where the browser actually is, FOUND and not written down.

    ⚠ The environment ships one Chromium under PLAYWRIGHT_BROWSERS_PATH, and
    its build number is not the one the installed playwright asks for: left to
    itself the library looks for a build that is not there and offers to
    download one, which the sandbox cannot do. So the path is discovered — and
    when it is not found, this says which directory it looked in instead of
    failing later inside the library, where the message is about a download.
    """
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    found = sorted(glob.glob(os.path.join(base, "chromium-*", "chrome-linux",
                                          "chrome")))
    if not found:
        raise SystemExit(f"no chromium under {base} — looked for "
                         f"chromium-*/chrome-linux/chrome")
    return found[-1]
OUT = sys.argv[1] if len(sys.argv) > 1 else "."
PORT = 8931


def seed(root):
    with open(os.path.join(root, rules.REGISTRY_FILE), "w", encoding="utf-8") as fh:
        fh.write(f"Palestra | {REF} | {ADM}\n")
    reg = rules.Registry(root)
    p = reg.by_name("Palestra")
    p.set_profile(
        brief="# Palestra\n\nIl registro della **palestra di casa**: chi fa cosa, "
              "con che attrezzi, e sotto quali regole.\n\n"
              "| Chi | Cosa |\n|---|---|\n| `Coach` | scrive le schede |\n"
              "| `Runner` | esegue e registra |\n\n"
              "> Le regole vincolano, i task aspettano.",
        specs="- tre sedute a settimana\n- il rack ha 120 kg di dischi\n"
              "- il fisioterapista si prenota con **due settimane** di anticipo",
        queue_cap=10, actor="web ui")
    for n, k, b in (("Coach", "chat",
                     "# Coach\n\nScrive le schede e le **corregge** dopo ogni "
                     "seduta.\n\n- non decide gli acquisti\n- non tocca il "
                     "registro fiscale"),
                    ("Runner", "skill",
                     "# Runner\n\nEsegue la scheda e ne *registra* l'esito."),
                    ("Alfredo", "human", None)):
        f = {"kind": k}
        if b:
            f["brief"] = b
        p.amend_project("consumer", n, "create", f, actor="web ui",
                        on_the_page=True)
    p.set_postbox({"Alfredo": "alfredo@example.com"}, "Alfredo")
    p.amend_project("consumer", "Marta", "create", {"kind": "human"},
                    actor="web ui", on_the_page=True)
    p.amend_project("consumer", "Sostituto", "create", {"kind": "chat"},
                    actor="web ui", on_the_page=True)
    p.amend_project("consumer", "Sostituto", "retire", {},
                    reason="il coach è tornato", actor="web ui", on_the_page=True)
    p.task_add("Coach", "il rack traballa",
               "Terzo bullone del montante di sinistra. Da stringere prima "
               "della prossima seduta di stacchi.", "Runner", urgent=True)
    p.task_add("Alfredo", "prenotare il fisioterapista",
               "Quello di via alta. Due settimane di anticipo.", "Coach")
    p.task_add("Runner", "registrare la seduta di ieri",
               "Cinque serie, l'ultima corta.", "Coach")
    return reg


async def main():
    root = tempfile.mkdtemp(prefix="shots-")
    reg = seed(root)
    app = web.build(registry=reg, log=logging.getLogger("shots"), master=MASTER,
                    refusal=rules.RulesError, fault=rules.RulesFault,
                    backup_dir=root)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT,
                                           log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        await asyncio.sleep(.1)

    shots = [("login", "/", False),
             ("consumers", "/p/Palestra/consumers", True),
             ("card-person", "/p/Palestra/consumers?edit=Alfredo", True),
             ("card-chat", "/p/Palestra/consumers?edit=Coach", True),
             ("card-new", "/p/Palestra/consumers?new=1", True),
             ("profile", "/p/Palestra/profile", True),
             ("log", "/p/Palestra/tasks", True),
             ("codes", "/p/Palestra/codes", True)]
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(executable_path=chromium())
        for scheme in ("light", "dark"):
            ctx = await browser.new_context(viewport={"width": 834, "height": 1120},
                                            device_scale_factor=2,
                                            color_scheme=scheme)
            page = await ctx.new_page()
            for name, path, need_session in shots:
                if need_session:
                    await page.goto(f"http://127.0.0.1:{PORT}/")
                    if await page.locator("#master").count():
                        await page.fill("#master", MASTER)
                        await page.click("button.go")
                await page.goto(f"http://127.0.0.1:{PORT}{path}")
                if name == "codes":
                    for _ in range(3):
                        await page.click("button.go")
                await page.screenshot(path=os.path.join(
                    OUT, f"ui-{name}-{scheme}.png"), full_page=(name != "profile"))
                print("shot", name, scheme)
            await ctx.close()
        await browser.close()
    server.should_exit = True


asyncio.run(main())
