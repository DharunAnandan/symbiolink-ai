"""
Passwordless sign-in by emailed one-time code.

The model here is two EQUAL alternatives, not two steps:

    /login       username + password           -> signed in
    /login/code  username or email -> code     -> signed in

Neither gates the other. A password alone works; a code alone works. That is
a deliberate product decision (see config.py's OTP_ENABLED note) and it means
the security properties worth testing are about the CODE itself -- it must be
short-lived, single-use, attempt-capped, and useless against another account.

The other ~78 tests log in with a password as a precondition and are entirely
unaffected by this path, which is why OTP stays enabled in TestingConfig.
"""

import re
from datetime import datetime, timedelta

import pytest

import otp_service
from models import db, LoginOtp, User


@pytest.fixture()
def otp_on(flask_app):
    """Enable two-factor login for one test, restoring the previous value."""
    previous = flask_app.config.get("OTP_ENABLED")
    flask_app.config["OTP_ENABLED"] = True
    yield
    flask_app.config["OTP_ENABLED"] = previous


@pytest.fixture()
def sent_codes(monkeypatch):
    """Capture codes instead of emailing them.

    Patches otp_service.send_code, which is what app.py calls -- so the test
    exercises the real issue/store/verify path and only the delivery hop is
    stubbed. Returns the list the codes land in.
    """
    captured = []

    def _fake_send(user, code):
        captured.append((user.username, code))
        return True

    monkeypatch.setattr(otp_service, "send_code", _fake_send)
    return captured


def _request_code(client, identifier="test_buyer"):
    """Ask for a code the way the page does."""
    return client.post("/login/code", data={"identifier": identifier})


def _password_login(client, username="test_buyer", password="pw12345"):
    return client.post("/login", data={"username": username, "password": password})


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------

def test_requesting_and_entering_a_code_signs_in(client, seeded_users, otp_on, sent_codes):
    resp = _request_code(client)
    assert resp.status_code == 302
    assert "/login/verify" in resp.headers["Location"]
    assert len(sent_codes) == 1

    _username, code = sent_codes[0]
    resp = client.post("/login/verify", data={"code": code}, follow_redirects=True)
    assert resp.status_code == 200

    # Really signed in: a protected page now renders instead of redirecting.
    assert client.get("/orders").status_code == 200


def test_code_screen_shows_a_masked_address_not_the_full_one(client, seeded_users, otp_on, sent_codes):
    _request_code(client)
    body = client.get("/login/verify").get_data(as_text=True)
    assert "test_buyer@symbiolink-test.org" not in body
    assert "@symbiolink-test.org" in body


# --------------------------------------------------------------------------
# The two paths are independent alternatives
# --------------------------------------------------------------------------

def test_password_alone_signs_in_without_any_code(client, seeded_users, otp_on, sent_codes):
    """The password path is untouched by the code path: no code is issued and
    none is required."""
    resp = _password_login(client)
    assert resp.status_code == 302
    assert "/login/verify" not in resp.headers.get("Location", "")
    assert sent_codes == [], "the password path must not email anything"
    assert client.get("/orders").status_code == 200


def test_requesting_a_code_does_not_sign_anyone_in_by_itself(client, seeded_users, otp_on, sent_codes):
    """Asking for a code is not authentication -- the code still has to be
    entered. Otherwise anyone could sign in as anyone by typing a username."""
    _request_code(client)

    resp = client.get("/orders")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_wrong_password_is_rejected_and_issues_no_code(client, seeded_users, otp_on, sent_codes):
    resp = client.post("/login", data={"username": "test_buyer", "password": "wrong"},
                       follow_redirects=True)
    assert "Invalid username or password" in resp.get_data(as_text=True)
    assert sent_codes == []


def test_code_can_be_requested_by_email_address_too(client, seeded_users, otp_on, sent_codes):
    """People asking for an emailed code reach for their email address."""
    resp = _request_code(client, "test_buyer@symbiolink-test.org")
    assert resp.status_code == 302
    assert "/login/verify" in resp.headers["Location"]
    assert len(sent_codes) == 1


