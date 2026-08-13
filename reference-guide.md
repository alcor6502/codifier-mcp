# Codifier MCP — manual

## THE MODEL

A project is a registry of **rules that bind** and **tasks that wait**, plus the
people of the project: **consumers** (chats, skills, humans), their **groups**,
and the project's own **profile** — its brief and its specs.

Rules are law: a chat proposes, a human approves on a web page no chat can
reach. Tasks are messages with an obligation: opening one for someone else is
normal; closing one costs an outcome or a reason.

Every call names the project by its **reference code**, never by name. You are a
**consumer**: your name is in your instructions, and it is what you pass in
`consumer`. If you do not know it, stop and ask.

There is an administration half to this service, with a manual of its own. If
you were given an admin code you already know; if you were not, you do not need
it, and nothing below depends on it.

## SESSION START

One call:

    rules_list(project, consumer)

It returns, in order: the PROJECT's brief and specs — identity first, then the
living facts — then your **brief** and your specs, then the
legend of the domains present, your rules in force (universal, then groups from the widest,
then exceptions), and at the foot **your open tasks in short form** — id, title,
urgent, age, urgent first then the oldest. Every rule line shows `reach` and the
names it reaches. The task **bodies are not in here**: `tasks_get` carries those.

The other half of a session start is:

    project_info(project)

the TECHNICAL half — domains, consumers, groups — and everything in it is
**alive**. ⚠ **Find your own consumer name in that list, spelled exactly,
before you go any further.** If it is not there your role is retired or
misspelt, and nothing further you call will say so this plainly.

Empty set or silent registry: stop and say so.

## READING A RULE

What binds you is **the ID and the body, and nothing else**. The rest is there
so you can tell one from another and find your way back to the decision: the
title, the domain, the perimeter, the permanence and the expiry.

The `reason` is the WHY of the rule, and `reason` is immutable: it is written
when the rule is proposed and it is never rewritten, because a why that could be
edited afterwards is a why that gets edited to fit. You read it where a person
decides — in the proposal queue, in the expiring list, on the approval page.
`rules_get(history=True)` gives you the rest of the story: dated gestures, the
hand, and only the fields that changed.

## CITING

Rules are cited as `(VA-0002)`, tasks as `(TK-0012)` — ID in parentheses, four
digits, in every prose field. Reads forgive the short form (`VA-02` resolves);
writes refuse anything else, naming field and token, spending neither a number
nor a queue slot.

A citation may only point at a rule that is **already approved**. A proposal has
no number worth citing yet: file the cited rule, let it be approved, and only
then file the one that cites it.

## EVERY CALL, IN FULL

    reference_guide(project='', key='')
    project_info(project)
    rules_list(project, consumer, query='', pending=False)
    rules_get(project, ids, consumer, history=False)
    rules_propose(project, domain, type, title, body, reason, reach,
                  proposed_by, groups=[], exceptions=[], supersedes='',
                  source='', consumer_key='')
    tasks_add(project, consumer, title, body, created_by, urgent=False,
              idem_key='', consumer_key='')
    tasks_list(project, consumer, query='', since='', until='',
               authored=False)
    tasks_get(project, ids)
    tasks_close(project, id, by, outcome='', reason='', consumer_key='',
                key='')
    tasks_amend(project, id, by, title='', body='', consumer='',
                consumer_key='', key='')

`reference_guide` is the one call that takes nothing: bare, it serves this page.
Its two optional arguments belong to the other half of the manual.

There is **no `id` parameter** on `rules_propose`. You give the `domain` — two
uppercase letters the project has declared — and the registry assigns the next
number in it, four digits. A number is not a choice, it is a position in a
sequence, and the one you are given comes back in the verdict.

## THE RULES OF THE HOUSE

1. **You RECEIVE rules: you do not write them, rewrite them, or quote them from
   memory.** Found something that deserves one? `rules_propose`, and forget it —
   the outcome is in `rules_list(pending=True)`.
2. **A proposal is born `proposed`**, in a queue with a project-level cap;
   approval happens on the web page, never here. `type` is one of R | M | F;
   `proposed_by` is required — an unsigned proposal is refused. Changing a
   decision is ONE gesture: propose with `supersedes`, and the approval retires
   the named rule inside the same decision.
3. **`reach` is declared, never deduced**: 'all' (no audience) or 'targeted',
   where the audience is groups UNION exceptions. Groups are the normal case;
   exceptions are single consumers standing NEXT TO the groups — they only ever
   ADD. An exception already covered by THIS rule's groups is refused at write
   time; an overlap that forms later blocks nothing — the next write on the rule
   refuses it, and the status report flags it.
4. **Tasks: opening for others is the point of the log.** `urgent` belongs to
   the creator. Closed is closed — no amend, no reopen. Closing or amending what
   you do not own is an administration gesture.
   ⚠ **Opening a task for a human does NOT notify them**: humans call no tools —
   their mail is seen by whoever reads the overview or the UI. Tasks are not a
   notification channel to the owner.
5. **Reads are project-wide.** The reference code opens EVERY read of the
   project — rules, tasks, registry: within a project there are no secrets
   between consumers; isolation is BETWEEN projects. Treat the reference code as
   a secret: it lives in your instructions, never in a vault file.
6. **`authored=True` shows the tasks you opened on other desks**, status and
   outcome included: check before re-sending a reminder.
7. **If your consumer has a secret**, every gesture in your name takes
   `consumer_key`. No secret configured: the name suffices.
8. **Caps are declared.** Ten ids per `_get`, refused not trimmed; past the byte
   ceiling the text truncates and says so; list cuts hit the fresh work and state
   the real total.
9. **Refusals are `refused` lines that name the port.** A traceback is a bug,
   not an answer.

## TASKS

A task waits on one desk. It carries a title, a body, an owner, whoever created
it, and an urgency that belongs to the creator for good. `tasks_list` gives you
your desk in short form — urgent first, then oldest, so when the cap cuts it
cuts the fresh work — with the recently closed ones trailing; `since` and
`until` open the window on the older closed ones.

Tasks **do not expire**. One pending for more than 30 days comes out MARKED, and
that is all: a deadline nobody set is not a deadline, and a task that vanished
on a timer is work nobody decided to drop.

Closing is one gesture with two verdicts: `outcome` completes it, `reason` drops
it, exactly one of the two.

## THE CEILINGS

| what | ceiling |
|---|---|
| IDs per `rules_get` | 10 |
| body of one rule | 64000 bytes |
| numbers in one domain | 9999 |
| items in a task list | 50 |
| codes per `tasks_get` | 10 |
| bytes per `tasks_get` | 60000 |
| body of one task | 64000 |

Every one of them is declared in the answer when it bites, so a truncated list
never looks like a short one.

## WHICH TOOL

| You want to | Use |
|---|---|
| start a session, get project + brief + rules + your open tasks | `rules_list` |
| find a rule by text | `rules_list(query=...)` |
| see the proposal queue | `rules_list(pending=True)` |
| read rules in full, with history as dated gestures | `rules_get(history=True)` |
| propose a rule, or replace one | `rules_propose` (+ `supersedes`) |
| see who exists — domains, consumers, groups, LIVE only | `project_info` |
| put work on a desk (yours or anyone's) | `tasks_add` |
| see your desk / what you opened for others | `tasks_list` (+ `authored=True`) |
| read tasks in full | `tasks_get` |
| finish a task (outcome) or drop it (reason) | `tasks_close` |
| fix or reassign an open task of yours | `tasks_amend` |
| this page | `reference_guide()` |
