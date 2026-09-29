"""
Registration with email verification, account deletion, order emails to
buyer/seller/admin, and the AI assistant endpoint.
"""

import pytest

import assistant
import data
import data_access
import order_emails
import orders as orders_module
import otp_service
from config import Config
from email_service import email_service
from models import db, User


@pytest.fixture()
def sent_codes(monkeypatch):
    captured = []

    def _fake_send(user, code, purpose="login"):
        captured.append((user.email, code, purpose))
        return True

    monkeypatch.setattr(otp_service, "send_code", _fake_send)
    return captured


@pytest.fixture()
def cleanup_signups(flask_app):
    """Remove any account/company a registration test created."""
    units_before = list(data.UNITS)
    yield
    with flask_app.app_context():
        for u in User.query.filter(User.email.like("%@newco-test.org")).all():
            db.session.delete(u)
        db.session.commit()
    data.UNITS[:] = units_before


def _register(client, **overrides):
    form = {
        "username": "newco", "company_name": "NewCo Test Recyclers", "category": "metal",
        "email": "owner@newco-test.org", "password": "secret123", "confirm_password": "secret123",
    }
    form.update(overrides)
    return client.post("/register", data=form)


def test_register_requires_email_code_before_account_is_usable(client, flask_app, sent_codes, cleanup_signups):
    resp = _register(client)
    assert resp.status_code == 302 and resp.headers["Location"].endswith("/register/verify")
    assert sent_codes and sent_codes[-1][0] == "owner@newco-test.org" and sent_codes[-1][2] == "register"

    with flask_app.app_context():
        user = User.query.filter_by(email="owner@newco-test.org").one()
        assert user.is_active is False and user.unit_id is None

    # Not signed in yet, and the password alone can't get in.
    assert client.get("/dashboard").status_code == 302
    other = flask_app.test_client()
    resp = other.post("/login", data={"username": "newco", "password": "secret123"})
    assert b"verify your email" in resp.data

    resp = client.post("/register/verify", data={"code": sent_codes[-1][1]})
    assert resp.status_code == 302
    with flask_app.app_context():
        user = User.query.filter_by(email="owner@newco-test.org").one()
        assert user.is_active is True and user.unit_id
        assert data.unit_by_id(user.unit_id)["name"] == "NewCo Test Recyclers"
    assert client.get("/dashboard").status_code == 200


def test_wrong_code_keeps_account_inactive(client, flask_app, sent_codes, cleanup_signups):
    _register(client)
    code = sent_codes[-1][1]
    wrong = "000000" if code != "000000" else "111111"
    resp = client.post("/register/verify", data={"code": wrong})
    assert resp.status_code == 200 and b"not right" in resp.data
    with flask_app.app_context():
        assert User.query.filter_by(email="owner@newco-test.org").one().is_active is False


def test_abandoned_signup_can_be_retried_with_same_email(client, flask_app, sent_codes, cleanup_signups):
    _register(client)
    resp = _register(flask_app.test_client(), username="newco2")
    assert resp.status_code == 302
    with flask_app.app_context():
        assert User.query.filter_by(email="owner@newco-test.org").count() == 1
        assert User.query.filter_by(email="owner@newco-test.org").one().username == "newco2"


def test_existing_company_name_is_rejected(client, sent_codes, cleanup_signups):
    existing = data.UNITS[0]["name"]
    resp = _register(client, company_name=existing.upper())
    assert resp.status_code == 200 and b"already registered" in resp.data
    assert not sent_codes


def test_undeliverable_email_is_rejected(client, sent_codes, cleanup_signups):
    resp = _register(client, email="someone@example.com")
    assert resp.status_code == 200 and b"cannot receive mail" in resp.data


def test_delete_user_account_removes_user_and_company(client, flask_app, sent_codes, cleanup_signups):
    _register(client)
    client.post("/register/verify", data={"code": sent_codes[-1][1]})
    with flask_app.app_context():
        user = User.query.filter_by(email="owner@newco-test.org").one()
        unit_id = user.unit_id
        data.LISTINGS.append({"unit_id": unit_id, "type": "waste", "material": "sawdust",
                              "qty_kg": 50, "interval_days": 7})
        removed = data_access.delete_user_account(user)
        assert removed["unit"] == unit_id
        assert User.query.filter_by(email="owner@newco-test.org").first() is None
    assert all(u["id"] != unit_id for u in data.UNITS)
    assert all(l["unit_id"] != unit_id for l in data.LISTINGS)


