"""
Tests for Stripe webhook handler (_handle_event) and billing access gates.

These tests call _handle_event directly with synthetic event dicts rather than
going through the HTTP webhook endpoint, avoiding the need to mock Stripe's
HMAC signature verification.
"""
import pytest
from datetime import datetime, timedelta
from app import db
from app.models import Family, User
from app.billing import _handle_event, family_has_paid_access, trial_days_remaining


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def paid_family(app):
    with app.app_context():
        f = Family(
            name='Paid Family',
            plan='paid',
            stripe_customer_id='cus_test123',
            stripe_subscription_id='sub_test123',
        )
        db.session.add(f)
        db.session.commit()
        yield f.id


@pytest.fixture()
def trial_family(app):
    with app.app_context():
        f = Family(
            name='Trial Family',
            plan='trial',
            trial_ends_at=datetime.utcnow() + timedelta(days=10),
        )
        db.session.add(f)
        db.session.commit()
        yield f.id


# ── family_has_paid_access ────────────────────────────────────────────────────

def test_paid_plan_has_access(app, paid_family):
    with app.app_context():
        f = db.session.get(Family, paid_family)
        assert family_has_paid_access(f) is True


def test_active_trial_has_access(app, trial_family):
    with app.app_context():
        f = db.session.get(Family, trial_family)
        assert family_has_paid_access(f) is True


def test_expired_trial_no_access(app):
    with app.app_context():
        f = Family(name='Expired', plan='trial', trial_ends_at=datetime.utcnow() - timedelta(days=1))
        db.session.add(f)
        db.session.commit()
        assert family_has_paid_access(f) is False


def test_free_plan_no_access(app):
    with app.app_context():
        f = Family(name='Free', plan='free')
        db.session.add(f)
        db.session.commit()
        assert family_has_paid_access(f) is False


def test_past_due_within_grace_has_access(app):
    with app.app_context():
        f = Family(name='GracePod', plan='past_due', trial_ends_at=datetime.utcnow() - timedelta(days=3))
        db.session.add(f)
        db.session.commit()
        assert family_has_paid_access(f) is True


def test_past_due_beyond_grace_no_access(app):
    with app.app_context():
        f = Family(name='LapsedPod', plan='past_due', trial_ends_at=datetime.utcnow() - timedelta(days=8))
        db.session.add(f)
        db.session.commit()
        assert family_has_paid_access(f) is False


def test_trial_days_remaining(app, trial_family):
    with app.app_context():
        f = db.session.get(Family, trial_family)
        days = trial_days_remaining(f)
        assert days is not None
        assert 9 <= days <= 10


# ── Webhook: checkout.session.completed ──────────────────────────────────────

def test_checkout_completed_sets_paid(app, trial_family):
    with app.app_context():
        f = db.session.get(Family, trial_family)
        event = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'metadata': {'family_id': str(f.id)},
                'customer': 'cus_new',
                'subscription': 'sub_new',
            }},
        }
        _handle_event(event)
        db.session.refresh(f)
        assert f.plan == 'paid'
        assert f.stripe_customer_id == 'cus_new'
        assert f.stripe_subscription_id == 'sub_new'
        assert f.trial_ends_at is None
        assert f.email_trial_warning_sent is True
        assert f.email_trial_ended_sent is True


def test_checkout_completed_unknown_family_is_noop(app):
    with app.app_context():
        event = {
            'type': 'checkout.session.completed',
            'data': {'object': {
                'metadata': {'family_id': '999999'},
                'customer': 'cus_x',
                'subscription': 'sub_x',
            }},
        }
        _handle_event(event)  # should not raise


# ── Webhook: invoice.payment_failed ──────────────────────────────────────────

def test_payment_failed_sets_past_due(app, paid_family):
    with app.app_context():
        f = db.session.get(Family, paid_family)
        event = {
            'type': 'invoice.payment_failed',
            'data': {'object': {'customer': 'cus_test123'}},
        }
        _handle_event(event)
        db.session.refresh(f)
        assert f.plan == 'past_due'
        assert f.trial_ends_at is not None


def test_payment_failed_ignores_non_paid_family(app, trial_family):
    with app.app_context():
        f = db.session.get(Family, trial_family)
        f.stripe_customer_id = 'cus_trial'
        db.session.commit()
        event = {
            'type': 'invoice.payment_failed',
            'data': {'object': {'customer': 'cus_trial'}},
        }
        _handle_event(event)
        db.session.refresh(f)
        assert f.plan == 'trial'  # unchanged — only paid→past_due


# ── Webhook: invoice.payment_succeeded ───────────────────────────────────────

def test_payment_succeeded_restores_paid(app):
    with app.app_context():
        f = Family(
            name='RecoveredPod',
            plan='past_due',
            stripe_customer_id='cus_recover',
            trial_ends_at=datetime.utcnow() - timedelta(days=2),
        )
        db.session.add(f)
        db.session.commit()
        event = {
            'type': 'invoice.payment_succeeded',
            'data': {'object': {'customer': 'cus_recover'}},
        }
        _handle_event(event)
        db.session.refresh(f)
        assert f.plan == 'paid'
        assert f.trial_ends_at is None


# ── Webhook: customer.subscription.deleted ───────────────────────────────────

def test_subscription_deleted_sets_free(app, paid_family):
    with app.app_context():
        f = db.session.get(Family, paid_family)
        event = {
            'type': 'customer.subscription.deleted',
            'data': {'object': {'id': 'sub_test123'}},
        }
        _handle_event(event)
        db.session.refresh(f)
        assert f.plan == 'free'
        assert f.stripe_subscription_id is None


# ── Webhook: customer.subscription.updated ───────────────────────────────────

def test_subscription_updated_active(app, paid_family):
    with app.app_context():
        f = db.session.get(Family, paid_family)
        f.plan = 'past_due'
        db.session.commit()
        event = {
            'type': 'customer.subscription.updated',
            'data': {'object': {'id': 'sub_test123', 'customer': 'cus_test123', 'status': 'active'}},
        }
        _handle_event(event)
        db.session.refresh(f)
        assert f.plan == 'paid'
        assert f.trial_ends_at is None


def test_subscription_updated_cancelled(app, paid_family):
    with app.app_context():
        f = db.session.get(Family, paid_family)
        event = {
            'type': 'customer.subscription.updated',
            'data': {'object': {'id': 'sub_test123', 'customer': 'cus_test123', 'status': 'canceled'}},
        }
        _handle_event(event)
        db.session.refresh(f)
        assert f.plan == 'free'
        assert f.stripe_subscription_id is None
