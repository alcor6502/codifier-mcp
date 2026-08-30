# Codifier MCP <img align="right" src="https://img.shields.io/badge/License-MIT-yellow.svg">

<img src="https://img.shields.io/badge/version-7.0.0-blue.svg"> <img src="https://img.shields.io/badge/python-3.12-3776AB.svg?logo=python&logoColor=white"> <img src="https://img.shields.io/badge/Unraid-7-F15A2C.svg"> <img src="https://img.shields.io/badge/MCP-16%20tools-8A63D2.svg">

**The rules your project runs on, in a registry instead of scattered Markdown —
so a chat can answer "which rules am I under?" in one call.**

Self-hosted. Nothing leaves your machine except towards the conversation that
asked. Rules are never deleted, IDs are never reused, and history is written by
the database itself.

*An Italian translation was maintained until `v4.1.0` and is still readable at
that tag. It was dropped rather than left to rot: two files of prose cannot be
kept honest by a test, and one that is wrong is worse than one that is missing,
because it gets believed. The twin project dropped its own for the same reason
after a sweep found seven divergences, all seven on the translated side.*

---

## Why it exists

Give an LLM project a set of rules and they start in one file. Then a role needs
one of its own, then a second role, and eighteen months later there are 177 of
them across three documents plus the roles' own memories. Every chat opens three
files to use forty rules.

The context cost is the symptom. **The disease is that nobody can answer
quickly: this chat, right now, which rules is it under?** Answering it today
means reading three files, holding in your head which applies to whom, and
trusting that nobody wrote the same thing twice in two places. It is a reading
job, and because it is a reading job it gets done badly.

|  | Rules in Markdown | Codifier |
|---|---|---|
| "Which rules apply to me?" | open three files, filter by hand | one call, ordered |
| Changing one rule that lives in three memories | three edits, and you forget the third | one edit |
| Reusing a retired rule's number | nothing stops you | the database refuses |
| "Why is this rule here?" | ask whoever wrote it | the reason is mandatory, and kept |
| A rule that stopped being needed | stays forever | its term runs out and it drops out on its own |
| Two rules that say the same thing | somebody notices, eventually | the queue is read whole before anything is approved |
| Someone edits the file by hand | invisible | recorded by a trigger |

The real leap is not the lookup: it is that **the database refuses things.** The
ID cannot be reused, the reason cannot be omitted, deletion does not exist, and
history is written by triggers — so a change made by hand with `sqlite3` is in
there too. What used to be discipline is now a constraint.

### The model in six sentences

**A project is one database**, named in a text registry the owner writes by
hand. Projects do not see each other and no tool lists them: a project is a
file, not a column, so a backup, a restore and a corruption are each one
project's business.

**Consumers** are whoever downloads rules: chats *and* skills. A skill acts, and
what acts is under rules. A person is not a consumer — a rule that binds a
person says so in its body. A consumer's name is ONE WORD, because that name is
quoted by hand in chat instructions and skill files, and a space is the mistake
nobody sees.

**The audience is MIXED, and declared rather than deduced.** `reach` is `all` —
everybody, no audience rows at all — or `targeted`, and then the audience is the
**groups** UNION the **exceptions**: single consumers standing next to the
groups, only ever adding. A rule that says `targeted` and names nobody is
refused by a trigger, and so is one that says `all` and names somebody.

**The reading order is the breadth of the door you came through.** Universal
first, then groups from the widest, then what was aimed at you by name — and
because breadth is a `COUNT` of live members computed now, the order stays right
by itself when a consumer appears or a group empties out.

**Widening binds somebody new, so it is promulgation**, not an edit: `rules_amend`
narrows a perimeter and refuses to widen one. To widen, you propose a supersede
and a person approves it.

**History is a photograph.** Each version records both what was declared and who
was actually reached that day, by name, so changing a group tomorrow cannot
rewrite what was true yesterday.

**Tasks live in the same registry and are modelled as the opposite of a rule**:
no audience, no approval, no expiry. Rules bind; tasks wait.

### How a rule gets in

    proposed ──approved, by a batch──> in force ──retired──> retired
        │                                  ▲                    ▲
        │                                  │                    │
        └──denied──> denied            it stays,       superseded by an heir,
                     the row STAYS,    for as long     inside the same decision
                     with its reason   as nobody
                                       ends it

ONE axis, and it is `status`: `proposed | active | retired | denied`. A rule
that has been approved is in force, and it stays in force until somebody ENDS
it — by retiring it, or by approving an heir that supersedes it. There are two
ways out of the list and both of them are gestures somebody made, with a reason
attached.

⚠ **This used to be two axes**, and until v5.0.0 an approved rule was born
*provisional* with an expiry stamped on it: past that date it left every list
while the row still said `active`, and no gesture and no event were written. The
mechanism was built against a diagnosis that named the wrong culprit. A project
did go from 63 rules to 172 — but not for want of a clock. It grew because
there was **no gate**: rules were written without anybody approving them, on the
argument that otherwise the sky would fall. The gate exists now and it is the
human approval on the page, so the clock was guarding a hole that had been
filled somewhere else.

And it broke the wrong way round. For a register of rules the acceptable
failure is the rule too many, which annoys you until you remove it — never the
rule that disappears while nobody is looking. The register had in fact already
decided this exact question, the other way, about tasks: *a task that vanished
on a timer is work nobody decided to drop*. Two objects in one database under
two opposite philosophies, and this is the one that gave way.

**Nothing replaced it**, and that too is a decision rather than an omission. The
obvious substitute — a report of rules in force for more than N months — would
have flagged the FOUNDATIONAL rules first, the ones that are born with the
project and die with it, which is guaranteed noise on exactly what must never be
touched.

**Approval is by batch, against its digest.** Proposals accumulate, and you see
them together, which is the only moment three near-duplicates are visible as
such. The lot page of the administration UI shows the queue whole, each proposal
with its reason, and computes a digest over what it displayed; the action must
hand that digest back, so what gets approved is provably the batch that was
**read** — a proposal arriving in between moves the digest and voids the stale
approval. Approval has not been a tool since v3.0.0: it happens in a browser,
behind the UI's own password, so no secret of that level ever travels in a
conversation. (An ed25519 signature used to ride on top; it left in v2.0.0 — it
was the clumsy way of letting a person in instead of a chat, and the UI solves
that at the root.)

Denial needs no digest: refusing cannot do harm. It does cost a sentence, one
per proposal. The denied row stays, with its reason, and
`rules_list(pending=True)` shows a chat its own refusals — so the same idea
coming back through another chat in three weeks is something you can see,
rather than something the registry can block.

How many proposals may wait at once is `queue_cap`, and it belongs to the
**project**, kept in the project's own database — not to the container, which
serves several. NULL is unlimited, 0 closes the queue, N is N.

### The number is not yours to pick

`rules_propose` takes the **domain**, not the ID: the registry assigns the next
number in it, four digits, and hands it back. A number is not a choice, it is a
position in a sequence — and whoever cannot pass it cannot pick it. Four digits
because IDs are never reused, so a domain burns numbers even while only twenty
rules are alive.

There is no numbering-gap report, and that is the same decision seen from the
other side: with a counter a gap cannot happen, so a report of one could only
ever have meant somebody chose.

### Citations are marked, checked, and expanded

A citation is an ID in **round brackets**, `(VA-0002)`. An ordinary parenthesis
is ordinary prose — what makes a token a citation is the shape `XX-NNNN`, not
the bracket — so a document's own `[[wiki links]]` stay free.

At the door the registry refuses a bare ID left outside a bracket of its own
(case does not save you), one that does not resolve, one pointing at a rule that
is **not in force yet**, and any note of your own written inside the brackets —
what is in there is not stored, and a registry that quietly dropped your words
would be worse than one that refuses them. Only the domains the project declared
are hunted, so a ticket number or a locale in a URL stays prose. That last is
the one that shapes the work: file the cited rule, have it approved, then file
the rule that cites it. The number of a proposal is not final until it is in, so
a batch whose members cite each other can be approved into a state where its
pointers were right only while they were being written.

On the way out every citation carries the current title of what it points at:

    (AL-0004)  →  (AL-0004 — alternative shares are not sold at a loss)

The gloss is generated, never stored — what goes into the database is the bare
pointer, which is why it cannot go stale — and a pointer at a retired rule
arrives already marked as such, in the text.

### What it looks like

```
rules_list(project="<code>", consumer="tax")

  VA-0002  Re-read the sources       reach all        reaches you: everyone
  PE-0001  The method of the four    reach targeted   reaches you: deliberativi
  FI-0003  Estimating the bracket    reach targeted   reaches you: by name
  ...
  38 rules · and your open tasks at the foot
```

`reaches_you` says *why* a rule is in your list — everybody, a group you belong
to, or your own name — which is exactly what you need in order to decide whether
it belongs somewhere else. The same call carries the project's brief, your own,
and the tasks open on your desk: one call, because the alternative was four, and
a chat that must make four calls before it can work gets three of them wrong
once.

