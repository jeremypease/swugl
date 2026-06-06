import stripe
import math
import os
from datetime import datetime, timedelta
from functools import wraps
from flask import (Blueprint, render_template, redirect, url_for, flash,
                   request, current_app, jsonify)
from flask_login import login_required, current_user
from . import db
from .models import Family, FamilyPayoutAccount

PLATFORM_FEE_RATE = 0.02  # 2% Swugl platform fee on event payouts

FREE_MEMBER_LIMIT = 25   # max Person records on free plan
FREE_EVENT_LIMIT = 3     # max upcoming events on free plan

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
        # trial_ends_at is reused here as the grace-period start timestamp:
        # the payment_failed webhook sets it to utcnow() and we allow 7 days.
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

@billing.route('/billing/connect', methods=['POST'])
@login_required
def connect_start():
    """Create a Stripe Express account for this family and redirect to onboarding."""
    s = _stripe()
    if not s:
        flash('Stripe is not configured.', 'error')
        return redirect(url_for('billing.billing_page'))

    family = current_user.family
    payout_account = family.payout_account

    if not payout_account:
        try:
            acct = s.Account.create(
                type='express',
                country='US',
                email=current_user.email,
                capabilities={'transfers': {'requested': True}},
                metadata={'family_id': str(family.id)},
            )
            payout_account = FamilyPayoutAccount(
                family_id=family.id,
                stripe_account_id=acct.id,
            )
            db.session.add(payout_account)
            db.session.commit()
        except Exception as e:
            flash('Could not create payout account. Please try again.', 'error')
            current_app.logger.error(f'Stripe Connect account create error: {e}')
            return redirect(url_for('billing.billing_page'))

    try:
        link = s.AccountLink.create(
            account=payout_account.stripe_account_id,
            refresh_url=url_for('billing.connect_refresh', _external=True),
            return_url=url_for('billing.connect_return', _external=True),
            type='account_onboarding',
        )
        return redirect(link.url, code=303)
    except Exception as e:
        flash('Could not start payout account setup. Please try again.', 'error')
        current_app.logger.error(f'Stripe AccountLink error: {e}')
        return redirect(url_for('billing.billing_page'))


@billing.route('/billing/connect/return')
@login_required
def connect_return():
    """Stripe redirects here after the admin completes (or skips) onboarding."""
    s = _stripe()
    family = current_user.family
    payout_account = family.payout_account
    if s and payout_account:
        try:
            acct = s.Account.retrieve(payout_account.stripe_account_id)
            if acct.details_submitted:
                payout_account.onboarding_complete = True
                db.session.commit()
                flash('Payout account connected. You can now withdraw event collections to your bank.', 'success')
            else:
                flash('Setup isn\'t complete yet — finish connecting your bank to enable payouts.', 'info')
        except Exception:
            pass
    return redirect(url_for('billing.billing_page'))


@billing.route('/billing/connect/refresh')
@login_required
def connect_refresh():
    """Stripe redirects here when the onboarding link has expired — regenerate it."""
    s = _stripe()
    family = current_user.family
    payout_account = family.payout_account
    if not s or not payout_account:
        flash('No payout account found. Please start setup again.', 'error')
        return redirect(url_for('billing.billing_page'))
    try:
        link = s.AccountLink.create(
            account=payout_account.stripe_account_id,
            refresh_url=url_for('billing.connect_refresh', _external=True),
            return_url=url_for('billing.connect_return', _external=True),
            type='account_onboarding',
        )
        return redirect(link.url, code=303)
    except Exception as e:
        flash('Could not refresh setup link. Please try again.', 'error')
        current_app.logger.error(f'Stripe AccountLink refresh error: {e}')
        return redirect(url_for('billing.billing_page'))


