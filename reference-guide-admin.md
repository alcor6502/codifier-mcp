# Codifier MCP — administration

This is the administration HALF of the manual, and this page is its MODEL. The
other half — the model of the service, session start, citing, the rules of the
house — is what `reference_guide()` serves bare, and you already have it: it is
not repeated here.

> ⚠ **If anything about an administration command is unclear, ask for its CARD:**
> **`reference_guide("<name>", project, key)`.** The card explains that one
> command in full — every argument, the cases nobody guesses, and the refusals it
> can hand you. The names you may ask for come back with this page, in `cards`,
> and the CARDS come back only to a caller who brought the key: asked without
> it, this service refuses them exactly as it refuses a name that means nothing,
> and the refusal lists the working cards alone. The tool names themselves are
> not a secret — they are in the catalogue of any client connected here, and in
> the README — but what they do, what they cost and how they refuse is on this
> side of the gate.

## THE GATE

The admin code travels in `key`, on **every** call: elevation is per call —
there is no session, no `su` that persists. A wrong code does not demote you to
an ordinary caller: `admin` is set by the presence of a key, so a typo is
refused as administration, never silently downgraded.

The scale is FLAT: **creating takes the admin code; MODIFYING anything that
exists takes the admin code PLUS a one-time `auth_code`** — perimeters,
retirements, renames, briefs, group membership, `queue_cap`. The code is minted
on the web UI, on the PROJECT's own codes page: a code is a row in that
project's database, so it is good for that project and for no other. It is shown
once and copied by hand into the chat, lives minutes, and is burned in the same
transaction as the SUCCEEDED gesture — a refusal rolls back and does not consume
it. Spent or expired it is nothing; alone it elevates nobody. Chained gestures
mint one code per gesture, one at a time.

**One declared exception**, downward: closing or amending someone else's task
takes the admin code ALONE. A task closed wrong reopens as a new task; a rule
retired wrong loses its ID and its continuity. There is no separate tool for it
— it is the `tasks_close` and `tasks_amend` you already have, with the admin
code in `key`, and the refusal without it names the owner and the port.

**The credentials are checked FIRST, and a refusal on them tells you nothing
else.** If the `auth_code` is missing, invented, spent or expired, that is what
comes back — not whether the rule you named exists, not what state it is in. So
a refusal naming the `auth_code` is never evidence about the target: mint a live
one and ask again. The code is **verified** early and **spent** late, in the
transaction of the gesture that succeeded, which is why a call refused further
down for a typo does not cost you a trip back to the page.

## WHAT IS NOT HERE

**The web UI**, which no chat can reach: approve or deny the batch against the
digest, write the project's own profile, mark who its proposals are posted
to, mint one-time auth codes, per-project backup,
consultation, log. Approval is the act these tools deliberately do not have.

# COMMANDS

One card each. `reference_guide("<name>", project, key)` returns just one.

## project_amend(project, entity, name, action, by, fields={}, reason='', auth_code='', key='')

The project's STRUCTURE — the one door for all of it.

- **`entity`**: `domain` | `consumer` | `group`.
  **`action`**: `create` | `amend` | `retire` | `revive`.
  **`name`** identifies the thing; **`fields`** carries what changes.
- **`by`** is YOUR consumer name, spelled as `project_info` spells it, and it is
  REQUIRED. It is the hand the history records: a register that cannot say whose
  gesture a row was answers the wrong question six months later.
  ⚠ It is required **in the schema**, so a call that OMITS it does not reach
  this service at all — your client refuses it first, with a message of its own
  that names the missing argument and nothing else. A call that passes it EMPTY
  is schema-valid and gets here, and then the refusal is ours and says the
  sentence above. Two doors, two different mistakes.
- ⚠ **PEOPLE ARE NOT HERE AT ALL.** A consumer of kind `human` is created,
  renamed, retired and revived on the **administration page**, and so is their
  address and the mark that says whose desk hears about the proposals. Every
  one of those four actions is refused here, on the way in and on the way out,
  and the refusal says where they live.

  The reason is not security, it is what the two things ARE. A `chat` and a
  `skill` are machinery, and machinery is managed by machinery: this command
  wires the project up. A person is not a row a chat invents, renames or
  retires — they are looked after by a person, behind a password no tool
  carries. It is the same sentence as *what is fundative has no tool*, said
  about the anagrafica instead of about the profile.

  What you CAN do with a person from here is the thing that matters: put work
  on their desk, with `tasks_add`.
