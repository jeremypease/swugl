"""Flask CLI commands for scheduled/background tasks.

Railway cron jobs:
    flask email-sequence   — daily  (0 8 * * *)
    flask digest           — weekly (0 8 * * 1)
    flask rsvp-reminders   — daily  (0 8 * * *)
    flask annual-events    — weekly (0 9 * * 1)
    flask story-prompts    — weekly (0 9 * * 1)
"""
import click
from datetime import datetime, timedelta, date
from flask import current_app, url_for
from flask.cli import with_appcontext

from . import db
from .models import (Family, User, Event, EventMeal, EventMealItem, EventAssignment,
                     EventSleepingSpot, EventRSVP, NotificationPreference,
                     Person, SpouseRelationship, ParentRelationship,
                     PollVote, CardSignature, AnnouncementReaction,
                     PhotoTag, CarpoolOffer, EventSurveyResponse)
from .notifications import send_family_digest, create_notification
from .email import (
    send_nudge_day3_email,
    send_nudge_day7_email,
    send_trial_warning_email,
    send_trial_ended_email,
    send_rsvp_reminder_email,
    send_annual_event_cloned_email,
)


def _request_ctx():
    """Push a minimal request context so url_for(_external=True) works in CLI commands."""
    base_url = current_app.config.get('BASE_URL', 'https://swugl.com')
    return current_app.test_request_context('/', base_url=base_url)


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

    with _request_ctx():
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
    with _request_ctx():
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


@click.command('story-prompts')
@click.option('--dry-run', is_flag=True, help='Print who would be prompted without generating or sending.')
@with_appcontext
def story_prompts(dry_run):
    """Send weekly Family Stories prompts to opted-in members of paid families."""
    if not current_app.config.get('MAIL_ENABLED') and not dry_run:
        click.echo('MAIL_ENABLED is not set — skipping. Pass --dry-run to preview.')
        return
    from .models import StoryPrompt
    from .billing import family_has_paid_access
    from .ai import generate_story_prompt
    from .email import send_story_prompt_email

    now = datetime.utcnow()
    total = 0
    with _request_ctx():
        for family in Family.query.all():
            if not family.enable_stories or not family_has_paid_access(family):
                continue
            participants = Person.query.filter_by(family_id=family.id, stories_enabled=True).all()
            for person in participants:
                # Pace once a week, and never stack an unanswered prompt.
                if person.story_last_prompted_at and (now - person.story_last_prompted_at).days < 6:
                    continue
                if StoryPrompt.query.filter_by(family_id=family.id, person_id=person.id, answered_at=None).first():
                    continue
                if dry_run:
                    click.echo(f'[DRY RUN] {family.name}: would prompt {person.get_display_name()}')
                    total += 1
                    continue
                recent = [r.question for r in StoryPrompt.query
                          .filter_by(family_id=family.id, person_id=person.id)
                          .order_by(StoryPrompt.created_at.desc()).limit(10)]
                question = generate_story_prompt(person, recent_questions=recent)
                if not question:
                    continue  # AI unavailable — skip this run
                prompt = StoryPrompt(family_id=family.id, person_id=person.id,
                                     question=question.strip(), source='auto')
                person.story_last_prompted_at = now
                db.session.add(prompt)
                db.session.commit()
                answer_url = url_for('main.story_detail', prompt_id=prompt.id, _external=True)
                if person.user:
                    send_story_prompt_email(person.user, person, question, answer_url)
                    create_notification(person.user, 'story_prompt',
                                        title='You have a new family story prompt',
                                        body=question[:120], url=answer_url)
                else:
                    # Account-less elder — ask admins/contributors to help capture it.
                    helpers = User.query.filter(
                        User.family_id == family.id, User.status == 'approved',
                        db.or_(User.is_admin.is_(True), User.is_delegate.is_(True)),
                    ).all()
                    for h in helpers:
                        create_notification(h, 'story_prompt',
                                            title=f'Help {person.get_display_name()} share a story',
                                            body=question[:120], url=answer_url)
                total += 1
                click.echo(f'{family.name}: prompted {person.get_display_name()}')
    click.echo(f'story-prompts done: {total} prompt(s){" (dry run)" if dry_run else ""}.')


@click.command('rsvp-reminders')
@click.option('--dry-run', is_flag=True, help='Print what would be sent without sending.')
@with_appcontext
def rsvp_reminders(dry_run):
    """Send RSVP reminders for events whose deadline is 3 days away."""
    if not current_app.config.get('MAIL_ENABLED') and not dry_run:
        click.echo('MAIL_ENABLED is not set — skipping. Pass --dry-run to preview.')
        return

    target_date = date.today() + timedelta(days=3)
    events = Event.query.filter_by(rsvp_deadline=target_date).all()
    sent = 0

    with _request_ctx():
        for event in events:
            responded_person_ids = {
                r.person_id for r in EventRSVP.query.filter_by(event_id=event.id).all()
            }
            users = User.query.filter_by(
                family_id=event.family_id, status='approved', email_verified=True
            ).all()

            for user in users:
                if not user.person_id or user.person_id in responded_person_ids:
                    continue
                if not NotificationPreference.is_enabled(user.id, 'rsvp_reminder'):
                    continue
                event_url = url_for('main.event_detail', event_id=event.id, _external=True)
                if dry_run:
                    click.echo(f'[DRY RUN] rsvp-reminder → {user.email} for "{event.name}" (deadline {target_date})')
                else:
                    send_rsvp_reminder_email(user, event, event_url)
                    deadline_str = target_date.strftime('%B %-d')
                    create_notification(user, 'rsvp_reminder',
                                        title=f'RSVP reminder: {event.name}',
                                        body=f'Deadline: {deadline_str}',
                                        url=event_url)
                    sent += 1

    if not dry_run:
        click.echo(f'rsvp-reminders done: {sent} sent for {len(events)} event(s) with deadline {target_date}.')