@billing.route('/events/<int:event_id>/payment/payout', methods=['POST'])
@login_required
def event_payout(event_id):
    """Transfer collected event payments to the family's connected Stripe account."""
    from .models import Event, EventPaymentConfig, EventPaymentRecord
    s = _stripe()
    if not s:
        flash('Stripe is not configured.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    event = db.session.get(Event, event_id)
    if not event or event.family_id != current_user.active_family_id:
        flash('Event not found.', 'error')
        return redirect(url_for('main.events_list'))

    if not current_user.active_is_admin:
        flash('Only admins can request payouts.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    config = event.payment_config
    if not config:
        flash('No payment configuration for this event.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    if config.payout_transfer_id:
        flash('Payout has already been requested for this event.', 'info')
        return redirect(url_for('main.event_detail', event_id=event_id))

    payout_account = current_user.family.payout_account
    if not payout_account or not payout_account.onboarding_complete:
        flash('Connect your bank account on the Billing page before requesting a payout.', 'info')
        return redirect(url_for('billing.billing_page'))

    paid_records = EventPaymentRecord.query.filter_by(
        event_id=event.id, status='paid'
    ).all()
    if not paid_records:
        flash('No payments have been collected yet.', 'info')
        return redirect(url_for('main.event_detail', event_id=event_id))

    # Use exact net amounts where available; fall back to gross estimate for records
    # where net_cents wasn't captured (e.g., old records before this feature).
    total_net_cents = 0
    for r in paid_records:
        if r.net_cents is not None:
            total_net_cents += r.net_cents
        else:
            # Estimate: gross minus Stripe's ~2.9% + $0.30 per transaction
            total_net_cents += r.amount_cents - math.ceil(r.amount_cents * 0.029) - 30

    swugl_fee_cents = math.ceil(total_net_cents * PLATFORM_FEE_RATE)
    transfer_amount = total_net_cents - swugl_fee_cents

    if transfer_amount <= 0:
        flash('Collected amount is too small to transfer after fees.', 'error')
        return redirect(url_for('main.event_detail', event_id=event_id))

    try:
        transfer = s.Transfer.create(
            amount=transfer_amount,
            currency='usd',
            destination=payout_account.stripe_account_id,
            transfer_group=f'event_{event.id}',
            description=f'{event.name} payout — {event.family.name}',
            metadata={
                'event_id': str(event.id),
                'family_id': str(event.family_id),
                'gross_cents': str(sum(r.amount_cents for r in paid_records)),
                'net_cents': str(total_net_cents),
                'swugl_fee_cents': str(swugl_fee_cents),
            },
        )
        config.payout_transfer_id = transfer.id
        config.payout_at = datetime.utcnow()
        db.session.commit()
        flash(f'Payout of ${transfer_amount / 100:.2f} initiated. Funds arrive in 1–2 business days.', 'success')
    except stripe.error.StripeError as e:
        flash(f'Payout failed: {e.user_message or str(e)}', 'error')
        current_app.logger.error(f'Stripe Transfer error for event {event_id}: {e}')

    return redirect(url_for('main.event_detail', event_id=event_id))


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
                           annual_price_id=current_app.config.get('STRIPE_ANNUAL_PRICE_ID'),
                           payout_account=family.payout_account)


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
    from .models import EventPaymentRecord
    data = event['data']['object']
    etype = event['type']

    if etype == 'checkout.session.completed':
        metadata = data.get('metadata', {})
        # Event payment (one-time) — distinguished by payment_type metadata
        if metadata.get('payment_type') == 'event':
            session_id = data.get('id')
            record = EventPaymentRecord.query.filter_by(
                stripe_checkout_session_id=session_id
            ).first()
            if record and record.status != 'paid':
                record.status = 'paid'
                record.paid_at = datetime.utcnow()
                payment_intent_id = data.get('payment_intent')
                if payment_intent_id:
                    record.stripe_payment_intent_id = payment_intent_id
                    # Retrieve net amount (after Stripe fees) from the balance transaction
                    s_api = _stripe()
                    if s_api:
                        try:
                            pi = s_api.PaymentIntent.retrieve(
                                payment_intent_id,
                                expand=['latest_charge.balance_transaction'],
                            )
                            bt = (pi.latest_charge or {}).get('balance_transaction') if isinstance(pi.latest_charge, dict) else getattr(getattr(pi, 'latest_charge', None), 'balance_transaction', None)
                            if bt and hasattr(bt, 'net'):
                                record.net_cents = bt.net
                        except Exception:
                            pass
                db.session.commit()
            return

        family_id = metadata.get('family_id')
        family = family_id and Family.query.get(int(family_id))
        if family:
            family.stripe_customer_id = data.get('customer')
            family.stripe_subscription_id = data.get('subscription')
            family.plan = 'paid'
            family.trial_ends_at = None
            # Suppress remaining trial lifecycle emails now that they've upgraded
            family.email_trial_warning_sent = True
            family.email_trial_ended_sent = True
            db.session.commit()

    elif etype == 'customer.subscription.updated':
        _sync_subscription(data)

    elif etype == 'customer.subscription.deleted':
        family = Family.query.filter_by(stripe_subscription_id=data['id']).first()
        if family:
            family.plan = 'free'
            family.stripe_subscription_id = None
            db.session.commit()
            _send_billing_email(family, 'cancelled')

    elif etype == 'invoice.payment_failed':
        customer_id = data.get('customer')
        family = Family.query.filter_by(stripe_customer_id=customer_id).first()
        if family and family.plan == 'paid':
            family.plan = 'past_due'
            family.trial_ends_at = datetime.utcnow()  # grace period starts now
            db.session.commit()
            _send_billing_email(family, 'payment_failed')

    elif etype == 'invoice.payment_succeeded':
        customer_id = data.get('customer')
        family = Family.query.filter_by(stripe_customer_id=customer_id).first()
        if family and family.plan == 'past_due':
            family.plan = 'paid'
            family.trial_ends_at = None
            db.session.commit()

    elif etype == 'account.updated':
        account_id = data.get('id')
        payout_account = FamilyPayoutAccount.query.filter_by(
            stripe_account_id=account_id
        ).first()
        if payout_account and data.get('details_submitted'):
            payout_account.onboarding_complete = True
            db.session.commit()


def _send_billing_email(family, event_type):
    """Send a billing lifecycle email to the family's admin user."""
    from .models import User
    from .email import send_payment_failed_email, send_subscription_cancelled_email
    admin = User.query.filter_by(family_id=family.id, is_admin=True, status='approved').first()
    if not admin or not current_app.config.get('MAIL_ENABLED'):
        return
    billing_url = 'https://swugl.com/billing'
    name = admin.first_name or admin.email
    if event_type == 'payment_failed':
        send_payment_failed_email(admin.email, name, billing_url)
    elif event_type == 'cancelled':
        send_subscription_cancelled_email(admin.email, name, billing_url)


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
