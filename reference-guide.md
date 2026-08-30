# Codifier MCP — manual

This page is the MODEL: what a signature cannot say, and what has to be known
before anything at all can be done. It is short on purpose, and it is meant to
be read once.

> ⚠ **If anything about a command is unclear, ask for its CARD:**
> **`reference_guide("<name>")`.** The card explains that one command in full —
> every argument that does not explain itself, the cases nobody guesses, and the
> refusals it can hand you with what to do about each. This page is only the
> model. The names you may ask for come back with this page, in `cards`.

## THE MODEL

A project is a registry of **rules that bind** and **tasks that wait**, plus the
people of the project: **consumers** (chats, skills, humans), their **groups**,
and the project's own **profile** — its brief and its specs.

Rules are law: a chat proposes, a human approves on a web page no chat can
reach. **A TASK is work that waits** — opening one for someone else is normal,
and closing one costs an outcome or a reason. **A MESSAGE is a condition that
decays**: same log, same commands, one letter-pair of difference in the ID, and
the one who sent it may close it when the reason is gone.

Every call names the project by its **reference code**, never by name. You are a
**consumer**: your name is in your instructions, and it is what you pass in
`consumer`. A consumer name and a group name are ONE WORD — letters, digits,
`-` and `_`, no spaces. If you do not know your own, stop and ask.

**`reach` is who a rule binds**, and you read it on every rule line: `all` —
everyone, no audience row — or `targeted`, and then the audience is groups UNION
exceptions. It is declared, never deduced.

There is an administration half to this service, with a manual of its own. If
you were given an admin code you already know; if you were not, you do not need
it, and nothing on this page depends on it.

## SESSION START

One call:

    rules_list(project, consumer)

The PROJECT's brief and specs, then your **brief** and your specs, then the
legend of the domains present, then your rules in force, then your open tasks
in short form.

⚠ **If your consumer is a `skill`, the project's brief and specs do not come.**
`profile` comes back saying `withheld` and why — never empty, never silently
dropped. **Your own brief and specs still come**: they are your mandate.

Then `project_info(project)`, the TECHNICAL half — domains, consumers, groups.
⚠ **Find your own consumer name in that list, spelled exactly, before you go any
further.** If it is not there your role is retired or misspelt, and nothing
further you call will say so this plainly.

Empty set or silent registry: stop and say so.

## CITING

Rules are cited as `(VA-0002)`, tasks as `(TK-0012)`, messages as `(MS-0001)` —
ID in round brackets, four digits, in every prose field. Reads forgive the short form (`VA-02`
resolves); writes refuse anything else, naming field and token, spending
neither a number nor a queue slot.

**A citation points at something that can still be USED**, and the door is not
the same for a rule and for a task. Which is which is on the card of the command
you are writing with: `rules_propose`, `tasks_add`, `tasks_close`.

## THE RULES OF THE HOUSE

1. **You RECEIVE rules: you do not write them, rewrite them, or quote them from
   memory.** Found something that deserves one? `rules_propose`, and forget it —
   the outcome is in `rules_list(pending=True)`.
2. **Reads are project-wide.** The reference code opens EVERY read of the
   project — rules, tasks, registry: within a project there are no secrets
   between consumers; isolation is BETWEEN projects. Treat the reference code as
   a secret: it lives in your instructions, never in a vault file.
3. **A cap that bites REFUSES rather than trims** — a silent cut answers a
   question you did not ask. Where a list is cut instead, it states the real
   total. Each cap is on the card of the command it governs.
4. **Refusals are `refused` lines that name the port**, the reason, and usually
   the way out. Read the line before you retry: a traceback is a bug, not an
   answer.

# COMMANDS

One card each. `reference_guide("<name>")` returns just one.

## reference_guide(name='', project='', key='')

This manual. Bare, it serves the page above plus `cards`, the list of names you
may ask for. With `name` it serves ONE card and nothing else.

- The name is **forgiven** surrounding space, capitals, and anything from the
  first bracket on — so pasting a whole signature back, the way the manual
  prints it, asks for that card.
- An unknown name is refused **with the list of names**, so a wrong guess costs
  no second call.
- `project` and `key` belong to the administration half. Without them you get
  the working manual, which is this one; the other half is a different FILE this
  call never opens, and its card names are not in the list you get here.
- The answer says which half it served, in `level`.

