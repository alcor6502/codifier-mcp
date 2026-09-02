# probes — the pages, driven for real

**No suite in this repository renders HTML.** `test_surface.py` reads the AST,
which is why it can say that every handler calls the engine with a compatible
signature — and why it cannot say what a handler does with the dictionary that
comes back. That gap is what these scripts are for.

They are **not a fourth suite**. Nothing here runs in CI, and the reason is in
`build.yml`: the test job installs the engine with `--no-deps` and carries no
web stack at all. Adding one would make every release pay for a dependency
tree the image does not have.

So they are run **by hand, when `web.py` or `mail.py` moves**. That is the
whole rule.

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
/tmp/probe-venv/bin/python probes/probe_ui.py
/tmp/probe-venv/bin/python probes/probe_link.py
```

Each prints one line per case and ends with `all green` or `FAILED: <names>`,
and exits non-zero when anything failed. **Read the exit code**: a probe that
dies halfway still prints a screen of passes.

The probes put the repository root on `sys.path` themselves, so they can be
launched from anywhere. They need no database, no network and no FastMCP —
each one builds its own registry under `mktemp -d`.

## The three files

| File | What it drives |
|---|---|
| `probe_ui.py` | the whole admin page: the door, the profile, the register and the card, the one-time codes minted in a run, the log dashboard, and the rules page **on all three kinds of consumer** |
| `probe_link.py` | the closing link end to end — the message composed by a real `Mailer` with only its delivery replaced, the button, the page opened with no session, the closure landing in the database, the second press, a doctored ticket, one from another entry, an expired one |
| `shots.py` | **not a check, a look.** Screenshots of every page, light and dark, at iPad width. Whether the type and the contrasts sit well on a real screen is a judgement no measurement replaces |

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
