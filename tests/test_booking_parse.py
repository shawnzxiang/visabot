from datetime import date

from visabot.client import AisClient
from visabot.verifier import SUCCESS_RE, _date_variants

# Mirrors the live en-ca booking form, field-for-field, as captured 2026-08-01.
# Note the absences: no `utf8`, no `asc_appointment` group.
APPT_PAGE = """
<html><head><meta name="csrf-token" content="metatoken=="></head><body>
<form id="appointment-form" action="/en-ca/niv/schedule/123/appointment" method="post">
  <input type="hidden" name="authenticity_token" value="formtoken==">
  <input type="hidden" name="confirmed_limit_message" value="1">
  <input type="hidden" name="use_consulate_appointment_capacity" value="true">
  <select name="appointments[consulate_appointment][facility_id]">
    <option value="89">Calgary</option><option value="95">Vancouver</option>
  </select>
  <input type="text" name="appointments[consulate_appointment][date]" value="">
  <select name="appointments[consulate_appointment][time]"></select>
  <input type="submit" name="commit" value="Schedule Appointment">
</form></body></html>
"""

MINIMAL_PAGE = '<html><head><meta name="csrf-token" content="onlymeta=="></head><body></body></html>'

# What the live form's own fields imply a browser POSTs, plus the three appointment
# fields the page's JS fills in.
EXPECTED_POST_KEYS = {
    "authenticity_token",
    "confirmed_limit_message",
    "use_consulate_appointment_capacity",
    "commit",
    "appointments[consulate_appointment][facility_id]",
    "appointments[consulate_appointment][date]",
    "appointments[consulate_appointment][time]",
}


def test_parse_booking_form_reads_live_form_fields():
    form = AisClient.parse_booking_form(APPT_PAGE)
    assert form["authenticity_token"] == "formtoken=="
    assert form["confirmed_limit_message"] == "1"
    assert form["use_consulate_appointment_capacity"] == "true"
    assert form["commit"] == "Schedule Appointment"


def test_parse_booking_form_falls_back_to_meta_and_defaults():
    form = AisClient.parse_booking_form(MINIMAL_PAGE)
    assert form["authenticity_token"] == "onlymeta=="
    assert form["confirmed_limit_message"] == "1"
    assert form["use_consulate_appointment_capacity"] == "true"
    assert form["commit"] == "Schedule Appointment"


def test_form_carries_no_utf8_or_asc_fields():
    """Bots written for other countries send these; the Canada form has neither."""
    form = AisClient.parse_booking_form(APPT_PAGE)
    assert "utf8" not in form
    assert not any("asc_appointment" in k for k in form)


def test_post_payload_matches_the_form_exactly(monkeypatch):
    """The POST body must be exactly what a browser would submit — no extras."""
    sent = {}

    class FakeResp:
        status_code = 200
        text = "Successfully Scheduled"
        headers = {"content-type": "text/html"}
        ok = True

    class FakeClient(AisClient):
        def __init__(self):
            self.schedule_id = "123"

        def _request(self, kind, method, url, **kw):
            sent.update(kw.get("data", {}))
            sent["_url"] = url
            return FakeResp()

    c = FakeClient()
    c.post_booking(95, "2026-08-12", "10:45", AisClient.parse_booking_form(APPT_PAGE))
    assert set(sent) - {"_url"} == EXPECTED_POST_KEYS
    assert sent["appointments[consulate_appointment][facility_id]"] == "95"
    assert sent["appointments[consulate_appointment][date]"] == "2026-08-12"
    assert sent["appointments[consulate_appointment][time]"] == "10:45"
    assert sent["_url"].endswith("/schedule/123/appointment")


def test_success_marker():
    assert SUCCESS_RE.search("You have Successfully Scheduled your appointment")
    assert SUCCESS_RE.search("successfully   scheduled")
    assert not SUCCESS_RE.search("could not be scheduled")


def test_date_variants_cover_padded_and_unpadded():
    v = _date_variants(date(2026, 8, 5))
    assert "2026-08-05" in v
    assert "05 August, 2026" in v
    assert "5 August, 2026" in v
    assert "August 5, 2026" in v