## project_info(project)

The technical structure of the project, and only what is ALIVE in it: the
domains with their gloss, the consumers with kind, brief and specs, the groups
with their live members, and three counts. Retired names are not here at all —
they are readable only from the administration side. The domains that number
the log are not here either: this is a list of places a rule can be FILED, and
those are not places.

**Do this first, with `rules_list`:** find your own consumer name in the list,
spelled exactly. A misspelt or retired role fails later in ways that look like
something else.

- ⚠ **Every consumer carries `signed`**, and it is the field to read before you
  write anything in somebody's name: `true` means that consumer's gestures have
  to be signed as well as addressed, `false` means the name alone is enough.
  The secret itself never leaves the database — `signed` says only whether
  there is one, and which of the two worlds you are in.
- **A `human`'s row is a DIFFERENT SHAPE**, and the payload says so rather than
  showing two nulls: no `brief` and no `specs` — they have no mandate by
  construction — and in their place `posted_to`, whether an address is on the
  row, and `approver`, whether the project's proposals are announced to them.
  The address itself is not here: it is not a working chat's business.

- The refusal you will meet if the code is wrong, and it is ONE answer for four
  different mistakes — nothing passed, the project's NAME instead of its code, a
  code that does not exist, a code with one character missing:

      project not recognised: this needs the project CODE, the one at the top
      of its instructions — not its name, and not a code from somewhere else.
      Without one that resolves the registry does not answer, and there is no
      way to list projects: either you have it, or you ask for it.

  There is no oracle: a wrong code answers exactly like a missing one, and no
  tool lists projects. If you have not got it, ask the person.

## rules_list(project, consumer, query='', pending=False)

The session-start call, and the one that answers *what binds me right now*.

- Order of the answer: the PROJECT's brief and specs — identity first, then the
  living facts — then your brief and your specs, then the legend of the domains
  present, then your rules in force (universal first, then groups from the
  widest, then exceptions), and at the foot **your open tasks in short form** —
  id, title, urgent, age, urgent first then the oldest. Task **bodies are not in
  here**: `tasks_get` carries those.
- Every rule line shows `reach` and the names it reaches, so you can tell why a
  rule is in your list — everybody, a group you belong to, or your own name.
- **`query`** filters by text over title and body. It narrows what you are
  shown; it does not narrow what binds you.
- **`pending=True`** shows the proposal queue instead: what has been filed and
  not yet decided, each with its reason. This is where a proposal of yours ends
  up, and the only place you learn it was denied.
- ⚠ **A `skill` consumer does not receive the project's brief and specs.**
  `profile` comes back as `{withheld: "skill", …}` with the reason spelled out.
  It is never dropped in silence, because a missing field reads like an empty
  project. If a skill needs something out of the project's profile, that thing
  belongs in the skill's own brief, or the job belongs to a chat.
- ⚠ **ON A `human` THIS CALL IS REFUSED, not answered empty** — and it is worth
  knowing before it happens, because this is the call a session opens with:

      alfredo is a human, and this call is refused rather than answered empty:
      no rule binds a person through this registry — one that does says so in
      its body — so an empty list here would read as a project with no rules
      in it.

  Their desk is real and `tasks_list` reads it. Nothing is broken: a person is
  bound by rules the way anybody is, and where a rule means to bind them it
  says so in its own body.
- **The list stops at 50 rules**, and says the real total when it cuts, with
  what to do about it — narrow with `query`, or read them by ID. `count` is
  always the true number reaching you, not the number of lines you got.
- A consumer name that is not in the registry is refused naming the live ones.

## rules_get(project, ids, consumer, history=False)

Rules in full, up to **10** at a time.

- What binds you is **the ID and the body, and nothing else**. The rest — title,
  domain, perimeter, state — is there so you can tell one rule from another and
  find your way back to the decision.
- The `reason` is the WHY, and **`reason` is immutable**: written when the rule
  is proposed, never rewritten, because a why that could be edited afterwards is
  a why that gets edited to fit. You read it where a person decides — the
  proposal queue and the approval page.
- **`history=True`** gives the rest of the story: dated gestures, the hand, and
  only the fields that changed — including the version number, which is what
  whoever administers the project will be asked for before they can move the
  rule's perimeter.
