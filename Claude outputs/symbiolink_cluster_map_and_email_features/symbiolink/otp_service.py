"""
One-time passcodes for two-factor login.

The flow this backs (see app.py's login/verify_otp routes):

    1. User submits username + password.
    2. Password checks out -- but NOTHING is logged in yet. A 6-digit code is
       generated, hashed, stored, and emailed.
    3. User submits the code. Only once it verifies does login_user() run.

The single most important property of that ordering: a correct password
alone must never produce an authenticated session. Flask-Login's login_user()
is therefore called in exactly one place in the whole OTP path -- after
verify() returns ok -- and the intermediate state lives in the session as a
plain "who is half-way through logging in" marker with no privileges of its
own.

Policy knobs are the module constants below rather than config so there is
one obvious place to read the security posture off. All of them are
deliberately conservative:

  * codes are HASHED, never stored in plaintext (see models.LoginOtp)
  * a code dies on first successful use (replay protection)
  * issuing a new code invalidates every older live one for that user, so a
    long email thread of old codes is not a pile of live keys
  * wrong guesses are capped, because a 6-digit code is only 10^6 options
  * resends are rate-limited, so this can't be turned into a mail bomb

Like the rest of the notification layer, sending is best-effort -- but unlike
notifications, a send FAILURE here must be surfaced, not swallowed: a user
who never receives the code cannot log in, and silently showing them a
"enter your code" screen would be a dead end. send_code() therefore returns a
bool the route acts on.
"""

import secrets
from datetime import datetime, timedelta

from werkzeug.security import check_password_hash, generate_password_hash

from models import db, LoginOtp

# 6 digits is the familiar shape for this kind of code. The brute-force
# resistance comes from MAX_ATTEMPTS and the short TTL, not from the length --
# see the module docstring.
CODE_LENGTH = 6

# Long enough to switch to a mail client, find the message and type it back;
# short enough that a code left sitting in an inbox is not a standing key.
TTL_MINUTES = 10

# Wrong guesses allowed against one code before it is burned. At 5 tries per
# code, guessing a 6-digit code is a 1-in-200,000 shot per issued code.
MAX_ATTEMPTS = 5

# Minimum gap between "resend the code" requests for one user.
RESEND_COOLDOWN_SECONDS = 60

# How long a half-finished login may sit at the code screen before the user
# has to start again with their password. Independent of TTL_MINUTES: this
# one bounds the lifetime of the pending-login session marker.
PENDING_LOGIN_MINUTES = 15


def generate_code():
    """A cryptographically random numeric code.

    secrets, not random: random is a Mersenne Twister seeded from the clock
    and its output is predictable from previous draws, which is exactly the
    property an attacker needs. zfill keeps leading zeros, so "004213" stays
    six characters and the user types what they were sent.
    """
    upper = 10 ** CODE_LENGTH
    return str(secrets.randbelow(upper)).zfill(CODE_LENGTH)


def issue(user, ip_address=None):
    """Create and store a fresh code for `user`. Returns the plaintext code.

    The plaintext is returned (never stored) purely so the caller can put it
    in the email. Everything persisted is the hash.

    Any earlier live codes for this user are invalidated first: without that,
    every code a user ever requested would stay usable until its own expiry,
    so requesting three codes would leave three live keys rather than
    replacing the first.
    """
    invalidate_live_codes(user.id)

    code = generate_code()
    otp = LoginOtp(
        user_id=user.id,
        code_hash=generate_password_hash(code),
        expires_at=datetime.utcnow() + timedelta(minutes=TTL_MINUTES),
        ip_address=(ip_address or "")[:64] or None,
    )
    db.session.add(otp)
    db.session.commit()
    return code