- **A person's row is a different shape, and the schema holds it, not the
  code.** Only a human may carry an `email`, and a human has **no `brief` and
  no `specs`**: they already know who they are and what they have to do, and
  two writable fields nobody ever reads are the same disease as an address on a
  chat. Both refusals come from the database as well as from the engine,
  because root opening the file with `sqlite3` is a documented road and a
  guarantee that lives only in Python is one that road walks past.
- **`rules_list` on a human is REFUSED**, not answered empty: no rule binds a
  person through this registry, and an empty list would read as a project with
  nothing in it. Their desk is real — `tasks_list` and `tasks_overview` answer
  for it, and the overview is where their address is read.
- **THE PROJECT ITSELF IS NOT HERE.** Its `brief`, its `specs` and its
  `queue_cap` are written by a person on the administration page, behind that
  page's password — not by this command and not by the admin code either. The
  brief is the project's identity and the specs are the facts every reading is
  done against: what is FUNDATIVE has no tool, the way what is catastrophic has
  none. Suggest the wording to whoever administers the project; the change is
  theirs. `entity='project'` is refused saying exactly this.
- **A CONSUMER'S `specs` ARE THAT CONSUMER'S.** `specs` in the call under a
  different name is refused, and the rule has NO exception — not with the admin
  code, not with a one-time code, whatever else rides along in `fields`. The
  refusal carries the road:

      the specs of advisory are changed by advisory. If you need them changed,
      open a task: tasks_add(project, consumer='advisory', title=…, body=…,
      created_by='tax'). When you see it closed you will know whether it was
      done, and with what reason if it was not.

  That is the point of answering with a task rather than with a permission: a
  permission denies and leaves nothing behind, a task leaves on the record who
  wanted what and how it ended.

  ⚠ **`by` is DECLARED, not proven.** It stops the mistake and the confusion,
  never the lie: a chat that writes somebody else's name goes through. That is
  accepted rather than overlooked — inside a login restricted to a single user
  there are no strangers in the project, and that is the real perimeter. Do not
  build anything on this that needs more than that.
- The ladder is the FLAT one, and this command has no case list of its own:
  `create` takes the admin code; every `amend`, `retire` and `revive` — briefs,
  names, group membership — takes the admin code AND a one-time `auth_code`.
- **One exception downward:** a consumer's `specs` ALONE pass on the reference
  code, because they are operational data and not identity. Presented next to a
  field that needs a higher gate, the call is refused WHOLE, naming the field
  that costs more:

      brief: this field is not operational data, and it does not travel on the
      reference code — specs would. The call is refused WHOLE: the part you
      are allowed is not written and the rest dropped, because a gesture that
      half happened is a gesture nobody can read six months later. Bring the
      admin code in `key`, and the one-time `auth_code` with it.

  Split the call.
- **`reason`** is required to retire anything.

The cases nobody guesses:

- **A domain's code is immutable.** Retiring a domain that still has active
  rules is refused, naming them.
- **A name of a consumer or a group is ONE WORD** — letters, digits, `-` and
  `_`, no spaces — and the refusal says which mistake you made. Those names are
  quoted exactly in `groups`, in chat instructions and in scheduled prompts, and
  a space is the character nobody sees when it is wrong. A PROJECT name keeps
  its spaces: the folder is the name as spelled, the file is the slug derived
  from it.
- **Names are amendable, and the OLD NAME STOPS RESOLVING.** Two factors,
  versioned, and the verdict lists what to update outside the registry — skill
  files, chat instructions, scheduled prompts. Nothing out there is updated for
  you.
- **A retired name is still a name TAKEN.** `create` on it is refused; the way
  past that refusal is `revive` — and you cannot revive what you cannot see, so
  the retired are readable from `project_status` and nowhere else.
- **Creating a group that mirrors a rule's exceptions is refused**, naming the
  rule. But ADDING a member to a group passes even when it covers a rule's
  exception: that overlap is repairable, it goes to the status report, and it is
  refused at the next write on that rule.
- **A group edit or a consumer retire that would leave a rule in force with ZERO
  effective consumers is refused**, naming the rules. A rule that binds nobody
  is a retirement in disguise.