- **`consumer`** is who is asking, and it is a NAME CHECK and nothing more: a
  name the registry does not know is refused, a name it knows changes not one
  field of the answer. ⚠ It does **not** mark which rules reach you — that is
  `rules_list`, which is the call built to answer it. This one reads a rule
  whoever asks, on purpose: a rule you are not bound by is still one you may
  have to read.
- The short form resolves on a read — `VA-02` finds `VA-0002` — so an old text
  does not have to be rewritten to be followed.
- More than ten IDs is refused, not trimmed:

      11 IDs asked for and the ceiling is 10: this one is REFUSED and not
      trimmed, because a silent cut answers a question you did not ask.
      Split the batch.

  An EMPTY list is refused too: there is no reading nothing on purpose.
- **There is a second ceiling, in bytes — 60000 — and it behaves differently.**
  Where the ID ceiling refuses, this one TRUNCATES and says so. ⚠ And it drops
  **whole rules, never text**: the rule that would cross the line does not
  arrive, and neither does anything after it, so what comes back is a SHORT
  LIST of complete rules and never a body cut off in the middle. The first rule
  always arrives whole however big it is. Ask for the rest in a second call.
- An ID that was never defined comes back in **`not_found`**, not as an error:
  asking about a rule that turns out not to exist is a legitimate question. Both
  things can happen in one call — unknown IDs and a byte cut — and the `note`
  then carries both sentences, one after the other.

## rules_propose(project, domain, type, title, body, reason, reach, proposed_by, groups=[], exceptions=[], supersedes='', source='', consumer_key='')

File a rule. It is born `proposed` and binds nobody: a human approves it on a
web page no chat can reach. File it and forget it — the outcome is in
`rules_list(pending=True)`.

There is **no `id` parameter**. You give the `domain` — two uppercase letters
the project has declared — and the registry assigns the next number in it, four
digits, up to **9999** per domain. ⚠ **The prefixes that number the log are
RESERVED and a proposal into one is refused**: they are not in the legend, and
`project_info` does not show them, so this is a constraint you meet by walking
into it. A rule numbered like an entry of the log could not be told apart from
one. A number is not a choice, it is a position in
a sequence, and the one you were given comes back in the verdict. Numbers are
assigned in order of ARRIVAL, not of calling: five proposals in flight come back
numbered in any order, so a batch that has to read in order is filed one at a
time.

The arguments that do not explain themselves:

- **`type`** — `R` binding · `M` method · `F` technical fact. Retirement is a
  STATE, not a type.
- **`reach` with `groups` and `exceptions`** is the one triple nobody gets right
  first time. `reach='all'` means everyone, including whoever is created
  tomorrow, and takes NO group and no exception. `reach='targeted'` needs at
  least one of the two, and the audience is groups UNION exceptions: an
  exception stands NEXT TO the groups and can only ADD. An exception already
  covered by this rule's own groups is refused at write time; an overlap that
  forms later blocks nothing — the next write on the rule refuses it, and the
  status report flags it.
  ⚠ **A `human` cannot be named in `exceptions`**, nor carried in through a
  group: a person has no rules, `rules_list` on one is refused rather than
  answered empty, and a rule reaching them would be law nobody can read that
  counted as read all the same. Reach a person with `tasks_add`.
- **`reason`** is mandatory and immutable: without the why a rule cannot be
  defended, and at the first opportunity it gets reopened.
- **`proposed_by`** is a signature — your consumer name, or a person's. An
  unsigned proposal is refused.
- **`supersedes`** is how a decision CHANGES, and it is one gesture, not two:
  name the rule in force that this one replaces, and the approval retires it
  inside the same decision. Content changes are a supersede too. Two pending
  proposals cannot claim the same victim.
- **`source`** is free text: where the thing came from.

⚠ **Citations are checked in FOUR fields — `title`, `body`, `reason` and
`source`** — through one door, with no exceptions: a `reason` that could carry
what a `body` cannot would be the same hole under a different name. So a title
with a bare `VE-05` in it is refused exactly as a body would be, and that is
the one people do not see coming.

A rule cites a RULE, never a task. And
in the body of a rule, a citation points at something that can still be used —
that means one thing only: a rule **in force**. A proposal has no number worth citing
yet — file the cited rule, let it be approved, then file the one that cites it —
and one that was denied, retired or superseded is refused too, because a pointer
at it reads like law and is not. When the rule you cited has an heir, the
refusal **names the heir**, so there is one thing to do about it:

    citation in `body` towards a rule that is out of force: VA-0001 →
    superseded by VA-0007. Where an heir is named, cite that one; where none
    is, say it in words.

