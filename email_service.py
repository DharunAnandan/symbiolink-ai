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
_log_order_history helper.
"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from config import Config


class EmailService:
    def __init__(self):
        self.host = getattr(Config, "SMTP_HOST", None)
        self.port = getattr(Config, "SMTP_PORT", None) or 587
        self.username = getattr(Config, "SMTP_USERNAME", None)
        self.password = getattr(Config, "SMTP_PASSWORD", None)
        self.from_addr = getattr(Config, "SMTP_FROM_ADDRESS", None) or self.username

        self.available = bool(self.host and self.username and self.password)
        if not self.available:
            print("Warning: SMTP credentials not configured. Email service will be in simulation mode.")

    def _send(self, to_addr, subject, body_text):
        if not to_addr:
            return False

        if not self.available:
            # Simulation mode: print exactly what would have gone out, same
            # spirit as whatsapp_service's console-log fallback -- lets the
            # whole notification flow be demoed and verified without needing
            # a real mailbox.
            print(f"[SIMULATED EMAIL] To: {to_addr} | Subject: {subject}\n{body_text}\n")
            return True

        try:
            msg = MIMEMultipart()
            msg["From"] = self.from_addr
            msg["To"] = to_addr
            msg["Subject"] = subject
            msg.attach(MIMEText(body_text, "plain"))

            context = ssl.create_default_context()
            with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                server.starttls(context=context)
                server.login(self.username, self.password)
                server.sendmail(self.from_addr, [to_addr], msg.as_string())
            return True
        except Exception as e:
            print(f"Error sending email to {to_addr}: {e}")
            return False

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

    def notify_unit(self, unit_id, subject, body_text):
        """Email every account behind this unit_id. Best-effort: returns
        True if at least one email was (simulated-or-actually) sent."""
        sent = False
        for addr in self._emails_for_unit(unit_id):
            if self._send(addr, subject, body_text):
                sent = True
        return sent

    def notify_admins(self, subject, body_text):
        sent = False
        for addr in self._emails_for_admins():
            if self._send(addr, subject, body_text):
                sent = True
        return sent


email_service = EmailService()