---

## How it is built

Every piece was chosen for a specific reason, and the reasons are worth stating:
they are the same ones you need if you want to adapt it.

### MCP — Model Context Protocol

The protocol the model uses to talk to external tools. An MCP server exposes
**tools**: functions with a name, typed parameters and a description. The model
reads the descriptions and decides on its own when to call them.

That has a consequence which governs the whole design: **every tool's
description rides at the head of every request**, always, even when none of them
is used — and it arrives *isolated*, read without the rest of the surface in
view. Hence the refusal to multiply tools, and hence the division of labour,
which since 4.1.0 has three levels instead of two:

- **the description** carries the signature and one line of what the tool does,
  and nothing else;
- **`reference_guide()`** is the model: projects and codes, consumers, the shape
  of a citation, the rules of the house. Only what a signature cannot say, and
  it is fetched when it is wanted;
- **`reference_guide("rules_propose")`** is one command's card — its arguments,
  the cases nobody guesses, and the refusals it raises, quoted as the service
  actually words them.

The reason for the third level is that the second was all-or-nothing: to learn
one thing about one tool, a caller paid for the entire manual. The manual as a
whole grew when it was cut into cards; what shrank is the price of one question.

Documentation for humans is in this README, which costs a conversation nothing.

### FastMCP

The Python implementation of the protocol. It handles HTTP transport, schema
serialisation and — the part that really earns its keep — the whole **OAuth 2.1
dance with Dynamic Client Registration and PKCE** that a remote connector
requires. Writing that by hand would have been the bulk of the work.

Two of its behaviours shaped code here rather than the other way round. It runs
synchronous tools **in a thread from a pool**, which a SQLite connection opened
at import does not survive; and it logs a refusal the way it logs a crash unless
the refusal says otherwise. Both are in *Traps already paid for*.

### OAuth 2.1 with GitHub login

The service has no users of its own: it delegates login to GitHub and then
**refuses anyone who is not the single configured username**. Anyone on GitHub
can *attempt* to log in; the refusal comes from the server, not from GitHub.

Since v1.2 the refusal covers **every request, the handshake included**: a
stranger who authenticates with their own GitHub account does not open a session
at all, and never sees that the tools exist. Refusals are logged with the reason,
because from the client a refused stranger and a broken deployment produce the
same symptom — the connector will not connect.

Why GitHub rather than a password: a password on an exposed service is a secret
living in plaintext somewhere with no revocation. An OAuth identity has expiry,
revocation and no client-side secret.

### Tailscale Funnel, and the page it cannot publish

The MCP server listens on `127.0.0.1` and does **not know** how traffic reaches
it. The Funnel runs in the same container and publishes that port on a public
HTTPS URL with a valid certificate, without opening a port on the router and
without exposing a home IP address.

The administration page is the opposite, deliberately. It is **not** published by
the Funnel and must never be: the Funnel can publish only ports 443, 8443 and
10000, so those three are refused for the UI at boot — on one of them the page
that promulgates rules would be on the internet. The page is reached on the
server's own LAN address, behind its own password.

### SQLite, with the history in triggers

One file per project, WAL mode, and the parts that matter are not in the tool
code:

- **versioning is written by triggers**, so a change made by hand with `sqlite3`
  is recorded exactly like one made through a tool. Discipline you have to
  remember is not a guarantee;
- **there is no DELETE**: retiring is a state, and an ID is never reused;
- **the invariants are constraints**, not checks in Python — `targeted` with
  nobody named, `all` with somebody named, an audience that is not contained in
  the one it narrows.

The database must live on **local storage**. WAL needs real file locking, and
over SMB or NFS locks are advisory at best — a failure that is silent, and
therefore the worst kind.

### Docker, running as root

The container runs as **root**, and the databases are 0644. This is the opposite
of the vault twin, which drops privileges, and it is deliberate: from the share
you read the database and you do not touch it, because a write by hand would
bypass the triggers and break history in silence.

The one file that is not world-readable is the registry, `projects.txt`, which
carries every code in clear: 0600, root only, and the mode is re-imposed at
every re-read rather than only at creation.

### Blocking preflight

The preflight checks run at startup. If **a single one** fails the service
**does not start** — it exits 2 — and a check that crashes counts as failed, not
as passed.

It looks excessive until it happens to you: a wrong mount that makes the registry
appear empty, a Funnel publishing the wrong port, a node key with expiry still
enabled that will switch everything off in six months, a database of a schema
generation this build does not know. A service that refuses to start and tells
you why beats one that starts and misbehaves.

---

## Architecture

```
   The model (hosted)
        │  HTTPS + OAuth 2.1 (DCR + PKCE)
        ▼
   Tailscale Funnel  ──►  https://<host>.<tailnet>.ts.net
        │  (in the same container)
        ▼
   127.0.0.1:3001   server.py  ── the 16 MCP tools
        │                        ├─ GitHub identity filter
        │                        └─ source IP filter
        │
        │                web.py  ── the administration page
        │                     ▲     0.0.0.0:9443, LAN only, its own password
        │                     │
        ▼                     ▼
   rules.py  ── Registry (projects.txt)  ──►  Project (one SQLite database)
        │
        ▼
   /db  ── projects.txt (0600) · one folder per project · backup/
```

Two servers, one process, one engine. They share a database, and that is the
reason they are not two containers: two processes on one SQLite database do not
share the lock that makes a multi-statement transaction atomic.

---

## Installation

Written for Unraid 7 with the Tailscale plugin, which is what the template
targets, but this is an ordinary container: two mounts, one port mapping and a
handful of environment variables. Read step 1 to the end before you start —
about forty-five minutes, most of it waiting for GitHub.

⚠ **One change at a time.** If something does not work, fix it before going on.
This is not general prudence: change two things at once and you have two
suspects and an evening.

<details>
<summary><b>1 · Prerequisites</b></summary>

- A Tailscale tailnet with **MagicDNS** and **HTTPS Certificates** enabled.
- Unraid 7 with the **Tailscale plugin** installed: it provides the Docker hook
  that gives the container its own Tailscale identity. Do not uninstall it, even
  if Tailscale on the host is disabled.
- On the host: **Allow Tailscale Funnel = No**. The Funnel belongs to the
  container, not to the host.
- **Local storage** for the databases — a pool or a disk, never a network
  share. SQLite in WAL mode needs real file locking.
- A GitHub account, and a password manager: five secrets come out of the next
  two steps and none of them can be recovered.
- A browser on the same LAN as the server. Rules are approved there, and there
  is no other way to approve them.

</details>

<details>
<summary><b>2 · GitHub OAuth application</b> — five minutes</summary>

`github.com` → Settings → Developer settings → OAuth Apps → **New OAuth App**

| Field | Value |
|---|---|
| Application name | anything, e.g. `codifier-mcp` |
| Homepage URL | `https://<host>.<tailnet>.ts.net` |
| Authorization callback URL | `https://<host>.<tailnet>.ts.net/auth/callback` |

*Generate a new client secret*, then store **Client ID** and **Client Secret** in
your password manager: the secret is shown once, and never expires.

⚠ **A new application per service.** Do not reuse another container's: there is
one callback per application, and the symptom of sharing it is a login that
succeeds and lands on the wrong service.

⚠ The callback must equal `BASE_URL` + `/auth/callback` **exactly**, scheme and
trailing slash included. It is the number one first-run mistake.

*Optional, and it costs nothing:* on the same page, **Application logo** takes a
PNG of at least 200×200, and **Badge background color** is what you see around
it. Set the badge dark — GitHub insets the logo in a round badge with a margin
of its own, and this icon's outer border is a light grey that disappears on
white, which makes the artwork read as smaller than it is.

</details>

<details>
<summary><b>3 · The secrets, all of them before you open Unraid</b></summary>

Generate these now and put them in the password manager. Half of an installation
stalls here because a value has to be invented in the middle of filling a form.

```sh
openssl rand -hex 32        # JWT_SIGNING_KEY — 64 hex characters
openssl rand -base64 18     # WEB_UI_PASSWORD — 12 characters minimum
openssl rand -hex 12        # the project's REFERENCE code
openssl rand -hex 12        # the project's ADMIN code
```

- **`JWT_SIGNING_KEY`** signs the tokens the service issues. It must stay stable
  forever: change it and every issued token dies, and the connector has to be
  reconnected. A different key per service, never reused.
- **`WEB_UI_PASSWORD`** opens the page that promulgates rules. There is no
  second account and no recovery.
- **The two project codes** are 8 to 32 letters and digits. The **reference
  code** opens every read of a project and lets a chat file a proposal; it goes
  at the top of that project's chat instructions. The **admin code** creates
  domains, consumers and groups, and — with a one-time code minted in the
  browser — modifies what already exists. It goes to whoever administers the
  project, and nowhere else.

