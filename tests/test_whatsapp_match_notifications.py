"""
Tests for the WhatsApp match-notification wiring added to
matching.notify_new_matches_for_unit().

Context: whatsapp_service.send_match_notification() already existed, fully
written and correct -- it's what powers the /whatsapp/test-notification/
<unit_id> dev endpoint -- but it was never actually called from the real
"a new match was just found" event. Only the in-app bell
(notifications.notify_unit) and email (email_service.notify_unit) calls
were. For this app's actual target user -- an MSME owner who intakes over
WhatsApp and may not open the web dashboard for days -- a bell-only alert is
close to worthless, so this wires the existing WhatsApp sender into the same
event the bell and email calls already fire on.
"""

import matching
import whatsapp_service as whatsapp_service_module

# notify_new_matches_for_unit() does `from whatsapp_service import
# whatsapp_service` INSIDE the function body (matching the existing local-
# import style already used there for notifications/email_service), so
# there's no `matching.whatsapp_service` module-level attribute to patch --
# that local import just re-binds to whatever whatsapp_service_module's own
# `whatsapp_service` singleton currently is at call time. Patching the
# METHOD on that singleton instance (rather than replacing the name) is what
# actually takes effect regardless of how/when it gets imported.
whatsapp_singleton = whatsapp_service_module.whatsapp_service


def test_new_match_sends_whatsapp_notification_with_correct_details(flask_app, monkeypatch):
    """U1 (Shivam Metal Works) has a real, seeded exact-match edge to U2
    (Ganga Alloy Casting) over metal_shavings -- see test_matching.py's
    test_exact_material_match_is_found for the same edge. Calling
    notify_new_matches_for_unit("U1", ...) must now also call
    whatsapp_service.send_match_notification(other_id="U2", ...) with match
    details describing U1 (the party that just listed) -- not just write to
    the bell and send an email as before."""
    sent = []
    monkeypatch.setattr(
        whatsapp_singleton, "send_match_notification",
        lambda unit_id, match_details: sent.append((unit_id, match_details)) or True,
    )

    with flask_app.test_request_context():
        matching.notify_new_matches_for_unit("U1", "Shivam Metal Works")

    assert sent, "expected send_match_notification to be called for U1's live match with U2"
    calls_to_u2 = [c for c in sent if c[0] == "U2"]
    assert calls_to_u2, f"expected a WhatsApp notification sent to U2, got calls to {[c[0] for c in sent]}"

    _, details = calls_to_u2[0]
    assert details["partner_name"] == "Shivam Metal Works"
    assert details["material"] == "Metal Shavings"
    assert details["partner_phone"] == "+91-90000-00001"  # U1's own phone, from data.py
    # Savings/distance are pre-formatted strings for the WhatsApp message
    # text, not raw floats -- match the shape send_match_notification's own
    # message-building code expects (f-strings, not numeric formatting).
    assert isinstance(details["savings"], str)
    assert isinstance(details["distance"], str)
    float(details["savings"])  # still parses as a number, just as text
    float(details["distance"])


def test_no_whatsapp_call_when_there_are_no_live_matches(flask_app, monkeypatch):
    """Mirrors the function's existing "safe to call with zero new edges"
    contract (see its own docstring) -- extended to the WhatsApp channel:
    no live edges means nobody gets bothered on any channel, WhatsApp
    included."""
    sent = []
    monkeypatch.setattr(
        whatsapp_singleton, "send_match_notification",
        lambda unit_id, match_details: sent.append((unit_id, match_details)) or True,
    )
    monkeypatch.setattr(matching, "build_edges", lambda: [])

    with flask_app.test_request_context():
        result = matching.notify_new_matches_for_unit("U1", "Shivam Metal Works")

    assert result == []
    assert sent == []


def test_real_unmocked_call_does_not_raise_in_simulation_mode(flask_app):
    """End-to-end sanity check with NO mocking: Twilio credentials are never
    configured under the test config (see whatsapp_service.py's
    _PLACEHOLDER_VALUES / TestingConfig), so whatsapp_service.client is None
    and every send goes through the real simulation-mode branch of
    _send_message() (prints "Simulation mode: would send..." and returns).
    This proves the actual wiring -- not just a mocked stand-in -- runs
    cleanly against U1's real seeded match with U2."""
    with flask_app.test_request_context():
        new_matches = matching.notify_new_matches_for_unit("U1", "Shivam Metal Works")

    assert any(e["from"] == "U1" and e["to"] == "U2" for e in new_matches), (
        "expected the real U1 -> U2 metal_shavings edge to be among the reported matches"
    )
