import stripe
import os
from datetime import datetime, timedelta
from functools import wraps
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, current_app, jsonify)
from flask_login import login_required, current_user
from . import db
from .models import Family

billing = Blueprint('billing', __name__)


# ── Helpers ────────────────────────────────────────────────────────────────

def _stripe():
    key = current_app.config.get('STRIPE_SECRET_KEY')
    if not key:
        return None
    stripe.api_key = key
    return stripe


def family_has_paid_access(family):
    """True if the family is on an active trial or paid plan."""
    if family.plan == 'paid':
        return True
    if family.plan == 'trial':
        return family.trial_ends_at and family.trial_ends_at > datetime.utcnow()
    if family.plan == 'past_due':
        # 7-day grace period from when subscription payment failed
        # stripe webhook sets trial_ends_at to the failure time
        grace = family.trial_ends_at
        return grace and (grace + timedelta(days=7)) > datetime.utcnow()
    return False


def trial_days_remaining(family):
    """Days left in trial, or None if not on trial."""
    if family.plan == 'trial' and family.trial_ends_at:
        delta = family.trial_ends_at - datetime.utcnow()
        return max(0, delta.days)
    return None


# ── Decorator ──────────────────────────────────────────────────────────────

def requires_plan(f):
    """Restrict a route to families with an active trial or paid plan."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for('main.login'))
        if not family_has_paid_access(current_user.family):
            flash('This feature requires a Family Plan. Upgrade to unlock it.', 'info')
            return redirect(url_for('billing.billing_page'))
        return f(*args, **kwargs)
    return decorated


# ── Routes ─────────────────────────────────────────────────────────────────

@billing.route('/billing')
@login_required
def billing_page():
    family = current_user.family
    days_left = trial_days_remaining(family)
    s = _stripe()
    invoices = []
    if s and family.stripe_customer_id:
        try:
            result = s.Invoice.list(customer=family.stripe_customer_id, limit=5)
            invoices = result.data
        except Exception:
            pass
    return render_template('billing.html', family=family,
                           days_left=days_left, invoices=invoices,
                           monthly_price_id=current_app.config.get('STRIPE_MONTHLY_PRICE_ID'),
                           annual_price_id=current_app.config.get('STRIPE_ANNUAL_PRICE_ID'))


@billing.route('/billing/checkout', methods=['POST'])
@login_required
def checkout():
    s = _stripe()
    if not s:
        flash('Billing is not configured yet.', 'error')
        return redirect(url_for('billing.billing_page'))

    price_id = request.form.get('price_id')
    if not price_id:
        flash('No plan selected.', 'error')
        return redirect(url_for('billing.billing_page'))

    family = current_user.family
    kwargs = dict(
        mode='subscription',
        line_items=[{'price': price_id, 'quantity': 1}],
        success_url=url_for('billing.checkout_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
        cancel_url=url_for('billing.billing_page', _external=True),
        client_reference_id=str(family.id),
        automatic_tax={'enabled': True},
        tax_id_collection={'enabled': True},
        metadata={'family_id': str(family.id), 'account_id': family.account_id or ''},
    )
    if family.stripe_customer_id:
        kwargs['customer'] = family.stripe_customer_id
    else:
        kwargs['customer_email'] = current_user.email

    try:
        session = s.checkout.Session.create(**kwargs)
        return redirect(session.url, code=303)
    except Exception as e:
        flash('Could not start checkout. Please try again.', 'error')
        current_app.logger.error(f'Stripe checkout error: {e}')
        return redirect(url_for('billing.billing_page'))


@billing.route('/billing/checkout/success')
@login_required
def checkout_success():
    flash('Welcome to the Family Plan! All features are now unlocked.', 'success')
    return redirect(url_for('billing.billing_page'))


@billing.route('/billing/portal', methods=['POST'])
@login_required
def customer_portal():
    s = _stripe()
    if not s or not current_user.family.stripe_customer_id:
        flash('No billing account found.', 'error')
        return redirect(url_for('billing.billing_page'))
    try:
        session = s.billing_portal.Session.create(
            customer=current_user.family.stripe_customer_id,
            return_url=url_for('billing.billing_page', _external=True),
        )
        return redirect(session.url, code=303)
    except Exception as e:
        flash('Could not open billing portal. Please try again.', 'error')
        current_app.logger.error(f'Stripe portal error: {e}')
        return redirect(url_for('billing.billing_page'))


@billing.route('/billing/webhook', methods=['POST'])
def webhook():
    s = _stripe()
    if not s:
        return jsonify({'error': 'not configured'}), 400

    payload = request.get_data()
    sig = request.headers.get('Stripe-Signature')
    secret = current_app.config.get('STRIPE_WEBHOOK_SECRET')

    try:
        event = s.Webhook.construct_event(payload, sig, secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        return jsonify({'error': 'invalid signature'}), 400

    _handle_event(event)
    return jsonify({'status': 'ok'}), 200


def _handle_event(event):
    data = event['data']['object']
    etype = event['type']

    if etype == 'checkout.session.completed':
        family_id = data.get('metadata', {}).get('family_id')
        family = family_id and Family.query.get(int(family_id))
        if family:
            family.stripe_customer_id = data.get('customer')
            family.stripe_subscription_id = data.get('subscription')
            family.plan = 'paid'
            family.trial_ends_at = None
            db.session.commit()

    elif etype == 'customer.subscription.updated':
        _sync_subscription(data)

    elif etype == 'customer.subscription.deleted':
        family = Family.query.filter_by(stripe_subscription_id=data['id']).first()
        if family:
            family.plan = 'free'
            family.stripe_subscription_id = None
            db.session.commit()

    elif etype == 'invoice.payment_failed':
        customer_id = data.get('customer')
        family = Family.query.filter_by(stripe_customer_id=customer_id).first()
        if family and family.plan == 'paid':
            family.plan = 'past_due'
            family.trial_ends_at = datetime.utcnow()  # grace period starts now
            db.session.commit()

    elif etype == 'invoice.payment_succeeded':
        customer_id = data.get('customer')
        family = Family.query.filter_by(stripe_customer_id=customer_id).first()
        if family and family.plan == 'past_due':
            family.plan = 'paid'
            family.trial_ends_at = None
            db.session.commit()


def _sync_subscription(sub):
    family = Family.query.filter_by(stripe_subscription_id=sub['id']).first()
    if not family:
        family = Family.query.filter_by(stripe_customer_id=sub['customer']).first()
    if not family:
        return
    status = sub.get('status')
    if status == 'active':
        family.plan = 'paid'
        family.trial_ends_at = None
    elif status in ('past_due', 'unpaid'):
        if family.plan == 'paid':
            family.plan = 'past_due'
            family.trial_ends_at = datetime.utcnow()
    elif status in ('canceled', 'incomplete_expired'):
        family.plan = 'free'
        family.stripe_subscription_id = None
    db.session.commit()
