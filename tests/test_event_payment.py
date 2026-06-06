"""
Tests for event payment collection: config setup, webhook handling,
and the billing._handle_event path for event payments.
"""
import pytest
from datetime import datetime
from app import db
from app.models import Family, User, Event, EventPaymentConfig, EventPaymentRecord
from app.billing import _handle_event


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def event_with_payment(app):
    with app.app_context():
        family = Family.query.first()
        e = Event(
            family_id=family.id,
            name='Test Retreat',
            start_date=datetime(2026, 8, 1).date(),
            end_date=datetime(2026, 8, 3).date(),
        )
        db.session.add(e)
        db.session.flush()
        config = EventPaymentConfig(
            event_id=e.id,
            amount_cents=5000,
            charge_type='per_family',
            description='Cabin fee',
        )
        db.session.add(config)
        db.session.commit()
        yield e.id


# ── EventPaymentConfig model ──────────────────────────────────────────────────

def test_payment_config_amount_dollars(app, event_with_payment):
    with app.app_context():
        e = db.session.get(Event, event_with_payment)
        assert e.payment_config is not None
        assert e.payment_config.amount_dollars == 50.0


def test_payment_config_defaults(app, event_with_payment):
    with app.app_context():
        e = db.session.get(Event, event_with_payment)
        assert e.payment_config.is_active is True
        assert e.payment_config.charge_type == 'per_family'


# ── Route: POST /events/<id>/payment/setup ────────────────────────────────────

def test_payment_setup_creates_config(app, auth_client):
    with app.app_context():
        family = Family.query.first()
        e = Event(
            family_id=family.id,
            name='Setup Test Event',
            start_date=datetime(2026, 9, 1).date(),
            end_date=datetime(2026, 9, 2).date(),
        )
        db.session.add(e)
        db.session.commit()
        event_id = e.id

    r = auth_client.post(f'/events/{event_id}/payment/setup', data={
        'amount_dollars': '30.00',
        'charge_type': 'per_family',
        'description': 'Camping fee',
        'deadline': '',
    }, follow_redirects=True)
    assert r.status_code == 200

    with app.app_context():
        config = EventPaymentConfig.query.filter_by(event_id=event_id).first()
        assert config is not None
        assert config.amount_cents == 3000
        assert config.charge_type == 'per_family'
        assert config.description == 'Camping fee'
        assert config.is_active is True


def test_payment_setup_rejects_too_small(app, auth_client):
    with app.app_context():
        family = Family.query.first()
        e = Event(
            family_id=family.id,
            name='Small Amount Event',
            start_date=datetime(2026, 9, 5).date(),
            end_date=datetime(2026, 9, 6).date(),
        )
        db.session.add(e)
        db.session.commit()
        event_id = e.id

    r = auth_client.post(f'/events/{event_id}/payment/setup', data={
        'amount_dollars': '0.25',
        'charge_type': 'per_family',
    }, follow_redirects=True)
    # Should flash an error and not create config
    assert r.status_code == 200
    with app.app_context():
        config = EventPaymentConfig.query.filter_by(event_id=event_id).first()
        assert config is None


def test_payment_setup_updates_existing(app, auth_client, event_with_payment):
    r = auth_client.post(f'/events/{event_with_payment}/payment/setup', data={
        'amount_dollars': '75.00',
        'charge_type': 'per_person',
        'description': 'Updated fee',
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        config = EventPaymentConfig.query.filter_by(event_id=event_with_payment).first()
        assert config.amount_cents == 7500
        assert config.charge_type == 'per_person'


# ── Route: POST /events/<id>/payment/disable ─────────────────────────────────

def test_payment_disable(app, auth_client, event_with_payment):
    r = auth_client.post(f'/events/{event_with_payment}/payment/disable',
                         follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        config = EventPaymentConfig.query.filter_by(event_id=event_with_payment).first()
        assert config.is_active is False


def test_payment_setup_requires_auth(client, event_with_payment):
    r = client.post(f'/events/{event_with_payment}/payment/setup', data={
        'amount_dollars': '20.00',
        'charge_type': 'per_family',
    })
    assert r.status_code == 302
    assert '/login' in r.headers['Location']


# ── Webhook: checkout.session.completed (event payment) ───────────────────────

def test_event_payment_webhook_marks_paid(app, event_with_payment):
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        e = db.session.get(Event, event_with_payment)
        record = EventPaymentRecord(
            event_id=e.id,
            payer_user_id=user.id,
            amount_cents=5000,
            stripe_checkout_session_id='cs_test_evt_001',
            status='pending',
        )
        db.session.add(record)
        db.session.commit()

        webhook_event = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'id': 'cs_test_evt_001',
                'metadata': {
                    'payment_type': 'event',
                    'event_id': str(e.id),
                    'payer_user_id': str(user.id),
                    'family_id': str(e.family_id),
                },
                'customer': 'cus_x',
                'subscription': None,
            }},
        }
        _handle_event(webhook_event)
        db.session.refresh(record)
        assert record.status == 'paid'
        assert record.paid_at is not None


def test_event_payment_webhook_does_not_activate_subscription(app, event_with_payment):
    """Event payment checkout must NOT upgrade the family's subscription plan."""
    with app.app_context():
        family = Family.query.first()
        original_plan = family.plan
        user = User.query.filter_by(email='admin@pease-family.com').first()
        e = db.session.get(Event, event_with_payment)

        record = EventPaymentRecord(
            event_id=e.id,
            payer_user_id=user.id,
            amount_cents=5000,
            stripe_checkout_session_id='cs_test_evt_002',
            status='pending',
        )
        db.session.add(record)
        db.session.commit()

        webhook_event = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'id': 'cs_test_evt_002',
                'metadata': {
                    'payment_type': 'event',
                    'event_id': str(e.id),
                    'payer_user_id': str(user.id),
                    'family_id': str(family.id),
                },
                'customer': 'cus_x',
                'subscription': None,
            }},
        }
        _handle_event(webhook_event)
        db.session.refresh(family)
        assert family.plan == original_plan


def test_event_payment_webhook_idempotent(app, event_with_payment):
    """Firing the webhook twice should not change status after first paid mark."""
    with app.app_context():
        user = User.query.filter_by(email='admin@pease-family.com').first()
        e = db.session.get(Event, event_with_payment)
        record = EventPaymentRecord(
            event_id=e.id,
            payer_user_id=user.id,
            amount_cents=5000,
            stripe_checkout_session_id='cs_test_evt_003',
            status='pending',
        )
        db.session.add(record)
        db.session.commit()

        evt = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'id': 'cs_test_evt_003',
                'metadata': {'payment_type': 'event'},
                'customer': 'cus_x',
                'subscription': None,
            }},
        }
        _handle_event(evt)
        _handle_event(evt)  # second fire
        db.session.refresh(record)
        assert record.status == 'paid'


def test_event_payment_webhook_unknown_session_is_noop(app):
    with app.app_context():
        evt = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'id': 'cs_does_not_exist',
                'metadata': {'payment_type': 'event'},
                'customer': 'cus_x',
                'subscription': None,
            }},
        }
        _handle_event(evt)  # should not raise
