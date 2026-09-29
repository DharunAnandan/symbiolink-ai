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

import logging
import smtplib
import ssl
import threading
import time
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid

import email_templates
from config import Config

logger = logging.getLogger("symbiolink.email")


class EmailService:
    def __init__(self):
        self.host = getattr(Config, "SMTP_HOST", None)
        self.port = getattr(Config, "SMTP_PORT", None) or 587
        self.username = getattr(Config, "SMTP_USERNAME", None)
        # Gmail shows app passwords grouped as "abcd efgh ijkl mnop"; accept
        # either form, as .env.example promises.
        self.password = (getattr(Config, "SMTP_PASSWORD", None) or "").replace(" ", "") or None
        self.from_addr = getattr(Config, "SMTP_FROM_ADDRESS", None) or self.username
        # Display name on the From: line -- without it Gmail shows the bare
        # address, which reads like a bot and hurts deliverability.
        self.from_name = getattr(Config, "SMTP_FROM_NAME", None) or "SymbioLink AI"

        # Brevo's HTTP API is used instead of SMTP when BREVO_API_KEY is set.
        # Hosts like Render's free tier block outbound SMTP ports entirely,
        # and an HTTPS API call goes through where SMTP can't. Free plan:
        # 300 emails/day, sending from a sender address verified in Brevo.
        self.brevo_key = getattr(Config, "BREVO_API_KEY", None)
        if self.brevo_key:
            self.from_addr = getattr(Config, "SMTP_FROM_ADDRESS", None) or self.from_addr or ""
        self.transport = "brevo" if self.brevo_key else "smtp"

        self.available = bool(self.brevo_key or (self.host and self.username and self.password))
        # Short machine-readable reason for the most recent failed send
        # ("quota", "auth", "error"), so callers can tell the user something
        # more useful than "could not send".
        self.last_error = None
        if not self.available:
            logger.warning("SMTP credentials not configured. Email service will be in simulation mode.")
        elif self.brevo_key and not self.from_addr:
            logger.warning("BREVO_API_KEY is set but SMTP_FROM_ADDRESS is empty; Brevo needs a verified sender.")

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

        self.last_error = None
        if self.transport == "brevo":
            return self._send_brevo(to_addr, subject, body_text, body_html)

        msg = self._build_message(to_addr, subject, body_text, body_html)
        # One retry: Gmail occasionally drops a fresh connection mid-handshake,
        # and a user waiting on a sign-in code shouldn't pay for that blip.
        # Permanent 5xx rejections (quota, bad recipient) are not retried.
        for attempt in (1, 2):
            try:
                with self._connect() as server:
                    server.sendmail(self.from_addr, [to_addr], msg.as_string())
                logger.info("Email sent to %s: %s", to_addr, subject)
                return True
            except smtplib.SMTPAuthenticationError:
                logger.exception("SMTP login rejected for %s -- check SMTP_USERNAME/SMTP_PASSWORD", self.username)
                self.last_error = "auth"
                return False
            except (smtplib.SMTPDataError, smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
                self.last_error = "quota" if _is_quota_error(e) else "error"
                logger.error("Email to %s rejected by server: %s", to_addr, e)
                return False
            except Exception:
                logger.exception("Error sending email to %s (attempt %d)", to_addr, attempt)
                self.last_error = "error"
                if attempt == 1:
                    time.sleep(1)
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
        # dedupe (keeping order) and drop placeholder addresses such as the
        # seeded demo units' @symbiolink.demo -- Gmail accepts those and
        # bounces them later, which only fills the sender's inbox with errors.
        recipients = [r for r in dict.fromkeys(recipients) if r and _deliverable(r)]
        if not recipients:
            return 0

        if not self.available:
            for addr in recipients:
                self._simulate(addr, subject, body_text, body_html)
            return len(recipients)

        if self.transport == "brevo":
            sent = 0
            for addr in recipients:
                if self._send_brevo(addr, subject, body_text, body_html):
                    sent += 1
                elif self.last_error == "quota":
                    break
            return sent

        sent = 0
        try:
            with self._connect() as server:
                for addr in recipients:
                    try:
                        msg = self._build_message(addr, subject, body_text, body_html)
                        server.sendmail(self.from_addr, [addr], msg.as_string())
                        sent += 1
                    except Exception as e:
                        if _is_quota_error(e):
                            # Every further send will fail the same way.
                            logger.error("Gmail sending limit reached; skipping remaining recipients: %s", e)
                            break
                        # Keep going -- one bad address shouldn't cost the
                        # other 24 recipients their notification.
                        logger.exception("Error sending email to %s", addr)
        except Exception:
            logger.exception("Error opening SMTP connection for batch of %d", len(recipients))
        return sent

    def _send_brevo(self, to_addr, subject, body_text, body_html=None):
        """One email through Brevo's transactional API (HTTPS, port 443).
        Same non-raising contract as _send(); sets last_error on failure."""
        import requests
        payload = {
            "sender": {"name": self.from_name, "email": self.from_addr},
            "to": [{"email": to_addr}],
            "subject": subject,
            "textContent": body_text,
        }
        if body_html:
            payload["htmlContent"] = body_html
        try:
            resp = requests.post(
                "https://api.brevo.com/v3/smtp/email",
                headers={"api-key": self.brevo_key, "accept": "application/json",
                         "content-type": "application/json"},
                json=payload, timeout=20,
            )
        except Exception:
            logger.exception("Error reaching Brevo to email %s", to_addr)
            self.last_error = "error"
            return False
        if resp.status_code in (200, 201, 202):
            logger.info("Email sent to %s via Brevo: %s", to_addr, subject)
            return True
        body = resp.text[:300]
        self.last_error = ("quota" if resp.status_code in (402, 429) or "limit" in body.lower()
                           else "auth" if resp.status_code == 401 else "error")
        logger.error("Brevo rejected email to %s: HTTP %s %s", to_addr, resp.status_code, body)
        return False

    def _simulate(self, to_addr, subject, body_text, body_html=None):
        """Simulation mode: print exactly what would have gone out, same
        spirit as whatsapp_service's console-log fallback -- lets the whole
        notification flow be demoed and verified without a real mailbox.
        The HTML part is summarized rather than dumped; 6KB of table markup
        per recipient would bury everything else in the console."""
        tag = f" [+{len(body_html)}B HTML]" if body_html else ""
        logger.info("[SIMULATED EMAIL]%s To: %s | Subject: %s\n%s\n", tag, to_addr, subject, body_text)

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
            logger.info("Email '%s' delivered to %d/%d recipients", subject, count, len(recipients))

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
        except Exception:
            logger.exception(f"Error looking up user emails for unit {unit_id}")

        try:
            import data
            unit = data.unit_by_id(unit_id)
            if unit and unit.get("email"):
                addrs.add(unit["email"])
        except Exception:
            logger.exception(f"Error looking up unit contact email for unit {unit_id}")

        return list(addrs)

    def _emails_for_admins(self):
        """Admin inboxes: ADMIN_EMAIL from the environment (the seeded admin
        account's own address is a placeholder that can't receive mail) plus
        any admin account with a real address."""
        configured = [a.strip() for a in (getattr(Config, "ADMIN_EMAIL", "") or "").split(",") if a.strip()]
        try:
            from models import User
            return list(dict.fromkeys(
                configured + [u.email for u in User.query.filter_by(role="admin").all() if u.email]
            ))
        except Exception:
            logger.exception("Error looking up admin emails")
            return configured

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
        except Exception:
            logger.exception("Error looking up all user emails")
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


def _is_quota_error(exc):
    """Gmail answers 550 5.4.5 "Daily user sending limit exceeded" once the
    account has sent ~500 messages in a rolling 24 hours."""
    text = str(exc).lower()
    return "5.4.5" in text or "sending limit" in text or "quota" in text


def _deliverable(addr):
    try:
        from otp_service import is_deliverable
        return is_deliverable(addr)
    except Exception:
        return True


email_service = EmailService()
