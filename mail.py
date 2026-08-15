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
from datetime import datetime, timezone
from email.message import EmailMessage

DAILY_CAP = 10

# The icon, EMBEDDED and not linked. A `<img src="https://…">` weighs nothing
# and is blocked by default in most clients — Apple Mail's Protect Mail
# Activity among them — so the ordinary case would be a broken placeholder,
# which is worse than no logo at all. Embedding costs about 24 KB a message,
# which against ten messages a day is nothing.
#
# ⚠ It must be in the image: it is in the Dockerfile's COPY line for this
# reason and no other — the MCP surface serves it from a raw GitHub URL and
# never needed the file itself.
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "codifier-icon.png")
ICON_CID = "codifier-icon"

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


class Mailer:
    """Configured once, from the environment, and handed the service's logger.

    It reaches for neither on its own: a module that read the environment
    itself would be a second place where the configuration is decided, and a
    second logger is how a warning stops appearing in the log everybody reads.
    """

    def __init__(self, *, host: str = "", port: int = 0, user: str = "",
                 password: str = "", sender: str = "", security: str = "",
                 log=None) -> None:
        self.host = (host or "").strip()
        self.port = int(port or 0)
        self.user = (user or "").strip()
        self.password = password or ""
        self.sender = (sender or "").strip() or self.user
        self.security = (security or "").strip().lower() or "starttls"
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
                f"as {self.sender} · at most {DAILY_CAP} a day per project")

    def _default_port(self) -> int:
        return {"ssl": 465, "none": 25}.get(self.security, 587)

    # ---------- the brake ----------

    def _allow(self, project: str) -> tuple[bool, bool]:
        """(may send, this is the last one). Under the lock, because the tools
        run in a thread pool and two threads reading the same count is exactly
        the shape a cap must not have."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with self._lock:
            day, sent = self._sent.get(project, [today, 0])
            if day != today:
                day, sent = today, 0
            if sent >= DAILY_CAP:
                self._sent[project] = [day, sent]
                return False, False
            sent += 1
            self._sent[project] = [day, sent]
            return True, sent == DAILY_CAP

    # ---------- the one way out ----------

    def _compose(self, project: str, subject: str, headline: str,
                 lines) -> EmailMessage:
        """ONE source for both halves. The plain text and the HTML are built
        from the SAME `lines`, so they cannot drift — which is the failure a
        multipart message invites: two versions of a sentence, and the one
        nobody reads goes stale first.

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
        msg.set_content(headline + "\n\n" + "\n\n".join(lines) + "\n",
                        cte="quoted-printable")

        paragraphs = "".join(
            f'<p style="margin:0 0 .85rem">{_html.escape(t)}</p>' for t in lines)
        body_html = (
            '<div style="font-family:-apple-system,BlinkMacSystemFont,'
            "'Segoe UI',Roboto,sans-serif;font-size:15px;line-height:1.55;"
            'color:#374151;max-width:33rem">'
            '<table role="presentation" cellpadding="0" cellspacing="0" '
            'border="0" style="margin:0 0 1.5rem"><tr>'
            f'<td style="padding-right:.75rem;vertical-align:middle">'
            f'<img src="cid:{ICON_CID}" width="42" height="42" alt="" '
            'style="display:block;border-radius:9px"></td>'
            '<td style="vertical-align:middle">'
            '<div style="font-size:.72rem;letter-spacing:.11em;'
            'text-transform:uppercase;color:#9ca3af">codifier</div>'
            f'<div style="font-size:1rem;color:#111827">'
            f'{_html.escape(project)}</div>'
            '</td></tr></table>'
            f'<p style="margin:0 0 1.1rem;font-size:1.18rem;font-weight:600;'
            f'color:#111827">{_html.escape(headline)}</p>'
            f'{paragraphs}</div>')
        msg.add_alternative(body_html, subtype="html", cte="quoted-printable")

        # THE ICON, and its absence is not a reason to lose a message. It is
        # attached to the HTML part, not to the message: `multipart/related`
        # is what tells a client that the image belongs to that markup rather
        # than being something to download.
        try:
            with open(ICON, "rb") as fh:
                msg.get_payload()[1].add_related(
                    fh.read(), maintype="image", subtype="png", cid=f"<{ICON_CID}>")
        except OSError as e:                                      # noqa: BLE001
            if self.log:
                self.log.warning("mail: no icon at %s (%s) — sent without it",
                                 ICON, e)
        return msg

    def send(self, project: str, to: str, subject: str, headline: str,
             lines) -> bool:
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
        allowed, last = self._allow(project)
        if not allowed:
            if self.log:
                self.log.warning(
                    "mail not sent for %s: %s already posted today, which is the cap. "
                    "Something is looping — the cap exists so a runaway cannot burn a "
                    "month's allowance in an afternoon", project, DAILY_CAP)
            return False
        lines = list(lines)
        if last:
            lines.append(
                f"This is the {DAILY_CAP}th message about {project} today, which is "
                f"the daily ceiling: nothing else will be posted about this project "
                f"until tomorrow. The work itself is unaffected — tasks are opened "
                f"and proposals are queued as usual — but you will not hear about "
                f"them by mail. If this arrived out of nowhere, something is looping.")
        msg = self._compose(project, subject, headline, lines)
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
                title: str, urgent: bool = False) -> bool:
    """A task landed on a human's desk. The body carries the ID, who sent it
    and where to read it — not the task's text: what is being sent is a
    knock on the door, and the register is where the work is read."""
    if not mailer.configured:
        return False
    row = prj.postbox(owner)
    if row is None:
        return False
    mark = "URGENT · " if urgent else ""
    # THE SUBJECT IS THE TASK ITSELF, short: it is what an inbox list shows, and
    # "TK-0001 is on your desk" spent that line telling the reader the one thing
    # they could work out from the sender. The ID and the project are in the
    # message, where there is room for them.
    #
    # NO DISCLAIMER either. It explained that the address on the row is why the
    # message arrived — true, and known to the one person who can change it,
    # since they typed it on the page themselves. A footer nobody needs is a
    # footer that teaches the eye to stop before the end.
    return mailer.send(
        prj.name, row["email"],
        f"{mark}Task: {_short(title)}",
        f"{tid} — {title}",
        [f"{sender} opened it for you in {prj.name}.",
         "Read it with tasks_get, or on the project's page."])


def proposal_queued(mailer: Mailer, prj, rid: str, title: str,
                    proposed_by: str) -> bool:
    """A rule is waiting for a person. Posted to the approver, if there is one
    and if they have an address — and silently not posted otherwise, which is
    the same shape as everything else here."""
    if not mailer.configured:
        return False
    who = prj.approver()
    if not who or not who["email"]:
        return False
    return mailer.send(
        prj.name, who["email"],
        f"Proposed Rule: {_short(title)}",
        f"{rid} — {title}",
        [f"Proposed by {proposed_by} in {prj.name}.",
         "It binds nobody until you approve it on the project's lot page, where "
         "the whole queue is decided in one turn against its digest: what is not "
         "ticked is denied, and a denial costs a sentence."])
