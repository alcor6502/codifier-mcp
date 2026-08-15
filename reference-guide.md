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
reach. Tasks are messages with an obligation: opening one for someone else is
normal; closing one costs an outcome or a reason.

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

Rules are cited as `(VA-0002)`, tasks as `(TK-0012)` — ID in round brackets,
four digits, in every prose field. Reads forgive the short form (`VA-02`
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
they are readable only from the administration side.

**Do this first, with `rules_list`:** find your own consumer name in the list,
spelled exactly. A misspelt or retired role fails later in ways that look like
something else.

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
- **`consumer`** is who is asking: it is what lets the answer say whether the
  rule reaches you.
- The short form resolves on a read — `VA-02` finds `VA-0002` — so an old text
  does not have to be rewritten to be followed.
- More than ten IDs is refused, not trimmed:

      11 IDs asked for and the ceiling is 10: this one is REFUSED and not
      trimmed, because a silent cut answers a question you did not ask.
      Split the batch.

- An ID that was never defined comes back in `missing`, not as an error: asking
  about a rule that turns out not to exist is a legitimate question.

## rules_propose(project, domain, type, title, body, reason, reach, proposed_by, groups=[], exceptions=[], supersedes='', source='', consumer_key='')

File a rule. It is born `proposed` and binds nobody: a human approves it on a
web page no chat can reach. File it and forget it — the outcome is in
`rules_list(pending=True)`.

There is **no `id` parameter**. You give the `domain` — two uppercase letters
the project has declared — and the registry assigns the next number in it, four
digits, up to **9999** per domain. A number is not a choice, it is a position in
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
- **`reason`** is mandatory and immutable: without the why a rule cannot be
  defended, and at the first opportunity it gets reopened.
- **`proposed_by`** is a signature — your consumer name, or a person's. An
  unsigned proposal is refused.
- **`supersedes`** is how a decision CHANGES, and it is one gesture, not two:
  name the rule in force that this one replaces, and the approval retires it
  inside the same decision. Content changes are a supersede too. Two pending
  proposals cannot claim the same victim.
- **`source`** is free text: where the thing came from.

⚠ **Citations in `body` and `reason`.** A rule cites a RULE, never a task. And
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

## tasks_add(project, consumer, title, body, created_by, urgent=False, idem_key='', consumer_key='')

Put work on a desk — yours or anybody's. Opening one for another desk is the
point of the log, and it is free.

**A task is a channel with two readers**, the desk it sits on and the hand that
opened it, and that makes the log two things: a message between chats (*look at
this proposal and tell me what you think* is a task), and a way to find out
whether another chat did the work — read it back with `tasks_list(authored=True)`.

- **`consumer`** is the owner, the desk it lands on. **`created_by`** is the
  signature, and it is required: a task whose sender is unknown is a task the
  owner cannot answer.
- **`urgent`** belongs to whoever creates the task, for good. The owner cannot
  clear it.
- **`idem_key`** is how a retried call does not become twins: same key, same
  desk, and you get the existing task back with `already_open: true` instead of
  a second one. Use it whenever you might be re-running.
- ⚠ **Opening a task for a HUMAN does not notify them.** Humans call no tools.
  The answer says so rather than leaving you to assume:

      alfredo is a human: this does NOT notify them. Their post is read from
      the overview or from the web page.

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

- **`authored=True`** turns the call around: the tasks YOU opened on other
  desks, with status and outcome. That is the channel's second reading — check
  it before re-sending a reminder, and use it to learn a task was closed without
  asking anybody.
- **`since` / `until`** open the window on the older closed ones. Days, not
  times.
- **`query`** filters by text.
- The list stops at **50** items and says the real total when it cuts, so a
  truncated list never looks like a short one.
- Tasks **do not expire**. One pending for more than **30** days comes out
  MARKED, and that is all: a deadline nobody set is not a deadline, and a task
  that vanished on a timer is work nobody decided to drop. Since 5.0.0 rules
  answer the same way, for the same sentence.
- Bodies are not here. `tasks_get` carries those.

## tasks_get(project, ids)

Tasks in full — title, body, owner, sender, urgency, state, and the outcome or
reason if it is closed.

- Up to **10** IDs, refused and not trimmed past that.
- Up to **60000** bytes in one answer; past that the text truncates and the
  answer says so.
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
it, **exactly one of the two**. Neither is optional — only the owner knows how
it went, and a closed task with nothing written is a task nobody can learn from.

- **`by`** is the signature.
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

Fix or reassign an OPEN task. Anything left empty is left alone.

- **`consumer`** moves it to another desk. **`title`** and **`body`** rewrite
  the text; there is no `urgent` here, and that is not an oversight — urgency
  belongs to whoever created the task.
- Closed is closed: a closed task is refused, not amended.
- Amending somebody else's task takes the admin code in `key`, the same as
  closing one; the refusal names the owner.
- The citation door runs here too, on the new title and the new body.