def _advance_year(d):
    """Advance a date by exactly one year, mapping Feb 29 → Mar 1 on non-leap years."""
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, month=3, day=1)


def _clone_event(source):
    """Create a new Event one year ahead of source, copying structure but clearing signups."""
    new = Event(
        family_id=source.family_id,
        name=source.name,
        description=source.description,
        location=source.location,
        kind=source.kind,
        start_date=_advance_year(source.start_date),
        end_date=_advance_year(source.end_date) if source.end_date else None,
        is_annual=True,
        has_meals=source.has_meals,
        has_assignments=source.has_assignments,
        has_sleeping=source.has_sleeping,
    )
    db.session.add(new)
    db.session.flush()  # get new.id

    for meal in source.meals:
        new_meal = EventMeal(
            event_id=new.id,
            name=meal.name,
            meal_date=_advance_year(meal.meal_date) if meal.meal_date else None,
            meal_time=meal.meal_time,
            notes=meal.notes,
        )
        db.session.add(new_meal)
        db.session.flush()
        for item in meal.items:
            db.session.add(EventMealItem(
                meal_id=new_meal.id,
                label=item.label,
                quantity=item.quantity,
                is_cleanup=item.is_cleanup,
            ))

    for a in source.assignments:
        db.session.add(EventAssignment(
            event_id=new.id,
            title=a.title,
            description=a.description,
            category=a.category,
            due_date=_advance_year(a.due_date) if a.due_date else None,
        ))

    for spot in source.sleeping_spots:
        db.session.add(EventSleepingSpot(
            event_id=new.id,
            name=spot.name,
            capacity=spot.capacity,
            notes=spot.notes,
        ))

    return new


@click.command('annual-events')
@click.option('--dry-run', is_flag=True, help='Print what would be cloned without making changes.')
@with_appcontext
def annual_events(dry_run):
    """Clone annual events that have passed with no upcoming recurrence."""
    today = date.today()
    cloned = 0

    past_annuals = (
        Event.query
        .filter(Event.is_annual == True, Event.start_date < today)
        .order_by(Event.start_date.desc())
        .all()
    )

    checked = set()
    with _request_ctx():
        for event in past_annuals:
            key = (event.family_id, event.name.strip().lower())
            if key in checked:
                continue
            checked.add(key)

            has_future = Event.query.filter(
                Event.family_id == event.family_id,
                Event.name == event.name,
                Event.is_annual == True,
                Event.start_date >= today,
            ).first()

            if has_future:
                continue

            if dry_run:
                new_start = _advance_year(event.start_date)
                click.echo(f'[DRY RUN] would clone "{event.name}" ({event.family.name}) → {new_start}')
            else:
                new_event = _clone_event(event)
                db.session.commit()

                admin = _admin_for(event.family)
                if admin:
                    event_url = url_for('main.event_detail', event_id=new_event.id, _external=True)
                    create_notification(admin, 'new_event',
                                        title=f'Annual event auto-scheduled: {new_event.name}',
                                        body=f'Review and update for {new_event.date_range_display()}',
                                        url=event_url)
                    if current_app.config.get('MAIL_ENABLED'):
                        send_annual_event_cloned_email(admin, new_event, event_url)

                click.echo(f'Cloned "{event.name}" ({event.family.name}) → {new_event.start_date}')
                cloned += 1

    if not dry_run:
        click.echo(f'annual-events done: {cloned} event(s) cloned.')


@click.command('merge-persons')
@click.option('--keep', 'keep_id', required=True, type=int, help='Person ID to keep.')
@click.option('--remove', 'remove_id', required=True, type=int, help='Person ID to delete after merge.')
@click.option('--dry-run', is_flag=True, help='Print what would change without committing.')
@with_appcontext
def merge_persons(keep_id, remove_id, dry_run):
    """Merge two duplicate Person records. All data transfers to --keep; --remove is deleted."""
    from .people_merge import merge_person_records
    keep = db.session.get(Person, keep_id)
    remove = db.session.get(Person, remove_id)
    if not keep:
        click.echo(f'ERROR: Person {keep_id} not found.', err=True); return
    if not remove:
        click.echo(f'ERROR: Person {remove_id} not found.', err=True); return
    click.echo(f'KEEP  : #{keep.id} — {keep.name} (family: {keep.family.name})')
    click.echo(f'REMOVE: #{remove.id} — {remove.name}')
    if remove.user:
        click.echo(f'  ⚠ REMOVE has a linked account ({remove.user.email}) — it will move to KEEP.')
    try:
        log = merge_person_records(keep, remove)
    except ValueError as e:
        db.session.rollback()
        click.echo(f'ERROR: {e}', err=True); return
    for line in log:
        click.echo(('[DRY RUN] ' if dry_run else '') + line)
    if dry_run:
        db.session.rollback()
        click.echo('[DRY RUN] No changes committed.')
    else:
        db.session.commit()
        click.echo('Done.')