def invalidate_live_codes(user_id):
    """Burn every still-usable code for this user.

    Marks them consumed rather than deleting, keeping the audit trail intact
    (see models.LoginOtp's docstring). Used when issuing a replacement and
    after a successful login.
    """
    now = datetime.utcnow()
    live = LoginOtp.query.filter(
        LoginOtp.user_id == user_id,
        LoginOtp.consumed_at.is_(None),
        LoginOtp.expires_at > now,
    ).all()
    for otp in live:
        otp.consumed_at = now
    if live:
        db.session.commit()
    return len(live)


def latest_code(user_id):
    """The most recently issued code row for a user, live or not."""
    return (LoginOtp.query
            .filter_by(user_id=user_id)
            .order_by(LoginOtp.created_at.desc())
            .first())


def seconds_until_resend(user_id):
    """How long the user must wait before another code may be sent.

    0 means "may send now". Returning the remaining seconds (rather than a
    bare bool) lets the page tell the user how long to wait, which is the
    difference between a rate limit that feels broken and one that feels
    deliberate.
    """
    latest = latest_code(user_id)
    if not latest:
        return 0
    elapsed = (datetime.utcnow() - latest.created_at).total_seconds()
    remaining = RESEND_COOLDOWN_SECONDS - elapsed
    return int(remaining) + 1 if remaining > 0 else 0


def verify(user_id, submitted_code):
    """Check a submitted code. Returns (ok: bool, reason: str).

    `reason` is a short machine-readable token the route maps to a message:
    "ok", "no_code", "expired", "too_many_attempts", "mismatch".

    A wrong guess increments attempts on the live code and, at the cap, burns
    it -- so an attacker gets MAX_ATTEMPTS tries per issued code rather than
    unlimited tries against a code that stays valid for its full TTL.

    Note this deliberately does NOT distinguish "no code was ever issued"
    from "the code expired" in a way the caller must leak to the user; both
    land the user back at "request a new code", which is the only useful
    action either way.
    """
    submitted = (submitted_code or "").strip().replace(" ", "").replace("-", "")
    if not submitted:
        return False, "mismatch"

    otp = latest_code(user_id)
    if otp is None or otp.consumed_at is not None:
        return False, "no_code"

    if datetime.utcnow() >= otp.expires_at:
        return False, "expired"

    if otp.attempts >= MAX_ATTEMPTS:
        return False, "too_many_attempts"

    # check_password_hash is constant-time for the comparison itself, which
    # matters more here than it looks: a naive == on a short numeric code is
    # a textbook timing-oracle target.
    if not check_password_hash(otp.code_hash, submitted):
        otp.attempts += 1
        burned = otp.attempts >= MAX_ATTEMPTS
        if burned:
            # Burn it outright rather than leaving a dead-but-unexpired row
            # that verify() would keep re-reading.
            otp.consumed_at = datetime.utcnow()
        db.session.commit()
        return False, "too_many_attempts" if burned else "mismatch"

    # Consume atomically. A plain `otp.consumed_at = now; commit()` is a
    # read-then-write: two submissions arriving together both read the row as
    # live, both pass the hash check, and both "succeed" -- reproduced, a
    # single code signed in two concurrent requests. A conditional UPDATE lets
    # the database arbitrate instead: only one request can flip consumed_at
    # from NULL, and the loser sees zero rows affected.
    claimed = (LoginOtp.query
               .filter(LoginOtp.id == otp.id, LoginOtp.consumed_at.is_(None))
               .update({LoginOtp.consumed_at: datetime.utcnow()},
                       synchronize_session=False))
    db.session.commit()
    if claimed != 1:
        return False, "no_code"
    return True, "ok"


# Domains whose addresses can never actually receive mail, so there is no
# point issuing a code to them.
#
# This exists because of a genuinely nasty failure mode: an SMTP server
# ACCEPTS a message for a non-existent domain and only bounces it minutes
# later, asynchronously. send_code() therefore returns True, the app believes
# the code was delivered, and the user is parked on the code screen forever
# with no error to explain why nothing arrives. Checking the address up front
# is the only way to catch that before it strands someone.
#
# Covers the seeded demo accounts (symbiolink.demo) plus the domains RFC 2606
# and RFC 6761 reserve precisely so they can never resolve.
UNDELIVERABLE_DOMAINS = {
    "symbiolink.demo",
    "example.com", "example.net", "example.org",
    "test", "invalid", "localhost", "example", "local",
}


