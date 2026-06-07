"""Central notification dispatch.

All code that wants to notify users should call notify() rather than
calling send_* email functions directly.  notify() checks the user's
preference before sending, so users who opt out are silently skipped.
"""
from datetime import date, datetime, timedelta
from flask import current_app, url_for

from . import db
from .models import NotificationPreference, NOTIFICATION_EVENTS, User, Event, Announcement, Person, Photo, SpouseRelationship


def create_notification(user, event_type, title, body=None, url=None):
    """Write an in-app Notification row and dispatch a push if the user has it enabled."""
    meta = NOTIFICATION_EVENTS.get(event_type, {})
    if not meta.get('in_app'):
        return
    if not NotificationPreference.is_enabled(user.id, event_type, 'in_app'):
        return
    from .models import Notification
    db.session.add(Notification(
        user_id=user.id,
        event_type=event_type,
        title=title,
        body=body,
        url=url,
    ))
    db.session.commit()
    send_push_notification(user, title, body=body, url=url)


def send_push_notification(user, title, body=None, url=None):
    """Dispatch a push notification to all registered devices for the user.

    No-op until PUSH_ENABLED is set. Mobile apps register tokens via
    POST /api/v1/push/register; this function iterates them and dispatches
    platform-specific payloads.
    """
    if not current_app.config.get('PUSH_ENABLED'):
        return
    from .models import UserDevice
    devices = UserDevice.query.filter_by(user_id=user.id).all()
    for device in devices:
        try:
            if device.platform == 'ios':
                _send_apns(device.token, title, body, url)
            elif device.platform == 'android':
                _send_fcm(device.token, title, body, url)
        except Exception:
            pass  # stale token — prune in a future task


def _send_apns(token, title, body, url):
    """Send APNs push. Configure APNS_KEY_ID, APNS_TEAM_ID, APNS_BUNDLE_ID, APNS_PRIVATE_KEY."""
    pass  # implement when mobile app is in TestFlight


def _send_fcm(token, title, body, url):
    """Send FCM push. Configure FCM_SERVER_KEY."""
    pass  # implement when Android app is in internal testing


def notify(users, event_type, **kwargs):
    """Send email + in-app notification to one or many users.

    Args:
        users: a User instance or a list of User instances.
        event_type: string matching a key in NOTIFICATION_EVENTS.
        **kwargs: passed through to the relevant send function.
    """
    if not isinstance(users, (list, tuple)):
        users = [users]

    from .email import send_new_event_notification, send_announcement_notification

    _dispatch = {
        'new_event':    send_new_event_notification,
        'announcement': send_announcement_notification,
    }
    send_fn = _dispatch.get(event_type)

    for user in users:
        # Email
        if current_app.config.get('MAIL_ENABLED') and send_fn:
            if NotificationPreference.is_enabled(user.id, event_type):
                send_fn(user, **kwargs)
        # In-app
        url = kwargs.get('url')
        if event_type == 'new_event':
            event = kwargs.get('event')
            create_notification(user, event_type,
                                title=f'New event: {event.name}',
                                body=event.date_range_display(),
                                url=url)
        elif event_type == 'announcement':
            ann = kwargs.get('announcement')
            create_notification(user, event_type,
                                title=f'New announcement: {ann.title}',
                                body=(ann.body[:120] + '…') if ann.body and len(ann.body) > 120 else ann.body,
                                url=url)


# ── Weekly digest ──────────────────────────────────────────────────────────

def _birthdays_in_window(people, start: date, end: date):
    """Return (person, date_this_cycle) pairs whose birthday falls in [start, end]."""
    results = []
    for person in people:
        if not person.birthday:
            continue
        for year in (start.year, start.year + 1):
            try:
                bd = person.birthday.replace(year=year)
            except ValueError:
                # Feb 29 on a non-leap year
                bd = date(year, 3, 1)
            if start <= bd <= end:
                results.append((person, bd))
                break
    results.sort(key=lambda x: x[1])
    return results


def _anniversaries_in_window(family_id, start: date, end: date):
    """Return (rel, date_this_cycle) pairs whose marriage_date falls in [start, end]."""
    rels = (
        SpouseRelationship.query
        .join(Person, SpouseRelationship.person1_id == Person.id)
        .filter(Person.family_id == family_id, SpouseRelationship.marriage_date.isnot(None))
        .all()
    )
    results = []
    for rel in rels:
        for year in (start.year, start.year + 1):
            try:
                ad = rel.marriage_date.replace(year=year)
            except ValueError:
                ad = date(year, 3, 1)
            if start <= ad <= end:
                results.append((rel, ad))
                break
    results.sort(key=lambda x: x[1])
    return results


def compute_digest(family):
    """Return a dict of digest content for a family, or None if nothing to send."""
    today = date.today()
    week_ahead = today + timedelta(days=7)
    two_weeks_ahead = today + timedelta(days=14)
    week_ago = today - timedelta(days=7)
    week_ago_dt = datetime.combine(week_ago, datetime.min.time())

    upcoming_events = (
        Event.query
        .filter(
            Event.family_id == family.id,
            Event.start_date >= today,
            Event.start_date <= two_weeks_ahead,
        )
        .order_by(Event.start_date)
        .all()
    )

    people = Person.query.filter_by(family_id=family.id).all()
    upcoming_birthdays = _birthdays_in_window(people, today, week_ahead)
    upcoming_anniversaries = _anniversaries_in_window(family.id, today, week_ahead)

    recent_announcements = (
        Announcement.query
        .filter(
            Announcement.family_id == family.id,
            Announcement.created_at >= week_ago_dt,
        )
        .order_by(Announcement.created_at.desc())
        .limit(3)
        .all()
    )

    recent_members = (
        User.query
        .filter(
            User.family_id == family.id,
            User.status == 'approved',
            User.approved_date >= week_ago,
        )
        .all()
    )

    recent_photo_count = (
        Photo.query
        .filter(
            Photo.family_id == family.id,
            Photo.created_at >= week_ago_dt,
        )
        .count()
    )

    has_content = bool(
        upcoming_events or upcoming_birthdays or upcoming_anniversaries
        or recent_announcements or recent_members or recent_photo_count
    )
    if not has_content:
        return None

    return {
        'upcoming_events': upcoming_events,
        'upcoming_birthdays': upcoming_birthdays,
        'upcoming_anniversaries': upcoming_anniversaries,
        'recent_announcements': recent_announcements,
        'recent_members': recent_members,
        'recent_photo_count': recent_photo_count,
    }


def send_family_digest(family):
    """Send the weekly digest to all opted-in members of a family.

    Returns the number of emails sent.
    """
    from .email import send_digest_email
    from .ai import narrate_digest

    content = compute_digest(family)
    if content is None:
        return 0

    try:
        ai_intro = narrate_digest(content, family.name)
    except Exception:
        ai_intro = None

    members = User.query.filter_by(family_id=family.id, status='approved').all()
    dashboard_url = url_for('main.home', _external=True)
    sent = 0
    for user in members:
        if NotificationPreference.is_enabled(user.id, 'digest'):
            send_digest_email(user, family, content, dashboard_url, ai_intro=ai_intro)
            sent += 1
    return sent
