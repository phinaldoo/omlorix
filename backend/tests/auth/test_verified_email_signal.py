import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.auth import utils as auth_utils


def test_verified_email_signal_requires_positive_value():
    assert auth_utils._has_positive_verified_email_signal(True) is True
    assert auth_utils._has_positive_verified_email_signal("true") is True
    assert auth_utils._has_positive_verified_email_signal("1") is True
    assert auth_utils._has_positive_verified_email_signal("yes") is True

    assert auth_utils._has_positive_verified_email_signal(False) is False
    assert auth_utils._has_positive_verified_email_signal("false") is False
    assert auth_utils._has_positive_verified_email_signal(None) is False
    assert auth_utils._has_positive_verified_email_signal("") is False
