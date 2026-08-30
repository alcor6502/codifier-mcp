"""
mail.py — the one place this service sends anything out.

WHY IT IS ITS OWN MODULE. The engine is the database, and a database that
opens a socket is a database whose transactions depend on somebody else's
network. Everything here happens AFTER the write has committed, and a failure
here is a line in the log — never a gesture that half happened.

WHAT SENDS A MAIL, and it is two things and no more:

  · a task opened for a HUMAN who has an address — to that address, at once;
  · a proposal entering the queue — to the project's `approver`, if there is
    one and if they have an address.

NO DIGEST, and that is a decision rather than something left for later. A
roll-up would have to know what is scheduled and when the night's runs are
finished, and a container knows neither. It will be a skill of Alfredo's, and
`tasks_overview` is already the payload it is composed from. So this file has
no scheduler, holds no queue and remembers nothing across a restart except the
counter below.

NO SWITCH. There is no "notifications on/off" knob anywhere, on purpose: two
ways to turn something off is one way too many, and the day the post stops
arriving somebody has to work out which of them did it. There are exactly two
ways, and both are ABSENCES — no SMTP host configured, so nothing is sent at
all; no address on a consumer, so that person is not written to.

THE BRAKE, and what it is actually for. The risk is not twenty mails in a day:
twenty mails are information, and somebody goes and looks at why. The risk is a
skill that loops on `tasks_add` and burns a month's allowance in half an hour,
leaving the real notifications undelivered for weeks. So: TEN PER DAY PER
PROJECT — per project and not per container, because a loop in one must not
silence another — and the TENTH is the one that says the sender is paused for
the day. In memory, so a restart forgets it, which is right: the runaway lives
inside a session.

⚠ THE ARITHMETIC IS DECLARED SO IT CAN BE REDONE. With two projects the worst
case is about 600 mails a month, comfortably inside the free thousand of the
account this uses. With four it does not fit. Whoever adds the third project
reads this line.

Nothing here is a dependency: `smtplib` and `email.message` are the standard
library, and they do STARTTLS, SMTPS and authentication.
"""
from __future__ import annotations

import html as _html
import os
import smtplib
import ssl
import threading
from urllib.parse import quote as _quote
from datetime import datetime, timezone
from email.message import EmailMessage

DAILY_CAP = 10

# HOW LONG A CLOSING LINK LIVES, in days, when the container does not say.
# Days and not minutes: a one-time code is minted for a gesture about to
# happen, while an entry on a person's desk waits — five minutes would be a
# button that is dead by the time it is read on the sofa. Two weeks is a
# fortnight's inbox, after which the link says so and the entry is untouched.
LINK_DAYS = 14

# NO ICON, and it is not an omission. The message carried one embedded, and two
# rounds of shrinking it taught the same thing twice: Apple Mail was not obeying
# the `width` attribute, so the number in the code and the number on the screen
# were never the same number. Then Alfredo made a contact card for the sender,
# and the client started drawing the picture itself — in the message list too,
# where nothing here could ever have put it.
#
# So the right size for a logo we cannot control turned out to be none: the
# address book wins, every message is 2 KB lighter, and the file left the image.
# `starttls` is the default because it is what port 587 speaks, and 587 is what
# a submission service hands you. The other two are named rather than deduced
# from the port: deducing it would be a rule with a case list, and the day
# somebody runs SMTPS on 587 the deduction is wrong in silence.
SECURITY = ("starttls", "ssl", "none")

# How long a title may be in a SUBJECT. Not a limit on the title — the register
# has its own, and this is only the line an inbox shows in a list. Cut with a
# real ellipsis so a cut is visibly a cut and not a title that happens to end
# oddly.
SUBJECT_TITLE = 70


def _short(text: str) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= SUBJECT_TITLE else t[:SUBJECT_TITLE - 1].rstrip() + "…"


# How much of a task's or a proposal's text travels. A ceiling and not a
# summary: what is cut is cut at the end, visibly, and the register has the
# whole of it. It exists because a body is written by a chat, and a chat can
# write four thousand words as easily as forty — a mailbox is not the place to
# discover that.
BODY_CAP = 4000