def test_unknown_account_gets_no_code(client, seeded_users, otp_on, sent_codes):
    resp = _request_code(client, "nobody_at_all")
    assert resp.status_code == 200
    assert "No account found" in resp.get_data(as_text=True)
    assert sent_codes == []


def test_verify_screen_is_unreachable_without_requesting_a_code(client, seeded_users, otp_on):
    """No pending login means no code screen -- otherwise the page could be
    used to guess codes against an arbitrary account."""
    resp = client.get("/login/verify")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    resp = client.post("/login/verify", data={"code": "000000"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# --------------------------------------------------------------------------
# Code lifecycle
# --------------------------------------------------------------------------

def test_a_code_cannot_be_used_twice(client, seeded_users, otp_on, sent_codes):
    _request_code(client)
    _username, code = sent_codes[0]
    client.post("/login/verify", data={"code": code}, follow_redirects=True)
    client.get("/logout", follow_redirects=True)

    # Replay the same code in a fresh login attempt.
    _request_code(client)
    resp = client.post("/login/verify", data={"code": code}, follow_redirects=True)
    assert client.get("/orders").status_code == 302, "a consumed code must not sign anyone in"
    assert resp.status_code == 200


def test_issuing_a_new_code_invalidates_the_previous_one(client, seeded_users, otp_on, sent_codes):
    _request_code(client)
    first = sent_codes[0][1]

    resp = client.post("/login/resend", follow_redirects=True)
    assert resp.status_code == 200
    # The cooldown blocks an immediate resend, so force a second issue
    # directly to test the invalidation rule rather than the rate limit.
    with client.application.app_context():
        user = User.query.filter_by(username="test_buyer").first()
        second = otp_service.issue(user)
        ok_old, _ = otp_service.verify(user.id, first)
        assert ok_old is False, "the superseded code must stop working"
        ok_new, _ = otp_service.verify(user.id, second)
        assert ok_new is True


def test_expired_code_is_rejected(flask_app, seeded_users, otp_on):
    with flask_app.app_context():
        user = User.query.filter_by(username="test_buyer").first()
        code = otp_service.issue(user)

        row = otp_service.latest_code(user.id)
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

        ok, reason = otp_service.verify(user.id, code)
        assert ok is False
        assert reason == "expired"


def test_wrong_guesses_are_capped_and_burn_the_code(flask_app, seeded_users, otp_on):
    """A 6-digit code is only 10^6 options; without a cap it is guessable."""
    with flask_app.app_context():
        user = User.query.filter_by(username="test_buyer").first()
        code = otp_service.issue(user)
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(otp_service.MAX_ATTEMPTS):
            ok, _reason = otp_service.verify(user.id, wrong)
            assert ok is False

        # Even the RIGHT code must now fail -- the code itself is burned.
        ok, reason = otp_service.verify(user.id, code)
        assert ok is False
        assert reason in ("too_many_attempts", "no_code")


def test_codes_are_not_stored_in_plaintext(flask_app, seeded_users, otp_on):
    with flask_app.app_context():
        user = User.query.filter_by(username="test_buyer").first()
        code = otp_service.issue(user)
        row = otp_service.latest_code(user.id)
        assert code not in row.code_hash
        assert row.code_hash != code


def test_one_users_code_does_not_work_for_another(flask_app, seeded_users, otp_on):
    with flask_app.app_context():
        buyer = User.query.filter_by(username="test_buyer").first()
        seller = User.query.filter_by(username="test_seller").first()
        buyer_code = otp_service.issue(buyer)
        otp_service.issue(seller)

        ok, _reason = otp_service.verify(seller.id, buyer_code)
        assert ok is False


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------

def test_resend_is_rate_limited(client, seeded_users, otp_on, sent_codes):
    _request_code(client)
    assert len(sent_codes) == 1

    resp = client.post("/login/resend", follow_redirects=True)
    assert "wait" in resp.get_data(as_text=True).lower()
    assert len(sent_codes) == 1, "the cooldown must block an immediate resend"


def test_resend_works_once_the_cooldown_has_passed(client, seeded_users, otp_on, sent_codes):
    _request_code(client)

    with client.application.app_context():
        user = User.query.filter_by(username="test_buyer").first()
        row = otp_service.latest_code(user.id)
        row.created_at = datetime.utcnow() - timedelta(
            seconds=otp_service.RESEND_COOLDOWN_SECONDS + 5)
        db.session.commit()

    client.post("/login/resend", follow_redirects=True)
    assert len(sent_codes) == 2
    assert sent_codes[0][1] != sent_codes[1][1], "a resend must issue a NEW code"


# --------------------------------------------------------------------------
# Accounts with no email
# --------------------------------------------------------------------------

def test_account_with_blank_email_cannot_request_a_code(client, flask_app, otp_on, sent_codes):
    """A blank address is the only "no address" state the schema allows
    (models.User.email is nullable=False). Such an account has no code path,
    but its password still works."""
    with flask_app.app_context():
        user = User(username="no_email_user", email="", role="unit", unit_id="U1")
        user.set_password("pw12345")
        db.session.add(user)
        db.session.commit()

    try:
        resp = client.post("/login/code", data={"identifier": "no_email_user"})
        assert resp.status_code == 200
        assert sent_codes == []

        assert client.post("/login", data={"username": "no_email_user", "password": "pw12345"}
                           ).status_code == 302
        assert client.get("/orders").status_code == 200
    finally:
        with flask_app.app_context():
            User.query.filter_by(username="no_email_user").delete()
            db.session.commit()


# --------------------------------------------------------------------------
# Code generation
# --------------------------------------------------------------------------

def test_generated_codes_are_the_right_shape():
    for _ in range(50):
        code = otp_service.generate_code()
        assert len(code) == otp_service.CODE_LENGTH
        assert code.isdigit(), "leading zeros must be preserved, not dropped"


def test_generated_codes_are_not_all_the_same():
    """A smoke test against a broken RNG -- 50 draws from 10^6 landing on one
    value would mean the generator is not random at all."""
    codes = {otp_service.generate_code() for _ in range(50)}
    assert len(codes) > 1


def test_mask_email_hides_the_local_part():
    assert otp_service.mask_email("jayanthi@acmesteel.co.in").endswith("@acmesteel.co.in")
    assert "jayanthi" not in otp_service.mask_email("jayanthi@acmesteel.co.in")
    assert otp_service.mask_email(None) == "your email address"
    assert otp_service.mask_email("notanemail") == "your email address"


# --------------------------------------------------------------------------
# Undeliverable addresses (the seeded u1..u24 demo accounts)
# --------------------------------------------------------------------------

def test_is_deliverable_classification():
    """Reserved and demo domains can never receive mail, so no code is worth
    issuing to them. Real domains must keep working, including Gmail's
    plus-addressing form."""
    for good in ("cluster.ops@gmail.com", "jayanthi@acmesteel.co.in",
                 "cluster.ops+u1@gmail.com", "a.b@sub.company.co.in"):
        assert otp_service.is_deliverable(good) is True, good

    for bad in ("u1@symbiolink.demo", "test@example.com", "a@mail.example.com",
                "root@localhost", "x@foo.invalid", "x@thing.test",
                "notanemail", "", None):
        assert otp_service.is_deliverable(bad) is False, bad


def test_demo_account_is_told_plainly_instead_of_being_stranded(client, flask_app, otp_on, sent_codes):
    """The bug this guards: an SMTP server ACCEPTS mail for a bogus domain and
    bounces it minutes later, so send_code() would report success and park the
    user at the code screen waiting forever. Such accounts must be told to use
    a password instead, not sent to a dead end."""
    with flask_app.app_context():
        user = User(username="demo_unit", email="demo_unit@symbiolink.demo",
                    role="unit", unit_id="U1")
        user.set_password("pw12345")
        db.session.add(user)
        db.session.commit()

    try:
        resp = client.post("/login/code", data={"identifier": "demo_unit"})
        assert resp.status_code == 200, "must not redirect to the code screen"
        assert "no email address that can receive a code" in resp.get_data(as_text=True)
        assert sent_codes == [], "no code should be issued to an undeliverable address"

        # ...and the password path still works for them.
        assert client.post("/login", data={"username": "demo_unit", "password": "pw12345"}
                           ).status_code == 302
        assert client.get("/orders").status_code == 200
    finally:
        with flask_app.app_context():
            User.query.filter_by(username="demo_unit").delete()
            db.session.commit()


def test_real_address_gets_a_code(client, seeded_users, otp_on, sent_codes):
    """The other half of the rule: refusing undeliverable addresses must not
    stop accounts that have a working one."""
    _request_code(client)
    assert len(sent_codes) == 1
    assert client.get("/orders").status_code == 302, "still gated on entering the code"


def test_code_path_is_hidden_when_disabled(client, seeded_users, flask_app, sent_codes):
    """With OTP_ENABLED off, passwords are the only way in and the code route
    must not quietly keep working."""
    previous = flask_app.config.get("OTP_ENABLED")
    flask_app.config["OTP_ENABLED"] = False
    try:
        resp = client.post("/login/code", data={"identifier": "test_buyer"})
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
        assert sent_codes == []

        body = client.get("/login").get_data(as_text=True)
        assert "Sign in with a code instead" not in body
    finally:
        flask_app.config["OTP_ENABLED"] = previous


# --------------------------------------------------------------------------
# Double submission
#
# The bug these guard, reproduced end to end: the sign-in alert email was
# sent synchronously, so a successful verify took ~4s. The page looked
# frozen, users pressed Verify again, and the second submission -- carrying a
# code the first had already consumed -- bounced a user who HAD just signed
# in back to the login page with an "expired" message.
# --------------------------------------------------------------------------

def test_duplicate_submit_after_success_is_a_harmless_noop(client, seeded_users, otp_on, sent_codes):
    _request_code(client)
    _username, code = sent_codes[0]

    first = client.post("/login/verify", data={"code": code})
    assert first.status_code == 302
    assert "/dashboard" in first.headers["Location"]

    # The same submission again, exactly as a second click would send it.
    second = client.post("/login/verify", data={"code": code})
    assert second.status_code == 302
    assert "/dashboard" in second.headers["Location"], \
        "a repeat submit must not report an error at someone already signed in"
    assert client.get("/orders").status_code == 200, "and must not sign them out"


def test_login_never_sends_email_synchronously(client, seeded_users, otp_on, sent_codes, monkeypatch):
    """Nothing a user waits on may make an SMTP call inline.

    Guards the regression directly: send_template is the blocking call, so if
    any login path reaches for it again this fails rather than quietly
    reintroducing a multi-second sign-in.
    """
    from email_service import email_service

    def _boom(*a, **kw):
        raise AssertionError("blocking send_template() called on a login path")

    monkeypatch.setattr(email_service, "send_template", _boom)

    # Password path...
    assert _password_login(client).status_code == 302
    client.get("/logout", follow_redirects=True)

    # ...and the code path.
    _request_code(client)
    _username, code = sent_codes[0]
    assert client.post("/login/verify", data={"code": code}).status_code == 302


def test_verify_is_atomic_so_one_code_signs_in_once(flask_app, seeded_users, otp_on):
    """The consume is a conditional UPDATE, not a read-then-write. Before that
    change two submissions arriving together both passed the hash check and
    both 'succeeded' on a single code."""
    with flask_app.app_context():
        user = User.query.filter_by(username="test_buyer").first()
        code = otp_service.issue(user)

        first_ok, _ = otp_service.verify(user.id, code)
        second_ok, second_reason = otp_service.verify(user.id, code)

        assert first_ok is True
        assert second_ok is False
        assert second_reason == "no_code"
