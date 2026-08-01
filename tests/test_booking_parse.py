from visabot.client import AisClient
from visabot.verifier import SUCCESS_RE, _date_variants
from datetime import date

APPT_PAGE = """
<html><head><meta name="csrf-token" content="metatoken=="></head><body>
<form action="/en-ca/niv/schedule/123/appointment" method="post">
  <input type="hidden" name="utf8" value="&#x2713;">
  <input type="hidden" name="authenticity_token" value="formtoken==">
  <input type="hidden" name="confirmed_limit_message" value="1">
  <input type="hidden" name="use_consulate_appointment_capacity" value="true">
  <select id="appointments_consulate_appointment_facility_id">
    <option value="89">Calgary</option><option value="95">Vancouver</option>
  </select>
</form></body></html>
"""

MINIMAL_PAGE = '<html><head><meta name="csrf-token" content="onlymeta=="></head><body></body></html>'


def test_parse_booking_form_prefers_hidden_inputs():
    form = AisClient.parse_booking_form(APPT_PAGE)
    assert form["authenticity_token"] == "formtoken=="
    assert form["confirmed_limit_message"] == "1"
    assert form["use_consulate_appointment_capacity"] == "true"


def test_parse_booking_form_falls_back_to_meta_and_defaults():
    form = AisClient.parse_booking_form(MINIMAL_PAGE)
    assert form["authenticity_token"] == "onlymeta=="
    assert form["confirmed_limit_message"] == "1"
    assert form["use_consulate_appointment_capacity"] == "true"


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
