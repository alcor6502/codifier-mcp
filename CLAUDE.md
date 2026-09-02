# codifier-mcp — working notes for Claude Code

Rule registry over SQLite, served as an MCP server. Twin of `archivist-mcp`;
both pin `mcp-common-engine` to a tag. Two servers in one process — the MCP
surface (`server.py`) and the admin page (`web.py`) — over one engine
(`rules.py`), because two processes on one SQLite database do not share the lock
that makes a multi-statement transaction atomic.

English in this repo: code, docstrings, errors, README, commit messages, tags.

## Before you touch anything

- **`VERSION` lives in `rules.py`, not `server.py`.** The engine is what the
  suites import; the server imports the engine. A grep against `server.py` finds
  nothing and reads like "no version".
- **A number is never written twice.** Versions, counts, caps: read them from the
  constant or count them. Where a copy is unavoidable, a check must compare the
  two — `test_surface.py` is full of them.
- **One change at a time, in the working tree too.** The bill comes at commit:
  two changes already mixed in the same files cannot be separated afterwards.
- **`git add` named files. Never `-A`.** Read `git status --short` line by line
  and ask whether each one is yours; a line you do not recognise is not.

## Running the suites, and shipping

```
scripts/test.sh                        # the bench and the five suites, in order
scripts/ship.sh <msg-file> <file>...   # suites, commit, push to main, release link
```

`test.sh` makes a venv, installs the engine **from the pin in
`requirements.txt`** — the tarball, or a clone of the same tag when the network
refuses the tarball — and runs the suites the CI runs, in the same order,
stopping at the first red. Each suite goes to a file and the script prints
`exit=`. `test_surface.py` reads both files and goes red if the suite list
differs from `build.yml` in either direction, or if the script carries a copy
of the tag.

`ship.sh` runs `test.sh`, `git add`s the **named** files, commits with the
anonymous identity passed on the command, pushes `HEAD:refs/heads/main`, proves
the push against the fetched hash, and — if `VERSION` moved in that commit —
prints the pre-filled release link. **The tag is never typed and never pushed**:
from the sandbox a tag push answers 403, and one typed on a tablet came out
`V7.0.0`, which the workflow's glob ignored in silence.

By hand, when you need one suite:

```
export PYTHONPATH=<mcp-common-engine, EXTRACTED FROM THE PINNED TAG>
python3.12 test_schema.py      # DDL: triggers, constraints, schema generation
python3.12 test_registry.py    # projects.txt, the router, its refusals
python3.12 test_collaudo.py    # the engine, refusals included
python3.12 test_surface.py     # the seam: image, template, manuals, README
python3.12 test_crash.py       # SIGKILL mid-transaction
```

No network, no FastMCP, no Docker.

- **Take the engine from the pinned tag, not from a sibling clone.** That clone
  is shared with the twin and drifts.
- **Read the exit code.** A suite that dies halfway still prints hundreds of
  greens; a `| tail` throws away the only signal that says so.
- **A bench built with `mktemp -d` needs the non-Python files too, and
  `.github/workflows/`.** Without that directory `test_surface.py` does not go
  red — it *dies* with `FileNotFoundError` on `build.yml`, after printing
  hundreds of passes. Compare the sha256 of `server.py` and `rules.py` between
  source and bench: a stale bench answers for code you are not running.

## Rules that cost something every time they are forgotten

- **A control never seen to fail is not a control.** Inject the defect, watch the
  red, and check that the red *names the culprit* — which tool, which value,
  which line. Several checks here were written, passed, and were later found to
  be measuring nothing.
- **A control that does not count what it watches can go green for lack of
  work.** v6.0.0 shipped a preflight check it never ran, because the verifier
  counted the `c_*` functions *defined* instead of the ones *listed* in `CHECKS`.
- **The guide ships in the same delivery as the behaviour.** A manual with the
  wrong limits is worse than a manual with none.
- **A new environment variable is born optional**, with a working default in
  code: Unraid does not propagate new variables to existing containers.
- **A rule with exceptions is not enforceable.** An exception justified by how
  today's callers behave stops holding without anyone touching the code.
- **The diff is the only proof.** A "DONE" written in a document is a claim to
  verify, not a state. `git show <tag>:<file>` and grep for the expected string
  before theorising about caches or delivery chains.

## The surface, and why it does not grow

Every tool description rides at the head of every request, always, and arrives
isolated. Hence three levels of documentation, and hence the refusal to multiply
tools: the **description** carries the signature and one line;
**`reference_guide()`** is the model; **`reference_guide("<name>")`** is one
command's card.

The manuals are two files — `reference-guide.md` (10 cards) and
`reference-guide-admin.md` (6) — deliberately, so that "the admin manual served
without a key" is a failure that cannot happen rather than one to test for.

`test_surface.py` reads the **AST**, not the text (a substring search is
satisfied by a commented-out line), and holds: every engine call exists with a
compatible signature; every writing tool passes the gate it claims; no docstring
or manual names a tool that does not exist; every template variable has a reader;
every imported module appears in a `COPY` line of the Dockerfile; the README's
version and tool-count badges match the constant and the AST; and every signature
in the README matches the code **in both directions** — every parameter named,
and none invented.

⚠ **Refusal strings are quoted verbatim in the cards and compared against what
the engine actually raises.** Change a refusal message and the card changes in
the same commit, or the suite goes red — which is the point of it.

## Shipping

- **`scripts/ship.sh`, every commit, straight to `main`.** No feature branch,
  no pull request, no merge: a branch is a merge, and a merge is where two hands
  in the same file notice each other too late. The tag is born on the release
  page, from the link the script prints — never typed, never pushed.
- **The tag must equal `VERSION`.** A CI step compares them and stops the
  release. It exists because a version was once published declaring another.
- **If the MCP surface moves — names, parameters, or *descriptions* — the tag
  note must say so:** `reconnect the connector and test in a NEW conversation`.
  Tool descriptions live in the client's cache, and a stale one does not fail:
  it advises badly.
- **The proof of a release is the version line in the container log**, or
  `reference_guide()`, which returns `VERSION` read at runtime. Not the push, and
  not a number written in a document.

## Four traps that live in the code, not the deployment

All four are FastMCP's, and any self-hosted server of this shape meets them.

- **Sync tools run in a thread from a pool**, so a SQLite connection opened at
  import dies on the first call. The cure is `check_same_thread=False` **plus** a
  re-entrant lock held for the whole of every public method — half the cure is
  worse than none, because without the lock two multi-statement transactions
  interleave and one `COMMIT` closes another's, silently.
- **A deliberate refusal must not look like a crash.** Raise a `ToolError`
  carrying a log level, from a decorator wrapping the tool — not from middleware:
  `call_tool` applies middleware outside and logs inside.
- **`workflow_dispatch` would publish `:latest` without comparing the version
  constant to the tag.** The `latest` tag is gated on `refs/tags/v`.
- **The Dockerfile lists its `COPY` lines by hand.** A missing module kills the
  container at boot; a missing `.md` kills nothing and serves an empty manual,
  which is worse. `COPY *.py` would put the test files in the image.

## Out of scope for a code change

Approving a rule, minting a one-time code and creating a project are not on the
MCP surface and are not to be added to it. What is catastrophic has no tool; what
is fundative — a project's brief, its specs, its `queue_cap`, and its people — is
written by a person on the admin page.
