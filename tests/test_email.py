"""
Tests for the From-header guard in app/email.py.

A malformed RESEND_FROM_EMAIL makes Resend reject *every* send — this is the
bug that silently took down all email for ~3 weeks. _from() must fall back to
a known-good default rather than pass garbage to Resend.
"""
import pytest
from app.email import _from, _valid_from, DEFAULT_FROM


@pytest.mark.parametrize('configured,expected', [
    ('Swugl <noreply@swugl.com>', 'Swugl <noreply@swugl.com>'),   # canonical
    ('noreply@swugl.com', 'noreply@swugl.com'),                    # bare address
    ('  Swugl <noreply@swugl.com>\n', 'Swugl <noreply@swugl.com>'),  # trimmed
    ('"Swugl <noreply@swugl.com>"', DEFAULT_FROM),  # stray quotes → fallback
    ('Swugl', DEFAULT_FROM),                         # bare name, no address
    ('noreply@ swugl.com', DEFAULT_FROM),            # space in address
    ('', DEFAULT_FROM),
    (None, DEFAULT_FROM),
])
def test_from_falls_back_when_malformed(app, configured, expected):
    with app.app_context():
        app.config['RESEND_FROM_EMAIL'] = configured
        assert _from() == expected


def test_valid_from_accepts_both_formats():
    assert _valid_from('a@b.com')
    assert _valid_from('Name <a@b.com>')
    assert not _valid_from('"quoted" garbage')
    assert not _valid_from('no-at-sign')
