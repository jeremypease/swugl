"""Flask CLI commands for scheduled/background tasks.

Railway cron: set the cron job command to:
    flask email-sequence
and schedule it to run daily (e.g. 0 8 * * *  UTC).
"""
import click
from datetime import datetime, timedelta
from flask import current_app, url_for
from flask.cli import with_appcontext

from . import db
from .models import Family, User
from .notifications import send_family_digest
from .email import (
    send_nudge_day3_email,
    send_nudge_day7_email,
    send_trial_warning_email,
    send_trial_ended_email,
)


def _admin_for(family):
    """Return the first verified admin user for a family, or None."""
    return (
        User.query
        .filter_by(family_id=family.id, is_admin=True, email_verified=True, status='approved')
        .first()
    )


def _member_count(family):
    return User.query.filter_by(family_id=family.id, status='approved').count()


@click.command('email-sequence')
@click.option('--dry-run', is_flag=True, help='Print what would be sent without sending.')
@with_appcontext
def email_sequence(dry_run):
    """Send onboarding and trial lifecycle emails to families that need them."""
    if not current_app.config.get('MAIL_ENABLED') and not dry_run:
        click.echo('MAIL_ENABLED is not set — skipping. Pass --dry-run to preview.')
        return

    now = datetime.utcnow()
    families = Family.query.all()
    sent = skipped = 0

    for family in families:
        admin = _admin_for(family)
        if not admin:
            continue

        days_old = (now - family.created_at).days if family.created_at else 999

        # ── Day-3 nudge: send if pod is 3+ days old and has only the admin ──
        if not family.email_nudge3_sent and days_old >= 3:
            if _member_count(family) <= 1:
                members_url = url_for('main.members', _external=True)
                if dry_run:
                    click.echo(f'[DRY RUN] nudge-day3 → {admin.email} ({family.name})')
                else:
                    send_nudge_day3_email(admin, family, members_url)
                    sent += 1
            family.email_nudge3_sent = True

        # ── Day-7 feature highlight: send if pod is 7+ days old ──
        if not family.email_nudge7_sent and days_old >= 7:
            dashboard_url = url_for('main.home', _external=True)
            if dry_run:
                click.echo(f'[DRY RUN] nudge-day7 → {admin.email} ({family.name})')
            else:
                send_nudge_day7_email(admin, family, dashboard_url)
                sent += 1
            family.email_nudge7_sent = True

        # ── Trial warning: send when ≤5 days remain in the trial ──
        if not family.email_trial_warning_sent and family.plan == 'trial' and family.trial_ends_at:
            days_left = (family.trial_ends_at - now).days
            if 0 <= days_left <= 5:
                billing_url = url_for('billing.billing_page', _external=True)
                if dry_run:
                    click.echo(f'[DRY RUN] trial-warning ({days_left}d left) → {admin.email} ({family.name})')
                else:
                    send_trial_warning_email(admin, family, days_left, billing_url)
                    sent += 1
                family.email_trial_warning_sent = True

        # ── Trial ended: send once when trial has expired ──
        if not family.email_trial_ended_sent and family.plan == 'trial' and family.trial_ends_at:
            if family.trial_ends_at < now:
                billing_url = url_for('billing.billing_page', _external=True)
                if dry_run:
                    click.echo(f'[DRY RUN] trial-ended → {admin.email} ({family.name})')
                else:
                    send_trial_ended_email(admin, family, billing_url)
                    sent += 1
                family.email_trial_ended_sent = True

    if not dry_run:
        db.session.commit()
        click.echo(f'email-sequence done: {sent} sent, {len(families) - sent} skipped.')
    else:
        click.echo(f'[DRY RUN] would affect {sent} families out of {len(families)}.')


@click.command('digest')
@click.option('--dry-run', is_flag=True, help='Print which families would receive a digest without sending.')
@with_appcontext
def digest(dry_run):
    """Send the weekly digest email to opted-in members of every family."""
    if not current_app.config.get('MAIL_ENABLED') and not dry_run:
        click.echo('MAIL_ENABLED is not set — skipping. Pass --dry-run to preview.')
        return

    families = Family.query.all()
    total_sent = 0
    for family in families:
        if dry_run:
            from .notifications import compute_digest
            content = compute_digest(family)
            if content:
                member_count = User.query.filter_by(
                    family_id=family.id, status='approved'
                ).count()
                click.echo(f'[DRY RUN] {family.name} — would notify up to {member_count} member(s)')
            else:
                click.echo(f'[DRY RUN] {family.name} — nothing to send')
        else:
            sent = send_family_digest(family)
            total_sent += sent
            if sent:
                click.echo(f'{family.name}: {sent} sent')

    if not dry_run:
        click.echo(f'digest done: {total_sent} email(s) sent across {len(families)} family/families.')
