# probes — the pages, driven for real

**No suite in this repository renders HTML.** `test_surface.py` reads the AST,
which is why it can say that every handler calls the engine with a compatible
signature — and why it cannot say what a handler does with the dictionary that
comes back. That gap is what these scripts are for.

They are **not a fourth suite** — the suites run on every job, these run at
the tag — but since v7.1.0 they are a **release gate**. `build.yml` has a
`probes` job and `image` waits for it, so a red probe is a release that does
not happen.

Run them by hand too, whenever `web.py` or `mail.py` moves. Waiting for the
tag to find out is waiting too long.

## What they have caught

Both of these were live defects that five green suites did not see:

- **`KeyError: 'note'` in `consumers_action`** — a 500 on a write that had
  *already succeeded*, which is the worst shape a fault can take: the page
  says failure and the database says otherwise. `note` is optional on the
  verdict of `amend_project`, and the handler read it unconditionally.
- **A 500 on the rules page for every skill.** `list_rules` has two payload
  shapes — for a skill the project's profile comes back as
  `{withheld, note}`, with no `brief` key — and the page read
  `profile["brief"]` regardless. Picking any skill from the menu returned
  Internal Server Error, and had done for several versions. Nothing caught it
  because nothing drove that page.

## Running them

```
python3.12 -m venv /tmp/probe-venv
/tmp/probe-venv/bin/pip install -r probes/requirements.txt

export PYTHONPATH=<mcp-common-engine, EXTRACTED FROM THE PINNED TAG>
/tmp/probe-venv/bin/python probes/run.py
```

⚠ **`run.py` is the command CI runs, character for character.** That is why it
exists instead of two lines of YAML that resemble it: a bench that runs
something slightly different from what the release runs is a bench that lies.
Run a single probe directly when you are working on it; run `run.py` before
you believe the result.

Each probe prints one line per case and ends with `all green` or
`FAILED: <names>`. `run.py` prints all of that, then a summary, and exits
non-zero if **anything** did.

### What `run.py` refuses to call a pass

| | Why it is not paranoia |
|---|---|
| a probe that exits non-zero | the obvious one |
| a probe that exits **zero** having printed fewer `PASS` lines than it has `ok()` calls in its own source | a probe killed halfway prints a screen of passes before it dies, and **a control that does not count what it watches goes green for lack of work**. The floor is read from the file, never written down — loops make the real count higher, so the test is `printed >= declared` |
| a run that found fewer than two probes | a glob that matches nothing succeeds at everything, which is the quietest way for a gate to become decorative |

`shots.py` is deliberately not picked up: only `probe_*.py` is. It has no
verdict to give, so it has no business in a gate — and pulling a browser into
every release would cost minutes for nothing. Its dependencies live apart, in
`requirements-shots.txt`, which CI does not install.

The probes put the repository root on `sys.path` themselves, so they can be
launched from anywhere. They need no database, no network and no FastMCP —
each one builds its own registry under `mktemp -d`.

### And the gate is pinned, because a gate is six lines of YAML away from gone

`test_surface.py` holds that `build.yml` has a `probes` job, that `image`
**waits** for it, that CI runs `run.py` and not a hand-rolled loop, and that it
does not install the browser stack. It also reads `run.py` itself and holds
that it still counts cases and still refuses an empty glob. Every one of those
was seen to fail by injecting the defect.

## The three files

| File | What it drives |
|---|---|
| `probe_ui.py` | the whole admin page: the door, the profile, the register and the card, the one-time codes minted in a run, the log dashboard, and the rules page **on all three kinds of consumer** |
| `probe_link.py` | the closing link end to end — the message composed by a real `Mailer` with only its delivery replaced, the button, the page opened with no session, the closure landing in the database, the second press, a doctored ticket, one from another entry, an expired one |
| `shots.py` | **not a check, a look.** Screenshots of every page, light and dark, at iPad width. Whether the type and the contrasts sit well on a real screen is a judgement no measurement replaces. Needs `requirements-shots.txt`, and finds the browser under `PLAYWRIGHT_BROWSERS_PATH` rather than naming a build |
| `run.py` | runs every `probe_*.py`, and is what both CI and a person invoke |

## Two things they have taught, which are about probes and not about the code

- **A probe that fails once in a while teaches you to re-run it**, which is the
  worst thing a check can teach. `probe_link.py` doctored a ticket by *setting*
  the last character of the signature to `0`; the signature is a hex digest, so
  one time in sixteen the "doctored" token was the good one. It flips that
  character now, and asserts that the flip changed something. The same defect
  was found in `test_collaudo.py` afterwards — a defect found in one file is a
  defect to grep the whole repository for.
- **A probe written against markup it did not look at fails on a page that is
  right.** Three cases here were wrong rather than the code: an apostrophe
  comes back as `&#x27;` because every value goes through `_esc`; raw markdown
  *is* on the page, inside the textarea where it belongs; and a heading carries
  its close link inside it, so the tag does not follow the words. Each one is
  marked with a ⚠ where it sits.
