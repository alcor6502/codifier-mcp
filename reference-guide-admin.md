# Codifier MCP — administration

This is the administration HALF of the manual. The other half — the model,
session start, citing, the rules of the house — is what `reference_guide()`
serves bare, and you already have it: it is not repeated here.

The admin code travels in `key`, on **every** call: elevation is per call —
there is no session, no `su` that persists. The scale is FLAT: **creating takes
the admin code; MODIFYING anything that exists takes the admin code PLUS a
one-time `auth_code`** — perimeters, retirements, renames, briefs, group
membership, `queue_cap`. The code is minted on the web UI's maintenance page:
minutes to live, burned in the same transaction as the SUCCEEDED gesture — a
refusal rolls back and does not consume it. Spent or expired it is nothing;
alone it elevates nobody. Chained gestures mint one code per gesture, one at a
time. The one declared exception: closing or amending someone else's task takes
the admin code only — a task closed wrong reopens as a new task.

    project_amend(project, entity, name, action, fields={}, reason='',
                  auth_code='', key='')
    rules_amend(project, id, reach, groups, exceptions, expected_version,
                reason, auth_code, key)
    rules_retire(project, id, reason, auth_code, key)
    project_status(project, key)
    rules_export(project, key, consumer='', expand=False)
    tasks_overview(project, key)

- **`project_amend`** — the project itself and its structure. `entity`: project
  | domain | consumer | group; `action`: create | amend | retire | revive. The
  ladder is the FLAT one stated above, and it has no case list of its own:
  creating takes the admin code; every amend, retire and revive — briefs, names,
  group membership, `queue_cap` — takes the admin code AND a one-time
  `auth_code`. One exception downward: `project.specs` and `consumer.specs`
  ALONE pass on the reference code, because they are operational data and not
  identity; presented next to a field that needs a higher gate, the call is
  refused whole, naming the field that costs more — the part you are allowed is
  never written and the rest dropped. A domain's code is immutable; retiring a
  domain with active rules is refused naming them; retiring anything costs a
  reason; creating a group that mirrors a rule's exceptions is refused naming
  the rule — but ADDING a member to a group passes even when it covers a rule's
  exception: that overlap is repairable, it goes to the status report and is
  refused at the next write on that rule. A group edit or consumer retire that
  would leave a rule in force with ZERO effective consumers is refused naming
  the rules. A name of a consumer or a group is ONE WORD — letters, digits,
  `-` and `_`, no spaces, and the refusal says which mistake you made: those
  names are quoted exactly, in `groups`, in chat instructions, in scheduled
  prompts, and a space is the character nobody sees when it is wrong. A
  PROJECT name keeps its spaces: the folder is the name as spelled, the file
  is the slug derived from it. Names of consumers and groups are amendable
  (two factors, versioned) and the OLD NAME STOPS RESOLVING: the verdict lists
  what to update outside the registry — skill files, chat instructions,
  scheduled prompts.
  `queue_cap`: NULL = unlimited, 0 = queue closed, N = N — it governs both the
  proposal queue and the batch page's action.
- **`rules_amend`** — the perimeter of a rule in force, NARROWED only: the new
  effective consumer set must be contained in the old one, and never empty — a
  narrowing that leaves no consumer is a retirement in disguise and is refused:
  that gesture is `rules_retire`'s. Widening binds someone new — that is
  promulgation: propose a supersede with the wider audience. Content changes are
  a supersede too. `expected_version` is the version `rules_get(history=True)`
  last showed you.
- **`rules_retire`** — end a rule without an heir: two factors (admin code +
  `auth_code`), because the way back is a proposal and a human approval. With an
  heir, supersede.
- **`project_status`** — computed counts, expiring rules with their reasons, the
  pending queue, prose citations pointing at retired or missing rules, and the
  overlaps that formed after the fact. It reports; it does not correct. It is
  also the ONE place the RETIRED are readable — domains, consumers and groups
  under `retired`, with date and reason. `project_info` lists the live alone,
  and a retired name is still a name TAKEN: a `create` on it is refused, so
  the way past that refusal is `revive` — and you cannot revive what you
  cannot see.
- **`rules_export`** — the corpus in one call; mind your client's result cap:
  this is the tool that meets it first.
- **`tasks_overview`** — every desk at once, read-only, ceilings declared.
- **Someone else's task** — the same `tasks_close` / `tasks_amend` you already
  know, with the admin code in `key`; the refusal names owner and port.
- **The web UI** (chats cannot reach it): approve/deny the batch against the
  digest, renew and promote, mint one-time auth codes, per-project backup,
  consultation, log.