There is no fourth way out of force: a rule that has been approved **stays**
until somebody ends it. It does not lapse on a date, and nothing in this
registry takes a rule out of the lists while nobody is looking. And a citation
towards one that never existed at all:

    citation in `body` that does not resolve: PE-9999 was never defined in
    this project.

Nothing is spent by any of these: a refused proposal costs neither a number nor
a place in the queue.

**The queue has a ceiling**, set per project. `queue_cap` 0 means the queue is
closed and nothing is lost by waiting; a full queue is refused with the titles
of what is waiting, because the ceiling exists so that whoever approves reads
what they tick. The body of one rule stops at **64000** bytes — a rule that long
is two rules.

## tasks_add(project, consumer, title, body, created_by, urgent=False, idem_key='', consumer_key='', kind='')

Put work on a desk — yours or any LIVE consumer's. Opening one for another desk
is the point of the log, and it is free. ⚠ A RETIRED name is refused, not
queued: a desk that has ended is a desk nothing reaches, and a task filed there
would be work nobody is going to see.

**TASK OR MESSAGE? ASK WHO WILL CLOSE IT, AND WHY.** It is the one question
that tells them apart, and it is worth more than any list of properties:

| | a **task** | a **message** |
|---|---|---|
| what it is | work that must happen | a condition that can pass |
| who closes it | the desk that DID it | its desk **or the one who sent it** |
| what closing means | somebody acted, and the outcome says what came of it | the condition is gone — possibly with nothing done |
| when the reason is owed | dropping it | dropping it (⚠ closing needs no words: the engine writes `closed by <who> on <date>`) |
| if nobody ever looks | it stays on the desk, and comes back marked stale | it may be opened and closed unseen, and that is correct |

Work that must happen is a **task**, even when it reads like news. A condition
that can stop mattering on its own is a **message**, even when it asks for
attention. Both sit on the same desk and arrive in the same list; `kind` tells
them apart.

**A task is a channel with two readers**, the desk it sits on and the hand that
opened it, and that makes the log two things: a REQUEST from one chat to
another (*look at this proposal and tell me what you think* is a task — it is a
request and not a `kind='message'`, and what makes it a task is that somebody
has to answer it), and a way to find out whether another chat did the work —
read it back with `tasks_list(authored=True)`.

- **`consumer`** is the owner, the desk it lands on. **`created_by`** is the
  signature, and it is required: a task whose sender is unknown is a task the
  owner cannot answer.
- **`urgent`** belongs to whoever creates the task, for good. The owner cannot
  clear it.
- **`idem_key`** is how a retried call does not become twins: same key, same
  desk, and you get the existing entry back with `already_open: true` and its
  `kind`, instead of a second one. Use it whenever you might be re-running.
  ⚠ **The twin has to still be OPEN.** The key absorbs a repeat, it does not
  remember one: once the first entry is closed the same key opens a NEW one —
  which is what a recurring audit wants, because finding the same thing again
  after it was dealt with is a new finding.
- **`kind`** is what you open. Left out, you get a **task**, exactly as before.
  Pass `'message'` and you get one of those instead — `MS-0001`, its own
  numbering, and **the sender may close it**. A word that is not a kind is
  refused with the list of the ones that are.

**A MESSAGE is for a condition that DECAYS.** The reminder stops because the
thing happened, and whoever sent it is usually the one who finds out: the tax
monitor says a statement is missing, the statement arrives, and the same skill
closes its own message on the next round. Nobody has to open a web page for the
circle to close.

Two guards come with it, and both are about the CLOSING rather than the opening:

- the **sender** must be a live consumer of this project, spelled as the
  registry spells it — because a right to close cannot belong to a signature
  nobody can resolve;
- the **desk must be somebody else**. A message to yourself is a note to
  yourself, and a note on your own desk is a task, which already works.