def is_deliverable(address):
    """Whether a code could realistically reach this address.

    Deliberately conservative: it only rejects addresses that are *structurally*
    incapable of receiving mail (malformed, or on a reserved/demo domain). It
    does not try to verify that a real mailbox exists on a real domain -- that
    needs an SMTP probe, which is slow, unreliable and widely treated as
    abuse. A typo'd address on a real domain still gets a code, bounces, and
    is the user's to notice.
    """
    if not address or "@" not in address:
        return False

    domain = address.rsplit("@", 1)[-1].strip().lower()
    if not domain:
        return False

    # A bare hostname with no dot ("user@localhost") is not a public mail
    # domain, so nothing sent there leaves the machine.
    if "." not in domain:
        return False

    # Reserved and demo domains, including subdomains of them
    # (mail.example.com, foo.invalid).
    return not any(domain == d or domain.endswith("." + d)
                   for d in UNDELIVERABLE_DOMAINS)


def mask_email(address):
    """Obscure an address for display on the code-entry screen.

    The screen has to confirm WHERE the code went, or a user with several
    addresses cannot tell which inbox to check -- but printing the full
    address would hand it to anyone who can reach that screen with a stolen
    password. "dharun@vertace.com" becomes "dh****@vertace.com".
    """
    if not address or "@" not in address:
        return "your email address"
    local, _, domain = address.partition("@")
    if len(local) <= 2:
        shown = local[:1]
    else:
        shown = local[:2]
    return f"{shown}{'*' * max(3, len(local) - len(shown))}@{domain}"


def send_code(user, code):
    """Email the code. Returns True if it was handed to the mail layer.

    Uses the same branded template as every other email in the app (see
    email_templates.py), with the code rendered as a large monospaced block
    rather than buried in a sentence -- people copy these at a glance.

    Returns False on failure so the caller can tell the user their code could
    not be sent, instead of parking them on a code screen that will never
    receive anything.
    """
    from email_service import email_service

    if not getattr(user, "email", None):
        return False

    try:
        return bool(email_service.send_template(
            user.email,
            f"{code} is your SymbioLink AI sign-in code",
            "login",
            "Your sign-in code",
            (f"Enter this code to finish signing in as {user.username}. "
             f"It expires in {TTL_MINUTES} minutes and can only be used once."),
            code=code,
            details=[
                ("Account", user.username),
                ("Requested", datetime.now().strftime("%d %b %Y, %I:%M %p")),
                ("Valid for", f"{TTL_MINUTES} minutes"),
            ],
            preheader=f"Your SymbioLink AI code is {code}. It expires in {TTL_MINUTES} minutes.",
            # No "--" in user-facing copy: this string is HTML-escaped, not
            # typographically processed, so it would render as two literal
            # hyphens in the email rather than a dash.
            footnote=("If you did not try to sign in, someone may have your password. "
                      "Never share this code. SymbioLink AI staff will never ask "
                      "you for it."),
        ))
    except Exception as e:
        print(f"Error sending OTP to {user.email}: {e}")
        return False


def purge_expired(older_than_days=30):
    """Drop OTP rows past their usefulness as an audit trail.

    Not called automatically -- there is no scheduler in this app -- but kept
    here so the table has an obvious maintenance path rather than growing
    forever. Returns the number of rows removed.
    """
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    stale = LoginOtp.query.filter(LoginOtp.created_at < cutoff).all()
    for otp in stale:
        db.session.delete(otp)
    if stale:
        db.session.commit()
    return len(stale)