def test_order_emails_go_to_buyer_seller_and_admin(flask_app, seeded_users, monkeypatch):
    sends = []
    monkeypatch.setattr(email_service, "send_async",
                        lambda recipients, subject, text, html=None: sends.append((list(recipients), subject, html)))
    with flask_app.app_context():
        order = orders_module.place_order("U2", data.unit_by_id("U2")["name"], "metal_shavings",
                                          "U1", data.unit_by_id("U1")["name"], 100, co2_saved_kg=40)
        order_emails.send_order_emails(order, "placed", "http://localhost/orders")

    by_recipient = {tuple(r): (subj, html) for r, subj, html in sends}
    buyer = next(v for k, v in by_recipient.items() if "test_buyer@symbiolink-test.org" in k)
    seller = next(v for k, v in by_recipient.items() if "test_seller@symbiolink-test.org" in k)
    admin = next(v for k, v in by_recipient.items() if Config.ADMIN_EMAIL in k)
    assert "request sent" in buyer[0]
    assert "New order request" in seller[0] and "Action needed" in seller[1]
    assert admin[0].startswith("[Admin]")
    assert "Accepted" in buyer[1]  # progress tracker rendered


def test_order_accept_email_says_accepted(flask_app, seeded_users, monkeypatch):
    sends = []
    monkeypatch.setattr(email_service, "send_async",
                        lambda recipients, subject, text, html=None: sends.append((list(recipients), subject)))
    with flask_app.app_context():
        order = orders_module.place_order("U2", "Seller", "metal_shavings", "U1", "Buyer", 100)
        orders_module.advance_order(order["id"])
        order_emails.send_order_emails(order, order["status"], "http://localhost/orders")
    subjects = [s for _r, s in sends]
    assert any("accepted by" in s for s in subjects)


def _login(client, username="test_buyer"):
    return client.post("/login", data={"username": username, "password": "pw12345"})


def test_assistant_basic_mode_without_key(client, seeded_users, monkeypatch):
    monkeypatch.setattr(Config, "GEMINI_API_KEY", None)
    _login(client)
    resp = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "text": "price of sawdust"}]})
    body = resp.get_json()
    assert resp.status_code == 200 and body["mode"] == "basic"
    assert "sawdust" in body["reply"].lower()


def test_assistant_uses_gemini_when_key_set(client, seeded_users, monkeypatch):
    monkeypatch.setattr(Config, "GEMINI_API_KEY", "test-key")
    captured = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "Try [Orders](/orders)."}]}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured.update(url=url, headers=headers, body=json)
        return _Resp()

    monkeypatch.setattr(assistant.requests, "post", _fake_post)
    _login(client)
    resp = client.post("/api/assistant/chat",
                       json={"messages": [{"role": "user", "text": "who can buy my waste?"}], "lang": "en"})
    body = resp.get_json()
    assert body == {"reply": "Try [Orders](/orders).", "mode": "ai"}
    assert captured["headers"]["x-goog-api-key"] == "test-key"
    system = captured["body"]["system_instruction"]["parts"][0]["text"]
    assert "COMPANY:" in system and "/market-prices" in system


def test_assistant_requires_login(client):
    resp = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "text": "hi"}]})
    assert resp.status_code in (302, 401)


def test_brevo_transport_sends_over_https(monkeypatch):
    """With BREVO_API_KEY set, email goes through Brevo's API, not SMTP."""
    calls = []

    class _Resp:
        status_code = 201
        text = '{"messageId": "x"}'

    def _fake_post(url, headers=None, json=None, timeout=None):
        calls.append((url, headers, json))
        return _Resp()

    import requests
    monkeypatch.setattr(requests, "post", _fake_post)
    monkeypatch.setattr(email_service, "available", True)
    monkeypatch.setattr(email_service, "transport", "brevo")
    monkeypatch.setattr(email_service, "brevo_key", "brevo-test-key")
    monkeypatch.setattr(email_service, "from_addr", "sender@symbiolink-test.org")
    monkeypatch.setattr(email_service, "_connect", lambda: (_ for _ in ()).throw(AssertionError("SMTP used")))

    assert email_service._send("a@symbiolink-test.org", "Hi", "text", "<b>html</b>") is True
    assert email_service._send_many(["b@symbiolink-test.org", "c@symbiolink-test.org"], "Hi", "t") == 2
    url, headers, body = calls[0]
    assert url == "https://api.brevo.com/v3/smtp/email"
    assert headers["api-key"] == "brevo-test-key"
    assert body["to"] == [{"email": "a@symbiolink-test.org"}]
    assert body["sender"]["email"] == "sender@symbiolink-test.org"


def test_brevo_quota_error_is_reported(monkeypatch):
    class _Resp:
        status_code = 429
        text = "Too many requests"

    import requests
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp())
    monkeypatch.setattr(email_service, "available", True)
    monkeypatch.setattr(email_service, "transport", "brevo")
    monkeypatch.setattr(email_service, "brevo_key", "k")
    assert email_service._send("a@symbiolink-test.org", "Hi", "text") is False
    assert email_service.last_error == "quota"