⚠ **A message is NOT a push notification.** A chat sees its desk when it starts,
and between two starts a day can pass: a message opened and closed in between is
one nobody will ever see, and that is correct. **For something that has to be
read regardless, open a TASK** — it stays on the desk until whoever owns it
closes it.
- **Opening a task for a HUMAN emails them, if their row carries an address.**
  They call no tool, so the register is not where they would find it. Their
  address is not something you can set — people and their post are looked after
  on the administration page, by a person — so if the answer says there is none,
  that is a sentence for whoever holds that password, not a thing to fix from
  here. The answer says which of the two happened rather than leaving you to
  assume:

      alfredo is a human: they call no tool, and their post is read from the
      overview or from the web page. There is an address on this row, so an
      email goes out too — the `posted` field of this answer says whether it did.

  ⚠ **`posted` is the field to read, not the note.** The note says what is on
  the row; `posted` says whether the message left. It is `false` when the
  container has no mail configured at all, and that is a legitimate state and
  not a fault — an `idem_key` that absorbed a repeat also posts nothing,
  because nothing happened.

- Citations in `title` and `body`: the door is the one on `rules_propose`, with
  two differences, and both follow from a task being a message rather than law.
  A task may cite a **task**, and only a task ID that was never opened is
  refused. A **closed** task stays citable, because pointing back at work that
  is done is what a log is for, and reading tells you the state. And a task
  may cite an **open proposal**, because asking another desk what it thinks of a
  proposal is the job. `denied` and `retired` are refused in a task too: a
  message that hands its reader a rule taken out of force misinforms.
- The body stops at **64000** bytes, and a title alone is refused: it is a
  reminder to whoever wrote it, not work anybody else can pick up.

## tasks_list(project, consumer, query='', since='', until='', authored=False)

One desk, short form, ordered by the server: **urgent first, then the oldest**.
So when the cap cuts, it cuts the fresh work. Recently closed ones trail.

**Tasks and messages sit on the same desk and arrive in the same list**, and
every row carries its `kind`. This is where they are told apart — the ID says
it too, but the field is the one to branch on.

- **`authored=True`** turns the call around: the tasks YOU opened on other
  desks, with status and outcome. That is the channel's second reading — check
  it before re-sending a reminder, and use it to learn a task was closed without
  asking anybody.
- ⚠ **`since` / `until` DO NOT WIDEN THIS LIST — THEY REPLACE IT.** The window
  filters on the CLOSING date, and something still open has not got one: a call
  carrying either one comes back with the open list EMPTY and closed entries
  only. It is an archive query, not a reading of the desk. To see both, call
  twice. Days, not times.
- **`query`** filters by text.
- The list stops at **50** items — the open ones and the closed ones each — and
  says the real total when it cuts, so a truncated list never looks like a
  short one.
- **Closed entries trail the list for a RECENCY window** and then stop showing
  up. They are not gone: they are asked for by date, with `since`/`until`.
  ⚠ That window is a SECOND one, and it is not the staleness window below —
  two settings that agree today and are not promised to.
- Tasks **do not expire**. One pending for more than **30** days comes out
  MARKED, and that is all: a deadline nobody set is not a deadline, and a task
  that vanished on a timer is work nobody decided to drop. Since 5.0.0 rules
  answer the same way, for the same sentence.
- **A task opened for a `human` who carries an address is EMAILED to them**,
  since 5.0.0 — the ID and the title in the subject line, and in the message
  the project, `Sender:` with who opened it, **the task's own text** and a
  footnote saying where it is answered. ⚠ The text arrives VERBATIM: markdown
  is not rendered, so asterisks and hashes are read as characters. Over 4000
  characters it is cut at the end, visibly.
  ⚠ **And the message carries a BUTTON that closes the task**, when the
  container knows its own address: it opens one page, shows that entry, and
  takes the outcome or the reason — no password, because the ticket in the
  link is the credential and the page does not answer outside the tailnet. So
  a person may close a task without a chat and without the register: read
  `kind` and the outcome, and do not assume that a task you opened for a human
  is still open because nobody spoke to you. A
  human without an address is not written to, and neither is anybody if the
  container has no mail configured: there is no on/off switch, and both ways to
  be quiet are absences. The verdict carries `posted`, true or false, because a
  notification nobody can confirm is a notification nobody trusts. An `idem_key`
  that absorbs a repeat posts nothing: nothing happened.
- **A proposal entering the queue is emailed to the project's approver**, if
  there is one and if they carry an address, and it carries the **rule's text**:
  whoever has to approve a thing should be able to read it without opening a
  page first. `rules_propose` says `posted` the same way. Nothing else is ever sent, and nothing composes a digest: a roll-up
  has to know what is scheduled and when the night's runs are finished, and a
  container knows neither.
