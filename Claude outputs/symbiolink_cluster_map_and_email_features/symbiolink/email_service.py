"""
Real email notifications via SMTP.

Follows the exact same "real if configured, simulated console-log otherwise"
pattern already used by whatsapp_service.py (Twilio) and payment_service.py
(Razorpay) -- so this works out of the box in a demo with zero setup, and
becomes fully real the moment SMTP_* env vars are set (see config.py), with
no code changes anywhere else.

Every public method here is deliberately non-raising (catches its own
exceptions and returns a bool) -- a notification email failing to send must
never break the request that triggered it, same philosophy as
notifications.py's create_notification() and app.py's
_log_order_history/_log_trust_rating helpers.

Two things this module is careful about, both of which only start to matter
once a *broadcast* (one listing -> every registered user) exists:

  1. One SMTP connection per batch, not per recipient. Gmail's handshake is
     TLS + AUTH before it will take a single message; paying that 25 times
     for a 25-recipient broadcast turns a sub-second send into most of a
     minute. _send_many() logs in once and reuses the session.
  2. Sending happens off the request thread. See send_async() -- recipient
     *lookup* stays on the request thread (it needs the DB session and is a
     cheap indexed query), and only the slow SMTP part is handed to the
     worker, so the background thread never touches Flask state.
"""

import smtplib
import ssl
import threading
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

import email_templates
from config import Config