- **`queue_cap`**: NULL = unlimited, 0 = queue closed, N = N. It governs both
  the proposal queue and the batch page's action. It exists so the queue is
  decided before it grows.

## rules_amend(project, id, reach, groups, exceptions, expected_version, reason, auth_code, key)

The perimeter of a rule in force — **NARROWED only**. Every argument is
required: there is no partial call here.

- The new effective consumer set must be CONTAINED in the old one, and never
  empty:

      this narrowing leaves NO consumer: that is a retirement in disguise, and
      the way out is rules_retire — which costs a reason and a one-time code,
      on purpose. A rule in force that binds nobody is a decision nobody took
      and nobody can find.

- **Widening binds someone new, and that is promulgation.** It is not an edit
  and it does not happen here:

      this is not a narrowing: it would newly bind tax, news. Widening is
      PROMULGATION — it puts an obligation on somebody who did not have it —
      and it goes through the page: propose a supersede carrying the wider
      audience, and let the approval retire this one in the same decision.

  Content changes are a supersede too. This command never touches title, body
  or reason.
- **`expected_version`** is the version `rules_get(history=True)` last showed
  you. If it moved under you the call is refused and nothing is written — read
  it again and decide with what you now know, rather than overwriting a decision
  you never saw.
- **`reason`** is what the history will carry. It is not the rule's `reason`,
  which is immutable: it is why the perimeter moved.

## rules_retire(project, id, reason, auth_code, key)

End a rule that has no heir. Two factors — admin code plus `auth_code` — because
the way back is a proposal and a human approval, and the ID never comes back.

- **With an heir, do not use this**: propose the replacement with `supersedes`,
  and the approval retires the old rule inside the same decision. One gesture,
  one decision, and the succession is recorded as a field of the row.
- **`reason`** is required.
- Retirement is a STATE, not a type: nothing is deleted, and the rule stays
  readable through its history.
- After it, citations pointing at it start being refused, and the ones already
  written in prose turn up in `project_status` under `dangling_citations`.

## project_status(project, key)

The report, and the only reading that sees what the working half cannot. It
reports; it does not correct. One factor.

- Computed counts and the pending queue. Nothing about a lifecycle, because
  there is not one to report: a rule in force stays in force until somebody
  ends it.
- **`dangling_citations`** — prose citations pointing at retired or missing
  rules. It covers the prose of every rule and the title and body of every OPEN
  task. The door refuses a pointer that is born broken; this catches the one
  that GOES broken when its target is later retired. Closed tasks are left out
  on purpose: `tasks_amend` answers `closed is closed`, so a finding there could
  never be cleared.
- **`consumers_no_rule_reaches`** leaves out the `human` ones, deliberately. A
  human is a destination and not a subject — they receive tasks, and no rule
  binds them through the registry — so listing them would mean listing every one
  of them for ever.
- **`stray_audience_rows`** — an audience row sitting next to a universal rule.
  It is inert, it is listed here, and the first write on that rule refuses it.
- ⚠ **It is the ONE place the RETIRED are readable** — domains, consumers and
  groups, with date and reason. `project_info` lists the live alone, and a
  retired name is still a name taken, so this is where you look before a
  `create` that is really a `revive`.

## rules_export(project, key, consumer='', expand=False)

The corpus in one call, for a migration or a review. One factor.

- **`consumer`** narrows it to what reaches one desk; **`expand=True`** renders
  the citations with their current titles and states instead of leaving the
  bare IDs.
- ⚠ **Mind your client's result cap, not this service's**: this is the tool that
  meets it first. Over that cap the result stops being data and becomes a file
  path — useful only if that file lands where your code runs. Narrow with
  `consumer`, or export from the web page.
- It carries the `reason` of every rule, which is what makes an export a
  document somebody can decide from.

## tasks_overview(project, key)

Every desk at once, read-only: who has what open, how old, and what is marked
stale. One factor.

- It is the answer to *is anything waiting on anybody* — the question no
  per-desk call can answer, because `tasks_list` is one desk at a time.
- Ceilings are declared in the answer, so a truncated overview never looks like
  a quiet project.
- Humans have desks here too, with their **address**: this is the one payload
  that carries it, because it is behind the admin code and `project_info`
  travels on the reference code. It is also what a digest would be composed
  from — this container composes none, and posts one task at a time as it is
  opened.
