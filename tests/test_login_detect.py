"""The sign-in form is data-remote: a REJECTED login still returns HTTP 200.
These tests pin the failure detection so the bot never proceeds as 'logged in'.
"""
import pytest

from visabot.client import CAPTCHA_MARKERS, LOGIN_ERROR_MARKERS

# Verbatim shape of a real rejected-login response body (AIS, 2026-08-01)
REJECTED = (
    'auth_partial = $("<form class=\\"simple_form new_user\\" id=\\"sign_in_form\\" '
    'novalidate=\\"novalidate\\" action=\\"/en-ca/niv/users/sign_in\\" '
    "accept-charset=\\\"UTF-8\\\" data-remote=\\\"true\\\" method=\\\"post\\\">"
    "<p class='error animated bounceIn'>\\nInvalid email or password.\\n<\\/p>"
)

ACCEPTED = 'window.location.href = "/en-ca/niv/groups/12345678";'


def _is_rejected(body: str) -> bool:
    low = body.lower()
    if any(m in low for m in LOGIN_ERROR_MARKERS):
        return True
    return "sign_in_form" in low and "error" in low


def test_rejected_login_is_detected():
    assert _is_rejected(REJECTED)


def test_accepted_login_is_not_flagged():
    assert not _is_rejected(ACCEPTED)


def test_locked_account_is_detected():
    assert _is_rejected("<p class='error'>Your account is locked.</p>")


@pytest.mark.parametrize("marker", CAPTCHA_MARKERS)
def test_captcha_markers_are_lowercase_for_matching(marker):
    assert marker == marker.lower()