⚠ **The two codes of a project may not be equal**, and no code may appear twice
in the registry: the service refuses the file naming the line.

⚠ **Codes are not a security boundary.** They are opaque so projects cannot
stumble into each other; no tool lists them and no error names one, and a wrong
code answers exactly like a missing one. The real boundary is the OAuth gate in
front.

</details>

<details>
<summary><b>4 · The database directory</b></summary>

```sh
mkdir -p /mnt/<pool>/<share>/Database/codifier
```

That directory is mounted at `/db`, and everything the service owns lives inside
it: `projects.txt`, one folder per project holding that project's `.db`, and
`backup/`.

⚠ **Local storage, never a network share.** Over SMB or NFS the locking SQLite
needs is advisory at best, and the failure is silent.

*If the parent is already a ZFS dataset, give this directory a dataset of its
own: its snapshots then move independently of the rest of the share.*

</details>

<details>
<summary><b>5 · The container</b></summary>

**The image is published.** Every `v*` tag runs the suites and only then builds
and pushes to `ghcr.io/alcor6502/codifier-mcp`; a tag that does not pass never
becomes an image. The template already points there, so there is nothing to
build.

*Or build it yourself*, from a clone on the server:

```sh
docker build --no-cache -t codifier-mcp /path/to/the/clone
```

Then change `Repository` in the template, or Unraid pulls the published image
over the one you just built and nothing tells you. Do not build on an Apple
Silicon Mac: the image comes out arm64.

**Install the template.** Download `codifier-mcp.xml` from this repository into
`/boot/config/plugins/dockerMan/templates-user/`, refresh the Unraid UI, then
Docker → **Add Container** → template `codifier-mcp`.

⚠ Two similar files, and they are not interchangeable: `codifier-mcp.xml` is the
clean one from the repository; `my-codifier-mcp.xml` is the one **Unraid
rewrites** after an Apply, and it holds the secrets in clear, masked fields
included. The first is publishable, the second is not.

**Paths**

| Name | Host → Container |
|---|---|
| Database | `/mnt/<pool>/<share>/Database/codifier/` → `/db` |
| App Data | `/mnt/user/appdata/codifier-mcp/data` → `/data` |
| Tailscale State | `/mnt/user/appdata/codifier-mcp/ts-state` → `/var/lib/tailscale` |

**Port mapping**: the **Web UI port**, `9443` → `9443`, published on the
server's own IP. The MCP port is **not** published here and must not be: the
Funnel serves it.

**Variables.** Every field carries its own description in the Unraid UI, and
those descriptions are the real documentation of the deploy — this table is the
summary.

| Variable | Value |
|---|---|
| `BASE_URL` | `https://<host>.<tailnet>.ts.net` — **no trailing slash**, https |
| `GITHUB_CLIENT_ID` · `GITHUB_CLIENT_SECRET` | from step 2 |
| `ALLOWED_GITHUB_LOGIN` | your GitHub username |
| `JWT_SIGNING_KEY` · `WEB_UI_PASSWORD` | from step 3 |
| `PORT` | `3001` — the MCP port, inside the container |
| `ALLOWED_CIDRS` | `160.79.104.0/21 # documented egress of the model provider` |
| `BACKUP_DIR` | `/db/backup` |

`WEB_BASE_URL` (`http://<server-ip>:9443`, no trailing slash) is what the
**closing button** in a posted task is built on — leave it empty and there is
no button, because an address the container guessed would be a link that goes
somewhere real and wrong. ⚠ Never put an address here that answers from
outside the tailnet: the link travels in clear through a mail relay, and what
makes that acceptable is that the page cannot be reached from the internet.