class EmailService:
    def __init__(self):
        self.host = getattr(Config, "SMTP_HOST", None)
        self.port = getattr(Config, "SMTP_PORT", None) or 587
        self.username = getattr(Config, "SMTP_USERNAME", None)
        self.password = getattr(Config, "SMTP_PASSWORD", None)
        self.from_addr = getattr(Config, "SMTP_FROM_ADDRESS", None) or self.username
        # Display name on the From: line -- without it Gmail shows the bare
        # address, which reads like a bot and hurts deliverability.
        self.from_name = getattr(Config, "SMTP_FROM_NAME", None) or "SymbioLink AI"

        self.available = bool(self.host and self.username and self.password)
        if not self.available:
            print("Warning: SMTP credentials not configured. Email service will be in simulation mode.")

    # ---------------------------------------------------------------- build

    def _build_message(self, to_addr, subject, body_text, body_html=None):
        """Assemble one message.

        multipart/alternative with the plain part FIRST is not stylistic --
        the RFC says clients render the *last* part they understand, so
        text-then-HTML is what makes an HTML-capable client show the pretty
        version while a text-only one still gets readable content.
        """
        if body_html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body_text, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))
        else:
            msg = MIMEMultipart()
            msg.attach(MIMEText(body_text, "plain", "utf-8"))

        msg["From"] = formataddr((str(Header(self.from_name, "utf-8")), self.from_addr))
        msg["To"] = to_addr
        msg["Subject"] = str(Header(subject, "utf-8"))
        # Date and Message-ID are required by RFC 5322. Gmail will accept a
        # message without them but spam filters mark it down for looking
        # machine-generated, which is exactly what a bulk broadcast can't afford.
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain="symbiolink.ai")
        return msg

    def _connect(self):
        """Open an authenticated SMTP session.

        Handles both common Gmail shapes: port 465 is implicit TLS
        (SMTP_SSL, encrypted from the first byte) while 587 is STARTTLS
        (plaintext greeting, then upgrade). Picking the wrong one is the
        single most common "it just hangs" misconfiguration, so it's keyed
        off the port rather than needing another env var.
        """
        context = ssl.create_default_context()
        if int(self.port) == 465:
            server = smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=context)
        else:
            server = smtplib.SMTP(self.host, self.port, timeout=20)
            server.ehlo()
            server.starttls(context=context)
            server.ehlo()
        server.login(self.username, self.password)
        return server

    # ----------------------------------------------------------------- send

    def _send(self, to_addr, subject, body_text, body_html=None):
        """Send to a single recipient. Opens and closes its own connection --
        fine for the one-off notifications, but use _send_many() for anything
        going to a list."""
        if not to_addr:
            return False

        if not self.available:
            self._simulate(to_addr, subject, body_text, body_html)
            return True

        try:
            msg = self._build_message(to_addr, subject, body_text, body_html)
            with self._connect() as server:
                server.sendmail(self.from_addr, [to_addr], msg.as_string())
            return True
        except Exception as e:
            print(f"Error sending email to {to_addr}: {e}")
            return False

    def _send_many(self, recipients, subject, body_text, body_html=None):
        """Send the same email to many recipients over ONE connection.

        Each recipient still gets their own message with only their own
        address in To: -- never a shared To/CC list. Two reasons: a cluster
        of competing MSMEs shouldn't be able to harvest each other's contact
        addresses from a notification header, and a per-recipient message is
        what lets the body be personalized later without restructuring this.

        A single recipient failing (one dead mailbox in the cluster) must not
        abort the rest of the batch, so each send is individually guarded.
        Returns the count actually sent.
        """
        recipients = [r for r in dict.fromkeys(recipients) if r]  # dedupe, keep order
        if not recipients:
            return 0

        if not self.available:
            for addr in recipients:
                self._simulate(addr, subject, body_text, body_html)
            return len(recipients)

        sent = 0
        try:
            with self._connect() as server:
                for addr in recipients:
                    try:
                        msg = self._build_message(addr, subject, body_text, body_html)
                        server.sendmail(self.from_addr, [addr], msg.as_string())
                        sent += 1
                    except Exception as e:
                        # Keep going -- one bad address shouldn't cost the
                        # other 24 recipients their notification.
                        print(f"Error sending email to {addr}: {e}")
        except Exception as e:
            print(f"Error opening SMTP connection for batch of {len(recipients)}: {e}")
        return sent

    def _simulate(self, to_addr, subject, body_text, body_html=None):
        """Simulation mode: print exactly what would have gone out, same
        spirit as whatsapp_service's console-log fallback -- lets the whole
        notification flow be demoed and verified without a real mailbox.
        The HTML part is summarized rather than dumped; 6KB of table markup
        per recipient would bury everything else in the console."""
        tag = f" [+{len(body_html)}B HTML]" if body_html else ""
        print(f"[SIMULATED EMAIL]{tag} To: {to_addr} | Subject: {subject}\n{body_text}\n")

    def send_async(self, recipients, subject, body_text, body_html=None):
        """Hand a batch to a background thread and return immediately.

        Used by the broadcast paths. `recipients` must already be a plain
        list of address strings resolved by the CALLER on the request thread
        -- this thread has no Flask application context and must not touch
        the DB session (SQLAlchemy sessions are not thread-safe, and the
        context wouldn't be pushed here anyway).

        daemon=True so a pending broadcast can never hold the dev server
        open on Ctrl+C.
        """
        recipients = [r for r in dict.fromkeys(recipients) if r]
        if not recipients:
            return 0

        def _worker():
            count = self._send_many(recipients, subject, body_text, body_html)
            print(f"[EMAIL] Broadcast '{subject}' delivered to {count}/{len(recipients)} recipients")

        threading.Thread(target=_worker, name="symbiolink-email", daemon=True).start()
        return len(recipients)

    # ------------------------------------------------------------ recipients

    def _emails_for_unit(self, unit_id):
        """Every address that should hear about this unit's activity: every
        login account tied to this unit_id (a unit can have more than one
        user account, and all of them should be notified, not just whichever
        one happens to be logged in right now) PLUS the unit's own contact
        email captured on the "Register a Listing" form (see
        app.py::register_unit()) -- a unit registered that way has no login
        account at all yet, so the User-table lookup alone would silently
        find nobody to email for it. De-duplicated so a unit whose login
        email happens to match its contact email doesn't get emailed twice."""
        addrs = set()
        try:
            from models import User
            addrs.update(u.email for u in User.query.filter_by(unit_id=unit_id).all() if u.email)
        except Exception as e:
            print(f"Error looking up user emails for unit {unit_id}: {e}")

        try:
            import data
            unit = data.unit_by_id(unit_id)
            if unit and unit.get("email"):
                addrs.add(unit["email"])
        except Exception as e:
            print(f"Error looking up unit contact email for unit {unit_id}: {e}")

        return list(addrs)

    def _emails_for_admins(self):
        try:
            from models import User
            return [u.email for u in User.query.filter_by(role="admin").all() if u.email]
        except Exception as e:
            print(f"Error looking up admin emails: {e}")
            return []

    def all_user_emails(self, exclude_unit_ids=None):
        """Every registered user's address -- the broadcast audience.

        `exclude_unit_ids` drops specific companies' accounts. Two callers
        rely on it, for different reasons: the posting company itself (they
        just filled in the form and watched the confirmation page render, so
        "a new listing was posted" is pure noise to them) and any unit that
        already got the *more specific* new-match email for this same
        listing -- see listing_broadcast.py. Sending both is the fastest way
        to train a cluster to filter these out entirely.

        Deactivated accounts are skipped. is_active is checked with an
        explicit `is not False` rather than a truthiness test because the
        column is nullable and rows seeded before the flag existed have NULL
        there -- treating those as deactivated would silently drop most of
        the cluster from every broadcast.
        """
        excluded = set(exclude_unit_ids or ())
        try:
            from models import User
            return [
                u.email for u in User.query.all()
                if u.email
                and getattr(u, "is_active", True) is not False
                and u.unit_id not in excluded
            ]
        except Exception as e:
            print(f"Error looking up all user emails: {e}")
            return []

    # --------------------------------------------------------------- public

    def notify_unit(self, unit_id, subject, body_text, body_html=None):
        """Email every account behind this unit_id. Best-effort: returns True
        if at least one email was (simulated-or-actually) sent.

        body_html stays optional so the existing plain-text callers in app.py
        and matching.py keep working untouched."""
        recipients = self._emails_for_unit(unit_id)
        return self._send_many(recipients, subject, body_text, body_html) > 0

    def notify_admins(self, subject, body_text, body_html=None):
        return self._send_many(self._emails_for_admins(), subject, body_text, body_html) > 0

    def send_template(self, to_addr, subject, kind, heading, intro, **kwargs):
        """Render a branded email (see email_templates.py) and send it to one
        address. The convenience wrapper most callers actually want -- it
        keeps the text/plain alternative in sync with the HTML automatically,
        which is easy to forget when building both parts by hand."""
        html = email_templates.render(kind, heading, intro, **kwargs)
        text = email_templates.plain_text(heading, intro, **{
            k: v for k, v in kwargs.items() if k != "preheader"
        })
        return self._send(to_addr, subject, text, html)

    def send_template_async(self, to_addr, subject, kind, heading, intro, **kwargs):
        """send_template(), but the SMTP round-trip happens on a worker thread.

        Rendering stays on the caller's thread on purpose: kwargs often carry
        values built from the request (url_for, IP, user agent), and those
        must be resolved while the request context still exists. Only the
        finished strings cross to the worker, which needs no Flask state.

        Use this anywhere a user is waiting on the response. A Gmail send is
        several seconds of TLS + AUTH, which is invisible in a background
        notification but reads as a frozen page when it sits between a click
        and a redirect -- and a page that looks frozen gets clicked again.
        """
        if not to_addr:
            return 0
        html = email_templates.render(kind, heading, intro, **kwargs)
        text = email_templates.plain_text(heading, intro, **{
            k: v for k, v in kwargs.items() if k != "preheader"
        })
        return self.send_async([to_addr], subject, text, html)

    def notify_unit_template(self, unit_id, subject, kind, heading, intro, **kwargs):
        """Branded equivalent of notify_unit() -- renders the HTML email and
        sends it to every address behind `unit_id` over one connection."""
        recipients = self._emails_for_unit(unit_id)
        if not recipients:
            return False
        html = email_templates.render(kind, heading, intro, **kwargs)
        text = email_templates.plain_text(heading, intro, **{
            k: v for k, v in kwargs.items() if k != "preheader"
        })
        return self._send_many(recipients, subject, text, html) > 0

    def notify_admins_template(self, subject, kind, heading, intro, **kwargs):
        """Branded equivalent of notify_admins()."""
        recipients = self._emails_for_admins()
        if not recipients:
            return False
        html = email_templates.render(kind, heading, intro, **kwargs)
        text = email_templates.plain_text(heading, intro, **{
            k: v for k, v in kwargs.items() if k != "preheader"
        })
        return self._send_many(recipients, subject, text, html) > 0

    def broadcast_template(self, recipients, subject, kind, heading, intro, **kwargs):
        """Same as send_template() but to a whole list, in the background.
        Returns how many recipients were queued (not delivered -- delivery
        happens on the worker thread and is logged there)."""
        html = email_templates.render(kind, heading, intro, **kwargs)
        text = email_templates.plain_text(heading, intro, **{
            k: v for k, v in kwargs.items() if k != "preheader"
        })
        return self.send_async(recipients, subject, text, html)


email_service = EmailService()