- Bodies are not here. `tasks_get` carries those.

## tasks_get(project, ids)

Entries in full — kind, title, body, owner, sender, urgency, state, and the
outcome or reason if it is closed. Tasks and messages both: read `kind` to tell
which one you are holding.

- Up to **10** IDs, refused and not trimmed past that. An EMPTY list is refused
  too, rather than answered with nothing.
- Up to **60000** bytes in one answer — and ⚠ **past that it drops WHOLE
  ENTRIES, not text.** The entry that would cross the line does not arrive, and
  neither does anything after it: what comes back is a SHORT LIST of complete
  entries, never a body cut off mid-sentence. The first entry always arrives
  whole however big it is, and the answer says how many of how many were read.
- An ID that names nothing comes back in **`not_found`**. An ID that names a
  RULE is not "not found": the answer says so and sends you to `rules_get`.
- Citations inside the text come back EXPANDED, with the current title and the
  current state:

      (TK-0001 — Re-read the tax register · completed)

  ⚠ That expansion is a READING aid and it is not always something you can paste
  back. A rule cited when it was in force and retired since reads
  `(VA-0001 — its title · retired)`, and a write that carries that citation is
  refused, correctly. Amend the words, not the pointer: rewrite the citation, or
  say the thing in words.
- No `consumer` argument: reads are project-wide.

## tasks_close(project, id, by, outcome='', reason='', consumer_key='', key='')

One gesture with two verdicts: **`outcome`** completes it, **`reason`** drops
it, **exactly one of the two**. On a TASK neither is optional — only the owner
knows how it went, and a closed task with nothing written is a task nobody can
learn from.

**On a MESSAGE the outcome may be left out, and the asymmetry is deliberate.**
Send nothing and it closes `completed`, with a line the engine writes itself:

    closed by FP-Update-Tax on YYYY-Mmm-DD

That sentence states the gesture this engine WITNESSED — who closed it and when
— and never why. It did not see a condition clear; it saw somebody press close.
**`reason` has no default and never will:** dropping something is a decision —
*I let this go knowing the condition did not clear* — and that is the one line
that explains a hole six months later. The ordinary ending is free; the
exceptional one is argued.

- **`by`** is the signature.
- **A message is closed by its desk OR by the one who sent it**, and that is the
  only power a message has that a task does not. Anybody else still needs the
  admin code:

      MS-0001 is between FP-Update-Tax and Proprietario: a message is closed
      by its desk or by the one who sent it, and anybody else takes the admin
      code in `key`.
- **Closed is closed** — no amend, no reopen. If the work came back, open a new
  task and cite this one.
- Closing somebody else's task is an administration gesture: the admin code goes
  in `key`, and there is no second factor for it, because a task closed wrong
  reopens as a new task. Without it:

      TK-0001 belongs to advisory: closing somebody else's task takes the
      admin code in `key`. Opening one for another desk is free; closing it is
      not, because only the owner knows how it went.

- ⚠ The citation door runs on the `outcome` or `reason` you give `tasks_close`
  as well, and that one matters more than the others: an outcome is written
  once, and closed is closed. Check the citation before you send it.

## tasks_amend(project, id, by, title='', body='', consumer='', consumer_key='', key='')

Fix or reassign an OPEN task. Anything left empty is left alone — but a call in
which nothing ends up different is REFUSED, not answered as a harmless no-op.

⚠ **Reassigning onto a person's desk EMAILS them**, and `posted` on the verdict
says whether it went. For a human that is the only channel there is: they call
no tool and do not go and look. Only a change of DESK posts — fixing a typo
wakes nobody — and the desk it leaves is not written to, because the register
never posts a subtraction.

- **`consumer`** moves it to another desk. **`title`** and **`body`** rewrite
  the text; there is no `urgent` here, and that is not an oversight — urgency
  belongs to whoever created the task.
- Closed is closed: a closed task is refused, not amended.
- **Two ends may amend it: the desk it sits on, and whoever SENT it.** An entry
  belongs to the one who wrote it as much as to the one who owes it, so fixing
  your own wrong words costs nothing. Anybody else takes the admin code in
  `key`, and the refusal names both ends.
- The citation door runs here too, on the new title and the new body.