def _paragraphs(text: str) -> list:
    """A block of prose into paragraphs, blank line by blank line, capped.

    ⚠ THE TEXT TRAVELS VERBATIM. Markdown written in a task body arrives as the
    characters that were typed — asterisks, hashes and backticks included —
    because the register stores prose and not markup, and a mail that
    reformatted it would be showing something nobody wrote. It is said in the
    manual so it is a known cost and not a surprise."""
    t = (text or "").strip()
    if not t:
        return []
    if len(t) > BODY_CAP:
        t = t[:BODY_CAP].rstrip() + "…"
    return [p.strip() for p in t.split("\n\n") if p.strip()]


class Mailer:
    """Configured once, from the environment, and handed the service's logger.

    It reaches for neither on its own: a module that read the environment
    itself would be a second place where the configuration is decided, and a
    second logger is how a warning stops appearing in the log everybody reads.
    """

    def __init__(self, *, host: str = "", port: int = 0, user: str = "",
                 password: str = "", sender: str = "", security: str = "",
                 base_url: str = "", link_days: int = LINK_DAYS,
                 log=None) -> None:
        self.host = (host or "").strip()
        self.port = int(port or 0)
        self.user = (user or "").strip()
        self.password = password or ""
        self.sender = (sender or "").strip() or self.user
        self.security = (security or "").strip().lower() or "starttls"
        # WITHOUT A BASE URL THERE IS NO LINK, and the message is the one this
        # service sent yesterday. That is the default on purpose: a container
        # updated without the new variable must not start printing links to a
        # host it guessed. Unraid does not propagate new variables to
        # containers already installed, and a guessed address in an email is a
        # link that goes somewhere real and wrong.
        self.base_url = (base_url or "").strip().rstrip("/")
        self.link_days = max(1, int(link_days or LINK_DAYS))
        self.log = log
        self._lock = threading.Lock()
        # project -> [day, sent]. The day is a UTC date string: a counter that
        # rolled over on a local midnight would reset at a different moment
        # than the one the log timestamps say.
        self._sent: dict[str, list] = {}

    # ---------- is it even on ----------

    @property
    def configured(self) -> bool:
        """No host, no post — and no complaint either. Every switch in this
        service is an absence, and this is the outermost one."""
        return bool(self.host and self.sender)

    def describe(self) -> str:
        if not self.configured:
            return ("off — no SMTP host or sender, so nothing is posted and nothing "
                    "complains")
        return (f"{self.host}:{self.port or self._default_port()} {self.security} "
                f"as {self.sender} · at most {DAILY_CAP} a day per project and kind")

    def _default_port(self) -> int:
        return {"ssl": 465, "none": 25}.get(self.security, 587)

    # ---------- the brake ----------

    def _allow(self, project: str, kind: str = "task",
               cap: int = DAILY_CAP) -> tuple[bool, bool]:
        """(may send, this is the last one). Under the lock, because the tools
        run in a thread pool and two threads reading the same count is exactly
        the shape a cap must not have."""
        # The counter is keyed by PROJECT AND KIND: messages must not eat the
        # tasks' allowance, because the day eleven skills all go quiet is the
        # day the alarms matter most — and `idem_key` collapses a repeat of the
        # SAME fault, never different faults at the same moment.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        slot = f"{project}/{kind}"
        with self._lock:
            day, sent = self._sent.get(slot, [today, 0])
            if day != today:
                day, sent = today, 0
            if sent >= cap:
                self._sent[slot] = [day, sent]
                return False, False
            sent += 1
            self._sent[slot] = [day, sent]
            return True, sent == cap

    # ---------- the one way out ----------

    def _compose(self, project: str, subject: str, sender: str, lines,
                 note: str = "", link: str = "", link_label: str = "") -> EmailMessage:
        """ONE source for both halves. The plain text and the HTML are built
        from the SAME arguments, so they cannot drift — which is the failure a
        multipart message invites: two versions of a sentence, and the one
        nobody reads goes stale first.

        THE SUBJECT IS THE TITLE, and the title is not printed a second time
        inside. `TK-0003 — Aprimi su iPad` used to be the line every inbox
        shows AND the first line of the message, and the second one bought
        nothing: nobody opens a message without having just read its subject.
        Which is why there is no `headline` parameter any more — one title, in
        one place, and a title that cannot disagree with itself is the strongest
        form of the promise above.

        Layout deliberately sober, and with no background colour: Apple Mail in
        dark mode inverts a message that does not set one, and a card painted
        white stays a white card with unreadable text on it."""
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["Subject"] = subject
        # `To` is NOT set here: the recipient belongs to the send, not to the
        # shape of the message, and this method is about the shape.
        # QUOTED-PRINTABLE, and not the 8bit `set_content` picks by itself.
        # Found by a probe against a server that does not advertise 8BITMIME:
        # smtplib downgrades, and an em dash comes out the other end as the six
        # literal characters `\u2014`. SMTP2GO does advertise it, so the message
        # that arrives today is right — which is exactly why this would have
        # waited for the one relay that does not, on a Sunday.
        #
        # 7-bit clean means the message no longer depends on what the server
        # says it can take.
        msg.set_content(
            "\n".join([project, f"Sender: {sender}"]) + "\n\n"
            + "\n\n".join(list(lines)
                          + ([f"{link_label}: {link}"] if link else [])
                          + ([note] if note else [])) + "\n",
            cte="quoted-printable")

        # THE PROSE IS PROSE, at the size prose is read at. It carries the
        # task's own text now, so making it small and slanted was making the
        # only part worth reading the hardest part to read.
        #
        # A single newline inside a paragraph is kept as a break: a chat writes
        # lists that way, and collapsing them would be rewriting what it said.
        paragraphs = "".join(
            f'<p style="margin:0 0 .9rem">'
            f'{_html.escape(t).replace(chr(10), "<br>")}</p>' for t in lines)
        # AND THE POINTER IS THE ONLY SMALL THING. It is the one line that is
        # identical in every message ever sent, which is exactly what makes it
        # a footnote: 9px, because at the body's size it competed with the body.
        # THE BUTTON, and it is a LINK dressed as one: a table-cell with a
        # background, which is the shape every mail client has agreed on for
        # twenty years. No image, no `<button>` — one is blocked by default and
        # the other does nothing outside a form.
        #
        # ⚠ It sits AFTER the text and BEFORE the footnote, and that order is
        # the argument: a person reads what happened and then acts. A button at
        # the top is a button pressed before the paragraph under it was read,
        # and this one closes something that cannot be reopened.
        if link:
            paragraphs += (
                f'<table cellpadding="0" cellspacing="0" border="0" '
                f'style="margin:1.4rem 0 .2rem"><tr><td '
                f'style="background:#2d5c86;border-radius:6px">'
                f'<a href="{_html.escape(link, quote=True)}" '
                f'style="display:inline-block;padding:.7rem 1.15rem;color:#ffffff;'
                f'font-size:15px;font-weight:600;text-decoration:none">'
                f'{_html.escape(link_label)}</a></td></tr></table>')
        if note:
            paragraphs += (
                f'<p style="margin:1.3rem 0 0;font-size:9px;font-style:italic;'
                f'color:#6b7280">{_html.escape(note)}</p>')
        body_html = (
            '<div style="font-family:-apple-system,BlinkMacSystemFont,'
            "'Segoe UI',Roboto,sans-serif;font-size:15px;line-height:1.55;"
            'color:#374151;max-width:33rem">'
            # THE HEADER IS THE PROJECT AND WHO SPOKE, in that order and in two
            # sizes. The project is the one word that changes between messages
            # and tells you which register just spoke; the sender is the one
            # thing the subject cannot carry, so it goes directly under the
            # name, at a size between that name and the prose — a label, not a
            # sentence, which is why it is `Sender:` in bold and then a name.
            f'<div style="font-size:1.18rem;font-weight:600;color:#111827;'
            f'margin:0 0 .2rem">{_html.escape(project)}</div>'
            f'<div style="font-size:17px;color:#374151;margin:0 0 1.15rem">'
            f'<span style="font-weight:700">Sender:</span> '
            f'{_html.escape(sender)}</div>'
            f'{paragraphs}</div>')
        msg.add_alternative(body_html, subtype="html", cte="quoted-printable")
        return msg

    def send(self, project: str, to: str, subject: str, sender: str, lines,
             note: str = "", kind: str = "task", cap: int = DAILY_CAP,
             link: str = "", link_label: str = "") -> bool:
        """True if it went. NEVER raises: a notification that can make a write
        fail is worse than no notification, and every caller of this is a
        caller whose transaction has already committed.

        `lines` is a LIST of paragraphs rather than one blob, because the plain
        text and the HTML are made from it and a blob would have to be split by
        guessing where the paragraphs were.

        The brake is counted BEFORE the socket is opened, and the tenth mail
        of a project's day carries the notice that the sender is paused — so
        the pause is visible in the post and not only in the log."""
        if not self.configured or not (to or "").strip():
            return False
        allowed, last = self._allow(project, kind, cap)
        if not allowed:
            if self.log:
                self.log.warning(
                    "mail not sent for %s: %s %s already posted today, which is the "
                    "cap for that kind. Something is looping — the cap exists so a "
                    "runaway cannot burn a month's allowance in an afternoon",
                    project, cap, kind)
            return False
        lines = list(lines)
        if last:
            lines.append(
                f"This is the {cap}th {kind} notice about {project} today, which is "
                f"the daily ceiling: nothing else will be posted about this project "
                f"until tomorrow. The work itself is unaffected — tasks are opened "
                f"and proposals are queued as usual — but you will not hear about "
                f"them by mail. If this arrived out of nowhere, something is looping.")
        msg = self._compose(project, subject, sender, lines, note,
                            link=link, link_label=link_label)
        msg["To"] = to
        try:
            self._deliver(msg)
        except Exception as e:                                    # noqa: BLE001
            # BROAD ON PURPOSE, and it is the whole contract of this file:
            # smtplib raises half a dozen classes, the socket layer raises its
            # own, and a name resolution failure raises something else again.
            # Any of them reaching a caller would turn a notification into a
            # failed gesture.
            if self.log:
                self.log.warning("mail not sent for %s to %s: %s", project, to, e)
            return False
        if self.log:
            # The ADDRESS and not the body: what a log answers here is "was
            # this person told", and the body is in their inbox.
            self.log.info("mail sent for %s to %s: %s", project, to, subject)
        return True

    def _deliver(self, msg) -> None:
        port = self.port or self._default_port()
        if self.security == "ssl":
            server = smtplib.SMTP_SSL(self.host, port, timeout=10,
                                      context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(self.host, port, timeout=10)
        try:
            if self.security == "starttls":
                server.starttls(context=ssl.create_default_context())
            if self.user:
                server.login(self.user, self.password)
            server.send_message(msg)
        finally:
            server.quit()


def from_env(log=None) -> Mailer:
    """The reader, in one place. Every variable is optional with a working
    default — Unraid does not propagate new variables to containers already
    installed, so a required one would be a container that stops booting the
    day it is updated."""
    return Mailer(host=os.environ.get("SMTP_HOST", ""),
                  port=int((os.environ.get("SMTP_PORT") or "0").strip() or 0),
                  user=os.environ.get("SMTP_USER", ""),
                  password=os.environ.get("SMTP_PASSWORD", ""),
                  sender=os.environ.get("SMTP_FROM", ""),
                  security=os.environ.get("SMTP_SECURITY", ""),
                  base_url=os.environ.get("WEB_BASE_URL", ""),
                  link_days=int((os.environ.get("TASK_LINK_DAYS") or "0").strip()
                                or LINK_DAYS),
                  log=log)


# =====================================================================
# The two things that are posted. They take the ENGINE and ask it, rather than
# being handed an address by a tool's verdict: an address that travelled
# through a tool's answer would be an address in a chat's context.
#
# ⚠ AND THEY ASK IT THROUGH ITS METHODS — `postbox`, `approver` — never through
# `prj.cx`. The first version of this file read the connection directly, which
# was wrong for a reason no test would have shown: `Project` is wrapped by
# `_serialised`, and the whole safety of `check_same_thread=False` is that
# every public method takes the lock first. A raw read from here is a read on a
# shared connection while another thread may be half way through a
# multi-statement transaction.
# =====================================================================

def task_opened(mailer: Mailer, prj, tid: str, owner: str, sender: str,
                title: str, body: str = "", urgent: bool = False,
                kind: str = "task") -> bool:
    """A task landed on a human's desk, WITH ITS TEXT.

    ⚠ It used to be a knock on the door and nothing more — the ID, who sent it,
    where to read it — on the argument that the register is where the work is
    read. That argument was written by somebody who was not reading these on a
    tablet: a person who has to open a register to find out whether a thing
    matters will open it late, and the notification will have cost them a
    gesture to learn nothing. A task's text is a paragraph, not a document.

    The register is still the truth, and the mail is still not a place one
    answers from: the footnote says so, and it never changes."""
    if not mailer.configured:
        return False
    row = prj.postbox(owner)
    if row is None:
        return False
    # THE CLOSING LINK, and it is asked of the ENGINE by method — like the cap
    # and the postbox, and for the same reason: the lock that makes one
    # connection safe lives inside those methods.
    #
    # ⚠ A failure to sign one must not stop the message. The notification is
    # what this function owes; the button is a convenience on top of it, and a
    # project without an admin code in the registry — or any other refusal
    # here — would otherwise turn "your task did not get posted" into the
    # consequence of a missing knob.
    link, label = "", ""
    if mailer.base_url:
        try:
            tick = prj.task_link(tid, mailer.link_days)
            link = (f"{mailer.base_url}/p/{_quote(prj.name)}/t/"
                    f"{_quote(tick['id'])}?k={_quote(tick['token'])}")
            label = "Close it"
        except Exception as exc:                       # noqa: BLE001
            link, label = "", ""
            if mailer.log:
                mailer.log.warning("no closing link on %s: %s", tid, exc)
    mark = "URGENT · " if urgent else ""
    # THE SUBJECT IS THE WHOLE HEADLINE: the ID and the task, in the line an
    # inbox list shows. It said `TK-0001 is on your desk` once, which spent that
    # line telling the reader the one thing they could work out from the sender;
    # then it said `Task: <title>` and printed `TK-0001 — <title>` again at the
    # top of the message. This is both of those, once.
    #
    # `TK-` is what says "task", and it says it in every list, every reply and
    # every search — which is why the word itself is not there.
    #
    # ⚠ The title is CUT here and nowhere else carries it, since the body no
    # longer repeats it. That is on purpose: this message is a knock on the
    # door, and 70 characters of a title is a knock. The text is in the
    # register, which is where the task is read.
    #
    # NO DISCLAIMER either. It explained that the address on the row is why the
    # message arrived — true, and known to the one person who can change it,
    # since they typed it on the page themselves. A footer nobody needs is a
    # footer that teaches the eye to stop before the end.
    # The cap is asked of the ENGINE, by method: mail.py never reaches into
    # `prj.cx`, because the lock that makes one connection safe lives inside
    # those methods, and a shape check keeps it that way.
    return mailer.send(
        prj.name, row["email"],
        f"{mark}{tid} — {_short(title)}",
        sender, _paragraphs(body),
        "Read it with tasks_get, or on the project's page."
        + (" The button closes it from inside the tailnet and asks for no "
           "password; it stops working once the entry is closed, because "
           "closed is closed." if link else ""),
        kind=kind, cap=prj.mail_cap(kind), link=link, link_label=label)


def proposal_queued(mailer: Mailer, prj, rid: str, title: str,
                    proposed_by: str, body: str = "") -> bool:
    """A rule is waiting for a person. Posted to the approver, if there is one
    and if they have an address — and silently not posted otherwise, which is
    the same shape as everything else here."""
    if not mailer.configured:
        return False
    who = prj.approver()
    if not who or not who["email"]:
        return False
    # THE WORDS STAY HERE, and they are the difference from a task: `TK-` is a
    # prefix that says what it is, `VA-` is not — an approved rule and a
    # proposed one wear the same one. So the kind is spelled, and the headline
    # is the whole of it.
    return mailer.send(
        prj.name, who["email"],
        f"Proposed Rule {rid} — {_short(title)}",
        proposed_by, _paragraphs(body),
        "It binds nobody until you approve it on the project's lot page, where "
        "the whole queue is decided in one turn against its digest: what is not "
        "ticked is denied, and a denial costs a sentence.")