Under **Show more settings**: `DB_DIR` (`/db` — move the mount, not this),
`WEB_PORT` (empty means 9443), `ADMIN_AUTH_CODE_DURATION` (minutes a one-time
code stays good), `TASK_LINK_DAYS` (days a closing button keeps working,
default 14), the six `SMTP_*` fields (leave them empty and nothing is ever
posted, and nothing complains about it — see *Notifications* below),
`LOG_LEVEL` (`INFO` or `WARNING`, nothing else),
`HTTP_MODE` (`stateless` on a new install; the code's fallback stays `stateful`
so a container installed earlier keeps behaving as it did), `BIND_HOST`
(`127.0.0.1`, and leave it — the administration page is not affected, it binds
wide on purpose because Docker's bridge forwards a published port to the
container's address, never to its loopback).

⚠ **`WEB_PORT` may not be 443, 8443 or 10000**, and may not equal `PORT`.
Startup is blocked on all four. The first three are the only ports the Funnel
can publish, and the Funnel runs in this container.

**Tailscale**: Enabled `true`, Hostname `<host>` — the same one inside
`BASE_URL` — Serve `funnel`, **Serve Port equal to `PORT`**, State Dir
`/var/lib/tailscale`.

Then **Apply**, never Restart. Restart reboots the existing container with the
old configuration; only Apply recreates it from the updated template.

⚠ **Updating an existing container rather than creating one?** Unraid does not
propagate a variable a template adds later, and does not remove one a template
stops declaring. New fields arrive empty and dead ones stay in the form. Fill
the new ones in by hand, and delete the dead ones — they do no harm, but they
are knobs somebody will fill in with care for nothing.

</details>

<details>
<summary><b>6 · First start: Tailscale, and the preflight</b></summary>

Three things happen in order, and the container log is where you read all three.

**One.** Tailscale prints a login link for the node: authorise it. Then, in the
tailnet policy, grant Funnel to `autogroup:member` rather than to a named node —
recreate the container and a policy that names a node stops matching.

**Two.** In the admin console, under Machines, **disable key expiry** for this
node. The preflight refuses to start while it is enabled, on purpose: expiry is
a scheduled outage that works perfectly for six months and then stops.

**Three.** The preflight runs. It prints one line per check, then its own count,
then the service starts. That count is of its own checks — do not compare it
with a number written down anywhere; what matters is that no line says FAIL.

At this point the log says the registry serves **no project yet**, and that is
correct: you have not written one. Step 7.

The startup line is the one to keep:

```
codifier-mcp <version> — engine <version> — starting on 127.0.0.1:3001 —
base_url … — allowed user: … — IP filter: 1 range … —
token store: /data/fastmcp — db: /db (process uid 0) — web UI: http://0.0.0.0:9443
```

It carries the version actually running, the resolved configuration and the HTTP
mode. It is the only place worth believing about any of them.

⚠ **The log page in the browser will not show you that line.** That page is a
ring in memory, and the startup line is printed before the ring exists. Read the
container log.

</details>

<details>
<summary><b>7 · Declare a project — <code>projects.txt</code></b></summary>

The service serves nothing until you do this, and no tool can do it: creating a
project is a gesture that belongs to the person who owns the machine.

The first boot writes `/db/projects.txt`, root-only, with the instructions
inside it. Add one line per project:

```
My Project | <reference code> | <admin code>
```

- **The name is a folder** next to the file, in that spelling, holding the
  project's database; the file itself is named from the slug — `My Project` →
  `my-project.db`. Renaming a project means editing the line **and** renaming
  the folder.
- **Blank lines and lines starting with `#`** are comments.
- **The codes** are the two from step 3: 8 to 32 letters and digits, no code
  twice in the file, and a project's two codes not equal to each other.
- **The file is re-read when its mtime changes**, so adding a project needs no
  restart. A file that will not parse stops everything, quoting the offending
  line, rather than serving half a truth — and the mtime is stamped only on a
  parse that succeeded, or a broken edit would be read once, fail, and leave the
  service quietly serving the last good version for ever.
- **0600, root only**, because the codes are in clear. The mode is re-imposed at
  every re-read: an editor that writes a new file and renames it over the old
  one brings its own mode with it.

A line with no database creates one, empty and current, and says so in the log.
A database with no line is not served.

⚠ **`created empty database for …` is normal exactly once**, the day the project
is born, and suspicious on any other day: it means the registry line and the
folder on disk are two gestures and only one of them was made.

**There is no migration.** A database of a different schema generation is
refused at boot, naming the file and the two numbers, never silently upgraded.
A v4 registry starts empty.

</details>

<details>
<summary><b>8 · Connect, and the calls that prove it</b></summary>

In the client: **Settings → Connectors → Add custom connector**, URL
`https://<host>.<tailnet>.ts.net/mcp`. The GitHub login opens, you authorise,
and the tools appear.

⚠ **Then open a NEW conversation.** A chat that was open before the connector
was added sees the old surface — see step 9.

Try these, in order. The refusals matter more than the successes.

```
project_info("<reference code>")     → the project: zero domains, zero consumers
project_info("My Project")           → must be REFUSED: a name is not a key
project_info("Zzzzzzzz99")           → must be refused with the SAME message
reference_guide()                    → the working manual
reference_guide(project="<ref>", key="<admin>")    → the administration manual
reference_guide(project="<ref>", key="wrong")      → must be REFUSED
```

If the second and third answer *differently*, stop and report it: a wrong code
and a missing one must be indistinguishable, or the refusal is an oracle.

Then the round trip that proves the machine, end to end:

```
project_amend(project="<ref>", entity="domain", name="PE", action="create",
              by="architect", fields={"description": "…"}, reason="…",
              key="<admin>")
project_amend(project="<ref>", entity="consumer", name="architect",
              action="create", by="architect", fields={"kind": "chat"},
              key="<admin>")
rules_propose(project="<ref>", domain="PE", type="F", title="…", body="…",
              reason="…", reach="all", proposed_by="architect")
rules_list(project="<ref>", consumer="architect")   → count 0: a proposal is not a rule
```

Now go to `http://<server-ip>:9443/`, sign in, open the project, approve the
batch — and `rules_list` answers with the rule, `reaches_you: everyone`. Mint a
one-time code on the project's **codes** page, retire the rule with it, then try
the *same* one-time code on another gesture: it must be refused. That is the
proof that the second factor is a factor.

Finally, paste the **reference code** at the top of that project's chat
instructions. From then on only conversations started inside that project have
it in context.

</details>

<details>
<summary><b>9 · After any change to the tools</b></summary>

There are **three cache layers**: the server, the connector and the chat
session.

After any change to the tool surface — names, parameters, descriptions —
**reconnect the connector and test in a NEW conversation**. A session's catalogue
is frozen at the moment the session was born, and a stale session does not fail
loudly: the client does not validate arguments against the schema, so a call
written for the new surface can travel through an old catalogue and work, which
proves nothing at all.

The honest test is to compare the tool description you have in hand with the one
in the image at that tag, or to call something that did not exist before.

Changes to internal behaviour — limits, formats, logic — do not alter the
surface: recreating the container is enough.

</details>

<details>
<summary><b>10 · Updating, and the way back</b></summary>

A release is a `v*` tag: the workflow runs the suites first and publishes only
then.

The registry does not knock — Unraid finds out when asked. **Check for Updates**
on the Docker page, then apply what it offers, and read the **startup line**
afterwards: it carries the version, and that is how you know the new image is
running rather than the old one restarting. If the surface moved, do step 9 too.

**If the template changed**, download it again over
`/boot/config/plugins/dockerMan/templates-user/`, refresh, and check the form for
fields that arrived empty and fields that should no longer be there — Unraid
does neither for you.

**The way back** costs one field: the previous tag is still on the registry, so
put it in `Repository` in place of `:latest` and Apply. Everything that matters
lives outside the image — the databases, the tokens, the Tailscale identity.

⚠ **The data has no way back across a schema generation.** There is no
migration, by design: a database of another generation is refused at boot rather
than upgraded, so a downgrade past one is a restore from backup, not a rollback.

</details>

---

## Maintenance and failures

<details>
<summary><b>The safe — what cannot be regenerated</b></summary>

| Item | Where it lives | If you lose it |
|---|---|---|
| `GITHUB_CLIENT_ID` + `SECRET` | the GitHub OAuth App | make a new one in five minutes, then update the template |
| `JWT_SIGNING_KEY` | only in the template | issued tokens become unreadable: reconnect the connector. **But never change it without reason** — the effect is the same |
| `WEB_UI_PASSWORD` | only in the template | no recovery and no second account: set a new one and Apply |
| **`projects.txt`** | the database directory | the reference and admin codes of every project, in clear and nowhere else. Rewriting it means new codes, and re-pasting them into every chat instruction and skill file |
| **The databases** | one per project, plus `backup/` | the only real loss |

⚠ **`projects.txt` is not in the backups.** `VACUUM INTO` copies one project's
database, so a copy carried off-site cannot open a project on its own — which is
the intent. The registry is protected by the snapshot of the directory, and by
nothing else.

⚠ The template Unraid saves under `/boot/config/plugins/dockerMan/templates-user/`
holds the secrets **in plaintext**, masked fields included: the masking is only
in the UI. That backup is sensitive material; the shareable copy is the sanitised
template in this repository.

</details>

<details>
<summary><b>Traps already paid for</b></summary>

Each of these cost at least one evening.

- **Unraid does not propagate a new variable to containers that already exist.**
  Add one to the template and an installed container never sees it: it falls
  back to the code's default, or does not start if the variable is required. The
  symptom is the worst kind — *the template is right and the service behaves like
  the old version*. New variables are therefore born optional here, with a
  working default in the code. The single exception is `WEB_UI_PASSWORD`, because
  a default for it **is** the placeholder the preflight refuses. Unraid does not
  remove a field either, so dead knobs stay in the form until you delete them.
- **A port you did not publish looks like a service that never started.** The
  administration page needs the port mapping *and* the variable — two halves of
  one decision. Forget the mapping and the container is up, the log is clean, and
  the page answers only inside the container.
- **Restart ≠ Apply.** Restart reuses the old configuration and looks entirely
  normal.
- **The container log is wiped by every Apply** — that is, exactly when you
  re-run the thing whose failure you were trying to read. Read the log *at* the
  Apply, not after the next one.
- **The browser's log page will not show you the startup line.** It is a ring in
  memory, and the startup line and the preflight are printed before the ring
  exists. Six weeks went by before anyone worked out why the line was never
  there.
- **In WAL a database is three files** — `.db`, `-wal`, `-shm`. Copying the first
  one by hand produces a backup that looks taken and is corrupt. Use the page:
  `VACUUM INTO` writes one quiescent file that opens without recovery.
- **Docker's build cache lies.** It has been known to report `CACHED` for a layer
  whose file had changed. Always `--no-cache` after touching sources, or you lose
  an hour testing the old image, convinced you fixed something.
- **A ghcr package is born private, even from a public repository.** `docker pull`
  answers `denied`, which reads like a typo in the image name. It is two clicks
  in the profile's package settings — two clicks, not two hours of reasoning.
- **Funnel permission is tied to the node identity.** Recreate the container and
  lose `ts-state`, and the node comes back as new with the Funnel needing
  re-authorisation. If Tailscale asks you to re-authorise during an ordinary
  update, that is not a step of the procedure: it is the symptom that the mount is
  missing. Grant Funnel to `autogroup:member` rather than to a named node, and
  never share `ts-state` between two containers.
- **Node key expiry is a scheduled outage.** Disable it. Preflight checks it
  precisely because it is silent: everything works for six months, then stops.
- **The registry's mode does not survive an editor.** `projects.txt` is written
  by a person, over a share, and an editor that writes a new file and renames it
  over the old one brings its own mode with it. Hence the mode is re-imposed at
  every re-read — and `entrypoint.sh` excludes that one file by name from its
  permissions sweep, because without the exception every restart reopened to
  everybody the file where the codes are in clear.

</details>

<details>
<summary><b>The service will not start</b></summary>

The preflight names the failing check, on stdout, whatever `LOG_LEVEL` says. The
frequent ones:

| Check | What to look at |
|---|---|
| `db` | the registry will not parse — the message quotes the line — or the mount is wrong. A database of another **schema generation** also lands here, naming the file and the two numbers: that is the refusal of migration, not a fault |
| `schema` | a database has had objects removed and the automatic repair was not enough |
| `ownership` | the mount is a network share, or permissions were changed by hand, or `projects.txt` is not 600 |
| `approval` | `ADMIN_AUTH_CODE_DURATION` is not a positive whole number |
| `mail` | `SMTP_PORT` is not a port number, `SMTP_SECURITY` is not one of the three, or `SMTP_HOST` is set with no `SMTP_FROM` beside it. It never opens a socket: a preflight that did would put the boot at the mercy of somebody else's network |
| `web` | `WEB_UI_PASSWORD` missing, still `CHANGEME`, or under twelve characters; or `WEB_PORT` is 443, 8443, 10000, or equal to the MCP port |
| `oauth` | a `CHANGEME` left in place, or `BASE_URL` that is not https |
| `token_store` | `FASTMCP_HOME` is not under `/data`: tokens would not survive |
| `funnel` | the Funnel is off, or `PORT` and Tailscale Serve Port differ |
| `node_key` | the node key still has an expiry date |
| `public_dns` | the hostname in `BASE_URL` does not resolve |
| `manuals` | a manual is missing from the image, or one has no `# COMMANDS` section to cut cards at |

The preflight is blocking and exits 2: the server is never reached. A check that
crashes counts as failed.

</details>

<details>
<summary><b>The connector will not connect</b></summary>

Almost always `BASE_URL` does not match the callback registered on GitHub
**exactly** — scheme included, trailing slash included.

The other two causes look identical from the client, and the log tells them
apart:

- **the GitHub login is not the allowed one**, or the source IP is outside
  `ALLOWED_CIDRS`. Since the gate covers the handshake, a stranger does not get a
  session at all — so at the client a refused visitor and a broken deployment
  produce the same message. The refusal line in the log is the only thing that
  distinguishes them, and it disappears above `WARNING`;
- **the service is up and the tools do not appear**: that is caching. Reconnect
  the connector and open a fresh conversation.

</details>

<details>
<summary><b>The administration page does not answer</b></summary>

In this order:

1. **The port mapping.** `9443` must be published on the server's IP. Without it
   the page answers only inside the container, and everything else looks fine.
2. **The address.** `http://<server-ip>:9443/` — plain HTTP, on the LAN. It is
   not published by the Funnel and must not be.
3. **The password.** There is no recovery: set a new `WEB_UI_PASSWORD` and Apply.
4. **A session that ended.** Every restart of the service invalidates every
   session, deliberately — the session secret is generated at boot and stored
   nowhere. So do eight hours of inactivity.

</details>

<details>
<summary><b>Something got messed up in a project</b></summary>

Nothing is deleted, so most of what looks like damage is readable:

```
rules_get(project, ids, consumer, history=True)   how the rule got here
project_status(project, key)                      what the working half cannot see:
                                                  dangling citations, stray audience
                                                  rows, retired names, the queue
```

A rule that should not be in force is retired, not removed, and the retirement
costs a reason and a one-time code on purpose.

If the file itself is damaged, restore the last quiescent copy from `backup/` and
check it before trusting it:

```sh
sqlite3 <file> "PRAGMA integrity_check"     # must say ok, with no recovery
```

Underneath everything sits the snapshot of the database directory, which is the
net for when the backups are gone too — and the only thing that covers
`projects.txt`.

</details>

<details>
<summary><b>Backups</b></summary>

The administration page takes one, per project, from that project's own page: a
`VACUUM INTO` into `BACKUP_DIR`, named after the project's slug and the UTC
minute — `my-project-20260813T061008Z.db`. It does not ask for the password
again, because it changes nothing.

To do it on a schedule instead, the shape is one loop over the project folders:

```sh
ROOT=/mnt/<pool>/<share>/Database/codifier
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
for db in "$ROOT"/*/*.db; do
  name=$(basename "$db" .db)
  sqlite3 "$db" "VACUUM INTO '$ROOT/backup/$name-$STAMP.db'"
done
# then prune: keep the last fifteen per project
```

⚠ **Never copy the three WAL files by hand instead.** And remember what the copy
does *not* contain: `projects.txt`, which is what a restored database would need
in order to be served at all.

</details>

---

## The administration page

Approving a rule is not the same act as writing one, and from v2.1 they no
longer happen in the same place. A chat proposes; a person approves, in a
browser, on the LAN.

The page is served by the same process, on a second port — 9443 by default —
because two processes on one SQLite database do not share the engine's lock. The
home page lists the projects the registry serves, by NAME: the person has
already proved who they are with the password, and a URL may carry a name where
a chat may only carry a code. Everything below it is per project.

The lot page shows the pending batch **whole and side by side**, each proposal
with the reason it was filed: that is where three proposals saying the same
thing become visible as what they are. You tick what goes in, give a reason for
what does not, and type the password **once for the action** — four rules are
not four passwords, and a password typed four times is typed without looking.

A proposal that supersedes a rule says so **before** you decide, with the
victim's ID and its current title: approving it retires that rule in the same
transaction, and whoever approves reads both halves of the move.

The digest covers what you were **looking at**, not what you ticked. If a
proposal arrives while you read, the action comes back refused with the page as
it now is — the same digest contract the MCP tool used to carry.

Beside it, readings that write nothing: the rules in force for a chosen
consumer, exactly as that consumer's chat reads them, brief first; a rule's
detail with its history and the diff between two versions; and the state of the
project. And three pages that write without touching a rule: **codes**, where
one-time authorisation codes are minted — press the button again and the new
one joins the ones already on the page, two spaces apart, so three codes are
one drag — **profile**, and **log**.

**The profile page is where the project talks about itself** — its `brief`, its
`specs` and its `queue_cap` — and since v5.0.0 that is the only place they can
be written from. No tool reaches them, not even with the admin code. The brief
is the project's identity and the specs are the facts every reading is done
against: what is FUNDATIVE has no tool, the way what is catastrophic has none. A
chat may suggest the wording; the change is a person's.

**The people page is where the project's PEOPLE are** — added, addressed,
marked, retired. Same sentence, said about the anagrafica: a chat and a skill
are machinery and a tool manages them, a person is not. The mark that says whose
desk hears about a proposal grants nothing at all: what opens this UI is the
password, and this only says where an email goes.

**The log page is the whole task log at once** — every entry in the project,
grouped by the desk it sits on or by who sent it, open only or open and closed,
with the two gestures a log needs under each one: close it, or correct it and
hand it to another desk. Nothing on it is capped and every entry carries its
body, because a page that showed you nine of eleven is a page you cannot work
from. Gestures made there are signed `web ui` in the history — what it
witnessed is that somebody closed this at the admin page, and a field that
typed a name would be a field that typed somebody else's.

**The password is typed once, at the door, and nowhere else.** Until v7.0.0
every writing gesture asked for it again, on the ground that a session alone is
a browser left open on the iPad; that guard is gone, for the reason the design
had already written against it — a secret typed five times an hour is typed
without looking, and a password typed without looking defends nothing while
costing every gesture. What is left is one password from the template, a signed
cookie, **eight hours of inactivity**, and a port that does not leave the
tailnet. A restart of the service invalidates every session, deliberately: the
session secret is generated at boot and stored nowhere. The one-time codes are
untouched — they guard the MCP surface, where the caller is a chat and not a
person.

**What is not here any more, since v4.0.0: the deployment page.** It created
projects, rekeyed them and printed their codes, and all three died with the
declarative registry — a project is now a line in `projects.txt`, written from
the server by the person who chooses its codes. What took its place is the codes
page: minting one-time codes is the one thing the design gives to this UI and to
nothing else.

## Notifications

Three things are posted, and no more: a **task opened on a person's desk**, a
**task moved ONTO a person's desk**, and a **proposal entering the queue**.
Each at the moment it happens, to the address on that person's row — a human is
the only kind of consumer that may carry one, and the schema refuses it on a
chat or a skill. Proposals go to the project's **approver**, the one human
marked for it on the profile page.

⚠ **The second one is an arrival and not an edit**, and it was added in 6.1.0
because its absence was a hole with a shape: a task opened on the wrong desk
and then handed to the right person reached them NEVER — the very gesture that
corrects the mistake was the one that swallowed the notice. Only a change of
DESK posts; fixing a typo wakes nobody, and the desk a task LEAVES is not
written to, because the register never posts a subtraction.

### Closing a task from the button in its email

A task posted to a person carries a **Close it** button. It opens a page
showing that one entry — its text, whose desk it is on — with two boxes: what
came of it, or why it will not be done. Writing one and pressing is the whole
gesture, and it asks for **no password**.

- **The ticket in the link is the credential**, and it is a signature rather
  than a row: HMAC over the project, the entry and an expiry, keyed on the
  project's admin code. ⚠ **No table, and that is the design**: a table is a
  column is a schema generation, and this register is loaded — a generation
  costs the corpus by hand and every open entry outright. Rotating the admin
  code voids every link already sent, which is the revocation this would
  otherwise not have.
- **It is single-use without being single-use.** Nothing spends the ticket,
  because `closed is closed`: the second press finds the entry closed and gets
  the refusal that already exists.
- **The GET never closes anything.** It renders a form. A link that acted on
  being fetched would be pressed by the first mail client that prefetches
  links, and nobody would ever know which one.
- ⚠ **What makes this acceptable is a PREMISE, not a detail**: the ticket
  travels in clear through somebody else's mail relay, and it is worth exactly
  as much as reaching that port is — the UI does not answer outside the
  tailnet, and the Funnel cannot publish it. Publish that port anywhere else
  and this is a hole.
- **It expires** — `TASK_LINK_DAYS`, 14 by default. Days and not minutes: a
  one-time code is minted for a gesture about to happen, a task on a desk
  waits. An expired link says so and leaves the entry untouched.
- **It closes signed by the desk it was sent to**, which is what the link is:
  it went to that desk and to no other. The page does not reach for
  administrator powers to do it.

**The subject is the whole headline, and says it once.** `TK-0003 — Aprimi su
iPad`, or `Proposed Rule VA-0007 — <title>`, because that is the line an inbox
list shows, and a reply, and a search. It is not printed again inside. `TK-`
already says *task*, which is why the word is not in a task's subject; `VA-`
does not say *proposed*, which is why a proposal's spells it out. ⚠ The title
is CUT at 70 characters there.

**And the message is four things** — five when there is a button: the
**project's name**, the one word that changes between messages and tells you
which register just spoke; **`Sender:`** and who spoke, in the size between
that name and the prose, because it is the one thing the subject cannot carry;
**the task's own text**, at the size prose is read at; **a button that closes
it**, when `WEB_BASE_URL` is set; and a **small footnote** saying where it is
answered. Nothing else, no disclaimer, and no picture — the sender's card in the address book
draws that better than anything sent from here, and in the message list too.

⚠ **The text travels, and that reverses a rule this file carried for an
afternoon.** The message was a knock on the door — the ID, who sent it, where
to read it — on the argument that the register is where the work is read. The
argument was written by somebody who was not reading these on a tablet: a
person who has to open a register to find out whether a thing matters will open
it late, and the notification will have cost them a gesture to learn nothing. A
task's text is a paragraph, not a document. It is capped at **4000
characters**, cut at the end and visibly — a ceiling, not a summary.

⚠ **Markdown arrives verbatim.** Asterisks and hashes typed into a body are the
characters that arrive: the register stores prose, not markup, and a mail that
reformatted it would be showing something nobody wrote.

The message sets **no background colour**, on purpose: Apple Mail in dark mode
inverts a message that sets none, and a card painted white stays a white card
with unreadable text on it.

**People are looked after on the page, all of it.** A consumer of kind
`human` is created, renamed, retired and revived there, their address is typed
there, and the mark that says whose desk hears about the proposals is set there
— in one gesture with the addresses, because they are one question.

The reason is not security. A `chat` and a `skill` are machinery, and machinery
is managed by machinery: `project_amend` wires the project up. A person is not a
row a chat invents, renames or retires. It is the same sentence as *what is
fundative has no tool*, said about the anagrafica instead of about the profile,
and the tools refuse every one of those four actions on a human saying where
they live. What a chat CAN do with a person is put work on their desk.

The page reaches the engine through the SAME method the tool calls, with a flag
that lifts that refusal — not through a road of its own. So every guard already
written keeps working there unduplicated: a name is one word, a retired name is
still taken, a retirement that would leave a rule binding nobody is refused. A
page with its own copy of those rules would be a page with one of them out of
step. The flag also stands in for the one-time code, and for the reason that
code exists: it is there so a chat holding the admin code cannot modify alone,
and on the page there is no chat — there is a person who came through a door no
tool can open.

**There is no on/off switch, anywhere.** The two ways to be quiet are both
ABSENCES: no `SMTP_HOST` on the container, so nothing is ever sent; no address
on a consumer, so that person is not written to. Two ways to turn something off
is one too many — the day the post stops arriving, somebody has to work out
which of them did it.

**No digest.** A roll-up would have to know what is scheduled and when the
night's runs have finished, and a container knows neither, so this one composes
none and has no scheduler. `tasks_overview` is already the payload one is made
of, and making it is a skill's job.

**At most ten a day, per project.** Per project and not per container, because a
runaway in one must not silence another. The tenth message carries the notice
that the sender is paused until tomorrow, so the pause is visible in the post
and not only in the log. It lives in memory, so a restart forgets it — which is
right: the loop this guards against lives inside a session. The ceiling is not
there against twenty messages, which would be information; it is there against a
skill looping on `tasks_add` and burning a month's allowance in an afternoon.
⚠ The arithmetic is written down in `mail.py` so it can be redone: two projects
is about 600 a month, comfortably inside a free thousand; four does not fit.

**A message that cannot be sent never fails a gesture.** The post leaves after
the write has committed, from a module of its own, and a failure is a line at
WARNING. A notification that can make a write fail is worse than no
notification.

## The task log

Rules are what BINDS a consumer. Tasks are what is WAITING for it — a
different thing, and modelled as one: no audience, no approval, no signature,
no expiry. The log exists so that *what is open for me?* is a single call,
and so is *what did I do lately?*, because closing a task costs a written
outcome. It replaces both the per-role changelog and the "pending" section
role memories used to keep.

IDs are `TK-NNNN`, never reused, cited like a rule: `(TK-0012)`. `TK` cannot
be declared as a domain of rules — the registry refuses — because the code
has to mean one thing.

**Anybody may open a task for anybody**, which is how an audit hands each
correction to the role that owns it. `created_by` is mandatory. Opening one for
a **human who carries an address** emails them — see *Notifications* below.
**Closing costs a sentence**: `tasks_close`
takes an `outcome` that completes it or a `reason` that drops it, exactly one of
the two, and the refusal is in the schema as well as at the door. **Closed is
closed** — an open entry is amended, a closed one not at all. **Both ends may
amend an open one**: the desk it sits on and whoever SENT it; anybody else
takes the admin code. Until v7.0.0 only the desk could, and the sender who had
written the wrong thing had no way back except opening a second entry or
reaching for a credential to fix their own typo.

### A task and a message: ask who will close it, and why

The log has two kinds of entry, and they are told apart by one question rather
than by a list of properties.

| | a **task** | a **message** |
|---|---|---|
| what it is | work that must happen | a condition that can pass |
| who closes it | the desk that DID it | its desk **or the one who sent it** |
| what closing means | somebody acted, and the outcome says what came of it | the condition is gone — possibly with nothing done |
| closing words | always owed | may be omitted: the engine writes `closed by <who> on <date>` |
| if nobody ever looks | it stays on the desk, and comes back marked stale | it may be opened and closed unseen, and that is correct |

Work that must happen is a **task**, even when it reads like news. A condition
that can stop mattering on its own is a **message**, even when it asks for
attention — the tax monitor says a statement is missing, the statement
arrives, and the same skill closes its own message on the next round.

⚠ A **task** is also how one chat asks another for something (*look at this
proposal and tell me what you think*). That is a request, not a
`kind='message'`, and the two words are worth keeping apart: what makes it a
task is that somebody has to answer it. ⚠ And a **message is not a push
notification** — a chat sees its desk when it starts, so one opened and closed
between two starts is one nobody will ever see. For something that has to be
read regardless, open a task.

**`urgent` belongs to whoever created the task** and cannot be changed by
anyone afterwards, because the receiver is the party with an interest in
clearing it. There are no levels, and there is **no automatic guard against
inflation**: `tasks_overview` counts the urgent per DESK, which answers *who is
being buried* and not *who is doing the burying*. ⚠ This file used to say the
count was BY CREATOR. It is not, it never was, and the sentence had been quoted
as an argument — so it is corrected here rather than quietly dropped: what
holds urgency honest is that it is permanent and visible, not a tally nobody
keeps.

**Tasks do not expire.** One open past thirty days comes back marked, and
that is all: an automatic expiry would be a drop with no reason, written by
the clock. Lists are the short form and the server orders them — urgent
first, then oldest first — so when a ceiling bites the cut falls on the
fresh work and never on what has been waiting. Truncation is always
declared, with the real total.

---

## Usage guide

<details>
<summary><b>The rules of the house</b></summary>

**1. Read at the start, not when you get stuck.** `rules_list` is the first call
of a session, and it carries the project's brief, your own, the rules in force
for you and the tasks on your desk. A chat that reads its rules after it has
already decided something is a chat that reads them for nothing.

**2. Nothing you learn survives the conversation unless you file it.** Found
something that deserves a rule? `rules_propose`, and then forget it: it binds
nobody until a person approves it, and the outcome comes back in
`rules_list(pending=True)`.

**3. Reads are project-wide.** The reference code opens every read of the
project — rules, tasks, structure. Within a project there are no secrets between
consumers; the separation is between projects.

**4. The registry assigns the number.** You pass the domain and get the ID back.

**5. Cite with the bare ID in round brackets** — `(VA-0002)` — in any field of
prose, and never with a note of your own inside the brackets. A citation that
does not resolve, or points at something not yet in force, is refused at the
door: the call spends no number and no place in the queue.

**6. Widening is not an edit.** Narrowing a perimeter is `rules_amend`;
widening one, or changing what a rule says, is a supersede that a person
approves.

**7. A refusal is information.** Every one names the field, the value and the
rule that was broken, and nothing is half-written: a refused call leaves the
registry exactly as it was.

</details>

<details>
<summary><b>Which tool for which job</b></summary>

| You want to | Use | Gate |
|---|---|---|
| know what binds you, right now | `rules_list` | reference code |
| read rules in full, with their reasons | `rules_get` | reference code |
| see what has been filed and not yet decided | `rules_list(pending=True)` | reference code |
| propose a rule, or a replacement for one | `rules_propose` | reference code |
| know which domains, consumers and groups exist | `project_info` | reference code |
| see what is waiting on your desk | `rules_list`, or `tasks_list` | reference code |
| read a task in full | `tasks_get` | reference code |
| put work on somebody's desk | `tasks_add` | reference code |
| finish or drop a task | `tasks_close` | reference code |
| fix or reassign an open task | `tasks_amend` | reference code |
| create a domain, a chat, a skill, a group | `project_amend` | admin code |
| rename, retire, revive, change a brief or a group | `project_amend` | admin code **+ one-time code** |
| change your OWN specs, as a consumer | `project_amend` | reference code |
| change the PROJECT's brief, specs or queue ceiling | *no tool* | the administration page |
| add, address, mark or retire a PERSON | *no tool* | the administration page |
| narrow a rule's perimeter | `rules_amend` | admin code **+ one-time code** |
| end a rule that has no heir | `rules_retire` | admin code **+ one-time code** |
| see what the working half cannot — dangling citations, retired names, the queue | `project_status` | admin code |
| pull the corpus out for a migration or a review | `rules_export` | admin code |
| see every desk at once | `tasks_overview` | admin code |
| read the manual, or one command's card | `reference_guide` | none, or both for the admin half |

**Approving is not in this table**, and that is the design: it happens in the
browser, and no tool reaches it.

</details>

<details>
<summary><b>The tools</b></summary>

Every tool takes `project` first — the project's **code**, never its name — and
a test holds these signatures against the code: a README that promises an
argument the tool has not got is the copy that diverges first. What each one
does in full, with the refusals it raises quoted as the service words them, is
in the manual the service itself serves: `reference_guide()` for the model and
the list of card names, `reference_guide("<name>")` for one command.

### The working half — the reference code

    reference_guide(name='', project='', key='')

The manual, in two grains. Bare, it serves the model page plus `cards`, the list
of names you may ask for; with `name`, one card and nothing else. The name is
forgiven surrounding space, capitals and anything from the first bracket on, so
pasting a whole signature back asks for that card, and an unknown name is
refused *with* the list. `project` and `key` together serve the administration
manual instead — a different file this call never opens otherwise.

    project_info(project)

The technical structure of the project, and only what is ALIVE in it: domains
with their gloss, consumers with kind and brief, groups with their live members.
Retired names are not here at all — they are readable only from
`project_status`. Do this first, and find your own consumer name spelled exactly:
a misspelt role fails later in ways that look like something else.

    rules_list(project, consumer, query='', pending=False)

The session-start call, and the one that answers *what binds me right now*. The
answer comes in one order: the project's brief and specs, then yours, then the
legend of the domains present, then your rules in force — universal first, then
groups from the widest, then what was aimed at you by name — and at the foot your
open tasks in short form. `query` filters what you are shown; it does not narrow
what binds you. `pending=True` shows the proposal queue instead, each with its
reason, which is the only place a chat learns that a proposal of its own was
denied.

⚠ **A `skill` consumer does not receive the project's brief and specs.** A skill
runs one job; that material is for whoever deliberates. It arrives **declared** —
`profile: {withheld: "skill", …}` with the reason — and never dropped in silence,
because a missing field reads like an empty project.

    rules_get(project, ids, consumer, history=False)

Rules in full. What binds you is the ID and the body; the rest is there so you
can tell one rule from another and find your way back to the decision. The
`reason` is the WHY and is **immutable** — a why that could be edited afterwards
is a why that gets edited to fit. `history=True` adds the dated gestures, the
hand, and the version number an administrator will be asked for before the rule's
perimeter can move. The short form resolves on a read, so `VA-02` finds
`VA-0002` and an old text does not have to be rewritten to be followed. Too many
IDs at once is refused, not trimmed: a silent cut answers a question you did not
ask.

    rules_propose(project, domain, type, title, body, reason, reach,
                  proposed_by, groups=[], exceptions=[], supersedes='',
                  source='', consumer_key='')

File a rule. It is born `proposed` and binds nobody until a person approves the
batch it is in, on a page no chat can reach. You pass the **domain** and the
registry hands back the ID. `supersedes` is how a decision is changed in one
gesture: approving the heir retires the old rule inside the same transaction.
File it and forget it — the outcome is in `rules_list(pending=True)`.

    tasks_add(project, consumer, title, body, created_by, urgent=False, kind='',
              idem_key='', consumer_key='')

Put work on a desk — yours or anybody's. Opening one for another desk is the
point of the log, and it is free. `created_by` is mandatory, `urgent` belongs to
whoever created it, and `idem_key` is what makes a retry harmless.

    tasks_list(project, consumer, query='', since='', until='',
               authored=False)

One desk, short form, ordered by the server: urgent first, then the oldest, so
when a ceiling cuts it cuts the fresh work. Recently closed ones trail.
`authored=True` turns the question round — what you have put on other people's
desks.

    tasks_get(project, ids)

Tasks in full: title, body, owner, sender, urgency, state, and the outcome or
reason if it is closed. ⚠ A citation read expanded here does not always paste
back: if the rule it points at has since been retired, the door refuses it. The
cure is to rewrite the citation, not to patch the pointer.

    tasks_close(project, id, by, outcome='', reason='', consumer_key='',
                key='')

One gesture with two verdicts: `outcome` completes it, `reason` drops it,
exactly one of the two and neither optional. Only the owner knows how it went,
and a closed task with nothing written is a task nobody can learn from.

    tasks_amend(project, id, by, title='', body='', consumer='',
                consumer_key='', key='')

Fix or reassign an OPEN task; anything left empty is left alone. Closed is
closed, and this is where you meet that.

### The administration half — the admin code

Six tools, and the scale is flat: creating takes the admin code, **modifying
anything that already exists takes the admin code and a one-time code** minted in
the browser and burned inside the transaction of the gesture that succeeded.

    project_amend(project, entity, name, action, by, fields={}, reason='',
                  auth_code='', key='')

The project's STRUCTURE — the one door for all of it. `entity` is `domain |
consumer | group`, `action` is `create | amend | retire | revive`, `name`
identifies the thing, `fields` carries what changes, and `by` is your consumer
name — required, because it is the hand the history records.

The project ITSELF is not here. Its brief, its specs and its queue ceiling are
written by a person on the administration page: what is fundative has no tool,
the way what is catastrophic has none. And a consumer's `specs` are that
consumer's — `specs` under another name is refused, with no exception for the
admin code, and the refusal says to open a task instead. `by` is declared and
not proven: it stops the mistake, never the lie, and inside a login restricted
to one person that is the perimeter that matters.

One exception downward: a consumer's `specs` alone travel on the reference
code, because they are operational data and not identity — presented next to a
field that costs more, the call is refused WHOLE, naming that field.
The cases nobody guesses: a domain's code is immutable; a consumer or group name
is ONE WORD; a name that is amended **stops resolving under the old one**, and
the verdict lists what to update outside the registry, which nothing updates for
you; a retired name is still a name taken, so the way past `create` is `revive`;
and an edit that would leave a rule in force reaching nobody is refused, naming
the rules, because that is a retirement in disguise.

    rules_amend(project, id, reach, groups, exceptions, expected_version,
                reason, auth_code, key)

The perimeter of a rule in force — **narrowed only**, and every argument is
required: there is no partial call here. The new audience must be contained in
the old one and never empty. Widening is refused with the names it would newly
bind, because widening puts an obligation on somebody who did not have it, and
that goes through the page. `expected_version` is what `rules_get(history=True)`
last showed you: if it moved under you nothing is written, and you read it again
rather than overwriting a decision you never saw.

    rules_retire(project, id, reason, auth_code, key)

End a rule that has no heir. Two factors, because the way back is a proposal and
a human approval, and the ID never comes back. **With an heir, do not use this**:
propose the replacement with `supersedes`. Retirement is a state — nothing is
deleted, the rule stays readable through its history, and citations already
written in prose start turning up in `project_status`.

    project_status(project, key)

The report, and the only reading that sees what the working half cannot. It
reports; it does not correct. Counts, the pending queue, `dangling_citations` —
pointers that went broken when their target was retired, which the door cannot
catch because they were valid when written — `stray_audience_rows`, and ⚠ **the
retired names**, which are readable here and nowhere else.

    rules_export(project, key, consumer='', expand=False)

The corpus in one call, for a migration or a review, carrying the `reason` of
every rule — which is what makes an export a document somebody can decide from.
`consumer` narrows it to one desk; `expand=True` renders citations with their
current titles. ⚠ This is the tool that meets **your client's** result cap first:
above it a result stops being data and becomes a file path, useful only if that
file lands where your code runs.

    tasks_overview(project, key)

Every desk at once, read-only: who has what open, how old, what is marked stale.
It answers *is anything waiting on anybody*, which no per-desk call can. Ceilings
are declared in the answer, so a truncated overview never looks like a quiet
project — and this is where a human's post is actually read.

### The limits

They are not repeated here. Every ceiling — how many IDs one read takes, how
large a body may be, how many rules a list returns — is stated in that command's
card and held against the constant by the suite. A third copy in this README
would be the one nothing checks, and therefore the one that goes wrong.

</details>

---

## Security

- **OAuth 2.1 with GitHub, restricted to one username.** That is the front door.
- **Source IP filter**, on top of OAuth and not instead of it. Both checks run
  on every MCP request, the handshake included — not only on tool calls. OAuth
  stops whoever is not authenticated; it does not stop whoever authenticates
  with their own GitHub account, and up to and including v1.1 such a stranger
  could still list every tool with its description. No rule ever left, but the
  shape of the surface did. Note that neither check covers the OAuth routes
  themselves: a stranger outside the allowed ranges can still complete a login.
  What they cannot do is speak MCP.
- **Three credentials, and the scale is flat.** The **reference code** opens
  every read of a project and lets a chat file a proposal — a proposal reaches
  nobody until a person approves it, so it cannot do harm, and asking a working
  chat for anything stronger would put that stronger thing in every chat.
  The **admin code** creates: a domain, a consumer, a group. Modifying anything
  that already exists — a perimeter, a retirement, a rename, a brief, a group's
  membership — takes the admin code **plus a one-time code**, minted on that
  project's page in the browser, shown once, burned inside the transaction of
  the gesture that succeeded. A refusal rolls back and does not consume it;
  alone it elevates nobody. The role does not elevate: the key elevates.
- **What is catastrophic has no tool.** Approving, minting a one-time code and
  creating a project are not on the MCP surface at all — the first two live
  behind the UI's password, the third behind root on the server. A secret of
  that level never travels in a conversation.
- **Two manuals, in two files.** `reference_guide()` bare serves the consumer's
  half; the administration half needs the project code *and* the admin code.
  They are two files rather than one text cut at a marker, so "the admin manual
  served without a key" is not a failure to test for — it is one that cannot
  happen.
- **The registry file is the safe.** `projects.txt` holds every reference and
  admin code in clear, which is the decision, and it is the one file here that
  is root-only, 0600. The mode is re-imposed at every re-read, not only at
  creation: it is edited from a share, and an editor that writes a new file and
  renames it over the old one brings its own mode with it. The line the service
  prints at boot names the file and how many projects it serves — and no code:
  that message went through the container log, which is what gets pasted into a
  conversation when something is wrong.
- **A malformed call does not print what it carried.** FastMCP validates
  arguments before any tool runs and logs what it rejected, with the arguments
  in the line — a record that obeys no LOG_LEVEL of ours and leaves no
  `refused` line, so a clean log is no evidence it did not happen. Here those
  arguments are the project's codes. From v2.1.1 the payload is redacted and the
  diagnosis is not: the tool, the parameter and the rule that was broken all
  survive.
- **The process runs as root and the databases are 0644.** This is the opposite
  of the vault twin, deliberately: from the share you read and you do not touch,
  because a write by hand would bypass the triggers and break history in
  silence.
- **Project codes are not a security boundary.** They are opaque so projects
  cannot stumble into each other; no tool lists them and no error names one, and
  a wrong code answers exactly like a missing one. The real boundary is the
  OAuth gate in front.

---

## What it deliberately does not do

**No approval tool.** Promulgating a rule is a person's act, and it happens in a
browser behind a password no tool carries. That is the whole point of the split:
redacting and promulgating stop being the same power.

**No project list, and no oracle.** No tool enumerates projects, no error names
one, and a wrong code answers exactly like a missing one. A chat either has the
code in its instructions or it asks the person.

**No delete, and no reuse.** Retiring is a state. An ID is never handed out
twice, in any domain, ever.

**No migration between schema generations.** A database this build does not
recognise is refused at boot by name, never upgraded in place. The corpus comes
back through the door, which is slower and leaves a record.

**No dumps by default.** Every tool answers a question rather than emptying a
table, because every byte coming back lands in a conversation's context, and
context is the scarce resource. `rules_export` is the exception, and it says so.

**No numbering-gap report, no per-rule notification, no levels of urgency.**
Each of those would be a mechanism papering over a decision somebody should
take.

---

<details>
<summary><b>Testing, and changing this</b></summary>

Five suites. No network, no FastMCP, no Docker.

```
python3 test_schema.py      # the DDL: triggers, constraints, generation
python3 test_registry.py    # projects.txt, the router, the refusals it raises
python3 test_collaudo.py    # the engine, refusals included
python3 test_surface.py     # the seam: the image, the template, the manuals
python3 test_crash.py       # SIGKILL mid-transaction, as Docker does
```

Each suite prints its own count, and no file repeats it. A number written down
in two places is two numbers, and this project has already paid for that once.

`test_surface.py` reads the source rather than running it, from the AST rather
than by searching the text — a substring search is satisfied by a line that has
been commented out. Every call into the engine must exist with a compatible
signature; every tool that writes must pass the gate it claims; no docstring or
manual may name a tool that does not exist; every variable the template declares
must have a reader in the code; every module the server imports must appear in a
`COPY` line of the Dockerfile; the version badge and the tool-count badge in this
README must match the constant and the AST; and every signature written in this
README must match the code **in both directions** — every parameter named, and
no parameter invented.

Two rules of the house, if you change anything here:

- **A control never seen to fail is not a control.** Inject the defect, watch
  the red, and check that it *names* the culprit — which tool, which value,
  which line. Several checks in that suite were written, passed, and were then
  found to be measuring nothing.
- **A variable added later is born optional**, with a working default in the
  code, because Unraid does not propagate new variables to containers that
  already exist.

If you fork this, four traps are in the code rather than in the deployment, and
all four are the FastMCP ones any self-hosted server of this shape will meet:

- **Sync tools run in a thread from a pool** (`anyio.to_thread.run_sync`), so a
  SQLite connection opened at import dies on the first call. The cure is
  `check_same_thread=False` **plus** a re-entrant lock held for the whole of
  every public method — half the cure is worse than none, because without the
  lock two multi-statement transactions interleave and one `COMMIT` closes
  another's, in silence.
- **A deliberate refusal must not look like a crash.** FastMCP logs its own
  error type without a traceback and everything else with one, so a refusal you
  raise arrives as a fault and the log fills with damage that is not damage. It
  is fixed with a `ToolError` carrying a log level, raised from a decorator
  wrapping the tool — **not** from a middleware: `call_tool` applies middleware
  outside and logs inside.
- **`workflow_dispatch` publishes `:latest` without ever comparing the version
  constant to the tag.** Gate the `latest` tag on `refs/tags/v`, or every
  installation pulls an image nothing checked.
- **The Dockerfile lists its `COPY` lines by hand.** A missing module kills the
  container at boot, after a tag; a missing `.md` kills nothing and serves an
  empty manual, which is worse. And `COPY *.py` puts the test files in the
  image.

</details>

---

## Package contents

| File | |
|---|---|
| `rules.py` | the engine: the registry, the projects, the DDL and the version |
| `server.py` | the MCP tools; parameters in the schema, prose in the manuals |
| `web.py` | the administration page: sessions, the lot and its digest, the codes |
| `mail.py` | the post: what a message looks like, the relay, the daily brake |
| `preflight.py` | the blocking startup checks, and the IP-filter parser |
| `reference-guide.md` · `reference-guide-admin.md` | the two manuals, cut into cards and served from the image |
| `entrypoint.sh` | permissions, preflight, start |
| `Dockerfile` · `requirements.txt` | the image, and the engine pinned to a tag |
| `codifier-mcp.xml` | Unraid template, every field documented — it **is** the configuration |
| `codifier-icon.png` · `codifier-icon-64.png` | the icon, in two sizes, used in **two** places — see below. Neither is in the image |
| `test_*.py` | the five suites, no network needed |

### The icon, and where it is actually seen

`codifier-icon.png` is pointed at by its raw GitHub URL from two files: the
Unraid template, which puts it on the container, and `server.py`, which passes
it to FastMCP as `icons=[…]`. A check compares the two URLs, because two hand
copies of one string have an expiry date.

Passing `icons` buys **the OAuth consent page** — the page seen when the
connector is added or reconnected — where FastMCP renders it in place of its
own logo.

**The post was briefly a third place, and is not one any more.** `mail.py`
embedded `codifier-icon-64.png` in every message for one afternoon. Two rounds
of shrinking it showed that Apple Mail was never obeying the `width` attribute
— the number in the code and the number on the screen were never the same
number — and then a **card in the address book** made the client draw the
sender's picture itself, in the message list as well, where nothing here could
ever have put it. So the right size for a logo we do not control turned out to
be none. The 64px file stays in the repository, out of the image and unused.

It does **not** buy the icon in Claude's connector list. That surface ignores
`serverInfo.icons`, which the MCP spec has carried since revision `2025-11-25`
(SEP-973); serving `/favicon.ico` and a root page with `<link rel="icon">` are
ignored as well. The tracking issue is
[anthropics/claude-ai-mcp#152](https://github.com/anthropics/claude-ai-mcp/issues/152).
Under a Tailscale Funnel the list shows Tailscale's icon, which is consistent
with that surface deriving the icon from the DOMAIN — nothing in this
repository can reach it. The field is sent anyway: the day the client reads it,
the list follows with no change here.

## Sibling

[archivist-mcp](https://github.com/alcor6502/archivist-mcp) — a document vault
with per-dataset git versioning. Same architecture, same OAuth gate, same
blocking preflight, and both adopt
[mcp-common-engine](https://github.com/alcor6502/mcp-common-engine), which each
pins to a tag. That one keeps files; this one keeps rules.

## Licence

MIT — see [LICENSE](LICENSE).
