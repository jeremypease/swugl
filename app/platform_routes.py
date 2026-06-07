from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, timedelta
from . import db
import stripe as stripe_lib
from .models import (Family, User, Person, Event, Album, Photo,
                     PlatformAuditLog, SystemAnnouncement, UserPodMembership,
                     SupportNote, SystemConfig)
from .billing import get_stripe_mode

platform = Blueprint('platform', __name__, url_prefix='/platform')


def _stripe():
    key = current_app.config.get('STRIPE_SECRET_KEY')
    if not key:
        return None
    stripe_lib.api_key = key
    return stripe_lib


def platform_admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_platform_admin:
            flash('Access denied.', 'error')
            return redirect(url_for('main.home'))
        return f(*args, **kwargs)
    return decorated


def _audit(action, target_type=None, target_id=None, detail=None):
    db.session.add(PlatformAuditLog(
        actor_id=current_user.id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        detail=detail,
    ))
    db.session.commit()


# ── Dashboard ────────────────────────────────────────────────────────────────

@platform.route('/')
@platform.route('/dashboard')
@login_required
@platform_admin_required
def dashboard():
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    stats = {
        'total_pods':   Family.query.count(),
        'total_users':  User.query.filter_by(status='approved').count(),
        'active_subs':  Family.query.filter(Family.plan.in_(['paid', 'trial'])).count(),
        'pods_this_week':  Family.query.filter(Family.created_at >= week_ago).count(),
        'pods_this_month': Family.query.filter(Family.created_at >= month_ago).count(),
        'users_this_week': User.query.filter(
            User.status == 'approved', User.approved_date >= week_ago.date()
        ).count(),
    }
    recent_pods = Family.query.order_by(Family.created_at.desc()).limit(10).all()
    audit_log = PlatformAuditLog.query.order_by(PlatformAuditLog.created_at.desc()).limit(20).all()
    stripe_mode = get_stripe_mode()
    return render_template('platform/dashboard.html',
                           stats=stats, recent_pods=recent_pods, audit_log=audit_log,
                           stripe_mode=stripe_mode)


# ── Pods ─────────────────────────────────────────────────────────────────────

@platform.route('/pods')
@login_required
@platform_admin_required
def pods():
    q = request.args.get('q', '').strip()
    if q:
        # Search by pod name OR admin email
        email_match_ids = (
            db.session.query(User.family_id)
            .filter(User.email.ilike(f'%{q}%'), User.is_admin == True)
            .subquery()
        )
        families = (
            Family.query
            .filter(db.or_(
                Family.name.ilike(f'%{q}%'),
                Family.id.in_(email_match_ids),
            ))
            .order_by(Family.created_at.desc())
            .all()
        )
    else:
        families = Family.query.order_by(Family.created_at.desc()).all()
    pod_data = []
    for f in families:
        member_count = User.query.filter_by(family_id=f.id, status='approved').count()
        admin = User.query.filter_by(family_id=f.id, is_admin=True, status='approved').first()
        pod_data.append({'pod': f, 'member_count': member_count, 'admin': admin})
    return render_template('platform/pods.html', pod_data=pod_data, q=q)


@platform.route('/pods/<int:pod_id>')
@login_required
@platform_admin_required
def pod_detail(pod_id):
    pod = db.session.get(Family, pod_id)
    if not pod:
        flash('Pod not found.', 'error')
        return redirect(url_for('platform.pods'))
    members = User.query.filter_by(family_id=pod.id).order_by(User.is_admin.desc(), User.first_name).all()
    event_count  = Event.query.filter_by(family_id=pod.id).count()
    photo_count  = Photo.query.filter_by(family_id=pod.id).count()
    album_count  = Album.query.filter_by(family_id=pod.id).count()
    person_count = Person.query.filter_by(family_id=pod.id).count()
    notes = SupportNote.query.filter_by(pod_id=pod.id).order_by(SupportNote.created_at.desc()).all()
    return render_template('platform/pod_detail.html',
                           pod=pod, members=members,
                           event_count=event_count, photo_count=photo_count,
                           album_count=album_count, person_count=person_count,
                           notes=notes)


# ── Support mode ─────────────────────────────────────────────────────────────

@platform.route('/pods/<int:pod_id>/enter-support', methods=['POST'])
@login_required
@platform_admin_required
def enter_support(pod_id):
    pod = db.session.get(Family, pod_id)
    if not pod:
        flash('Pod not found.', 'error')
        return redirect(url_for('platform.pods'))
    reason = request.form.get('reason', '').strip()
    if not reason:
        flash('A reason is required to enter support mode.', 'error')
        return redirect(url_for('platform.pod_detail', pod_id=pod_id))
    session['active_family_id'] = pod_id
    session['support_mode'] = True
    session['support_pod_id'] = pod_id
    _audit('enter_support', 'family', pod_id, reason)
    flash(f'You are now in support mode for {pod.name}. All actions are audited.', 'info')
    return redirect(url_for('main.home'))


@platform.route('/exit-support')
@login_required
def exit_support():
    pod_id = session.pop('support_pod_id', None)
    session.pop('support_mode', None)
    session.pop('active_family_id', None)
    if current_user.is_platform_admin:
        _audit('exit_support', 'family', pod_id)
    return redirect(url_for('platform.pods') if current_user.is_platform_admin else url_for('main.home'))


# ── Users ─────────────────────────────────────────────────────────────────────

@platform.route('/users')
@login_required
@platform_admin_required
def users():
    q = request.args.get('q', '').strip()
    results = []
    if q:
        results = User.query.filter(User.email.ilike(f'%{q}%')).limit(50).all()
    return render_template('platform/users.html', results=results, q=q)


@platform.route('/users/<int:user_id>/resend-verification', methods=['POST'])
@login_required
@platform_admin_required
def resend_verification(user_id):
    from .email import send_verification_email
    from .routes import _hash_token
    import secrets
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('platform.users'))
    token = secrets.token_urlsafe(32)
    user.verification_token = _hash_token(token)
    user.verification_token_expiry = datetime.utcnow() + timedelta(hours=24)
    db.session.commit()
    if current_app.config.get('MAIL_ENABLED'):
        send_verification_email(user, url_for('main.verify_email', token=token, _external=True))
    _audit('resend_verification', 'user', user_id)
    flash(f'Verification email sent to {user.email}.', 'info')
    return redirect(url_for('platform.users', q=user.email))


@platform.route('/users/<int:user_id>/send-password-reset', methods=['POST'])
@login_required
@platform_admin_required
def send_password_reset(user_id):
    import secrets
    from .email import send_password_reset_email
    from .routes import _hash_token
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('platform.users'))
    token = secrets.token_urlsafe(32)
    user.reset_token = _hash_token(token)
    user.reset_token_expiry = datetime.utcnow() + timedelta(hours=1)
    db.session.commit()
    if current_app.config.get('MAIL_ENABLED'):
        from flask import url_for as _url_for
        reset_url = _url_for('main.reset_password', token=token, _external=True)
        send_password_reset_email(user, reset_url)
    _audit('send_password_reset', 'user', user_id)
    flash(f'Password reset email sent to {user.email}.', 'info')
    return redirect(url_for('platform.users', q=user.email))


@platform.route('/users/<int:user_id>/extend-trial', methods=['POST'])
@login_required
@platform_admin_required
def extend_trial(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('platform.users'))
    pod = user.family
    days = int(request.form.get('days', 30))
    if pod.trial_ends_at and pod.trial_ends_at > datetime.utcnow():
        pod.trial_ends_at += timedelta(days=days)
    else:
        pod.trial_ends_at = datetime.utcnow() + timedelta(days=days)
    pod.plan = 'trial'
    db.session.commit()
    _audit('extend_trial', 'family', pod.id, f'{days} days')
    flash(f"Trial for {pod.name} extended by {days} days.", 'info')
    return redirect(url_for('platform.users', q=user.email))


@platform.route('/users/<int:user_id>/toggle-platform-admin', methods=['POST'])
@login_required
@platform_admin_required
def toggle_platform_admin(user_id):
    user = db.session.get(User, user_id)
    if not user:
        flash('User not found.', 'error')
        return redirect(url_for('platform.users'))
    if user.id == current_user.id:
        flash('You cannot remove your own platform admin status.', 'error')
        return redirect(url_for('platform.users', q=user.email))
    user.is_platform_admin = not user.is_platform_admin
    db.session.commit()
    action = 'granted' if user.is_platform_admin else 'revoked'
    _audit(f'platform_admin_{action}', 'user', user.id, user.email)
    flash(f'Platform admin {action} for {user.get_full_name()}.', 'info')
    return redirect(url_for('platform.users', q=user.email))


# ── Pod billing actions ───────────────────────────────────────────────────────

@platform.route('/pods/<int:pod_id>/set-plan', methods=['POST'])
@login_required
@platform_admin_required
def set_plan(pod_id):
    pod = db.session.get(Family, pod_id)
    if not pod:
        flash('Pod not found.', 'error')
        return redirect(url_for('platform.pods'))
    new_plan = request.form.get('plan', '').strip()
    if new_plan not in ('free', 'trial', 'paid', 'past_due'):
        flash('Invalid plan.', 'error')
        return redirect(url_for('platform.pod_detail', pod_id=pod_id))
    old_plan = pod.plan
    pod.plan = new_plan
    if new_plan == 'trial' and not pod.trial_ends_at:
        from datetime import timedelta
        pod.trial_ends_at = datetime.utcnow() + timedelta(days=30)
    db.session.commit()
    _audit('set_plan', 'family', pod_id, f'{old_plan} → {new_plan}')
    flash(f'Plan updated to {new_plan}.', 'info')
    return redirect(url_for('platform.pod_detail', pod_id=pod_id))


@platform.route('/pods/<int:pod_id>/cancel-subscription', methods=['POST'])
@login_required
@platform_admin_required
def cancel_subscription(pod_id):
    pod = db.session.get(Family, pod_id)
    if not pod:
        flash('Pod not found.', 'error')
        return redirect(url_for('platform.pods'))
    if not pod.stripe_subscription_id:
        flash('No active Stripe subscription to cancel.', 'error')
        return redirect(url_for('platform.pod_detail', pod_id=pod_id))
    s = _stripe()
    if not s:
        flash('Stripe is not configured.', 'error')
        return redirect(url_for('platform.pod_detail', pod_id=pod_id))
    try:
        s.Subscription.cancel(pod.stripe_subscription_id)
        pod.plan = 'free'
        pod.stripe_subscription_id = None
        db.session.commit()
        _audit('cancel_subscription', 'family', pod_id)
        flash('Subscription cancelled and plan set to free.', 'info')
    except Exception as e:
        flash(f'Stripe error: {e}', 'error')
    return redirect(url_for('platform.pod_detail', pod_id=pod_id))


@platform.route('/pods/<int:pod_id>/set-admin', methods=['POST'])
@login_required
@platform_admin_required
def pod_set_admin(pod_id):
    pod = db.session.get(Family, pod_id)
    if not pod:
        flash('Pod not found.', 'error')
        return redirect(url_for('platform.pods'))
    user_id = request.form.get('user_id', type=int)
    user = db.session.get(User, user_id)
    if not user or user.family_id != pod_id:
        flash('User not found in this pod.', 'error')
        return redirect(url_for('platform.pod_detail', pod_id=pod_id))
    user.is_admin = True
    user.is_delegate = False
    membership = UserPodMembership.query.filter_by(user_id=user.id, family_id=pod_id).first()
    if membership:
        membership.role = 'admin'
    db.session.commit()
    _audit('set_admin', 'user', user.id, f'pod {pod_id} ({pod.name})')
    flash(f'{user.get_full_name()} is now an admin of {pod.name}.', 'info')
    return redirect(url_for('platform.pod_detail', pod_id=pod_id))


@platform.route('/pods/<int:pod_id>/approve-user/<int:user_id>', methods=['POST'])
@login_required
@platform_admin_required
def pod_approve_user(pod_id, user_id):
    pod = db.session.get(Family, pod_id)
    user = db.session.get(User, user_id)
    if not pod or not user or user.family_id != pod_id:
        flash('User not found in this pod.', 'error')
        return redirect(url_for('platform.pod_detail', pod_id=pod_id))
    from .models import NotificationPreference
    user.status = 'approved'
    NotificationPreference.seed_defaults(user.id)
    db.session.commit()
    _audit('approve_user', 'user', user.id, f'pod {pod_id} ({pod.name})')
    flash(f'{user.get_full_name()} approved.', 'info')
    return redirect(url_for('platform.pod_detail', pod_id=pod_id))


@platform.route('/pods/<int:pod_id>/delete', methods=['POST'])
@login_required
@platform_admin_required
def delete_pod(pod_id):
    pod = db.session.get(Family, pod_id)
    if not pod:
        flash('Pod not found.', 'error')
        return redirect(url_for('platform.pods'))
    confirm_name = request.form.get('confirm_name', '').strip()
    if confirm_name != pod.name:
        flash('Pod name did not match. Deletion cancelled.', 'error')
        return redirect(url_for('platform.pod_detail', pod_id=pod_id))
    pod_name = pod.name
    _audit('delete_pod', 'family', pod_id, pod_name)
    _hard_delete_pod(pod)
    flash(f'Pod "{pod_name}" and all its data have been permanently deleted.', 'info')
    return redirect(url_for('platform.pods'))


def _hard_delete_pod(pod):
    """Permanently delete a pod and all associated data in dependency order."""
    from .models import (
        Event, EventMeal, EventMealItem, EventAssignment, EventRSVP,
        EventSleepingSpot, EventComment, CarpoolOffer, EventSurveyResponse,
        EventPaymentConfig, EventPaymentRecord,
        Album, Photo, PhotoTag, Announcement, AnnouncementReaction,
        Poll, PollOption, PollVote, GreetingCard, CardSignature,
        Checklist, ChecklistItem, FamilyPayoutAccount,
        ParentRelationship, SpouseRelationship,
        NotificationPreference, Notification, UserDevice,
        OAuthAccount, UserCredential, CalendarToken,
    )
    fid = pod.id

    user_ids   = [u.id for u in User.query.filter_by(family_id=fid).all()]
    event_ids  = [e.id for e in Event.query.filter_by(family_id=fid).all()]
    album_ids  = [a.id for a in Album.query.filter_by(family_id=fid).all()]
    person_ids = [p.id for p in Person.query.filter_by(family_id=fid).all()]

    if event_ids:
        meal_ids = [m.id for m in EventMeal.query.filter(EventMeal.event_id.in_(event_ids)).all()]
        if meal_ids:
            EventMealItem.query.filter(EventMealItem.meal_id.in_(meal_ids)).delete(synchronize_session=False)
        EventMeal.query.filter(EventMeal.event_id.in_(event_ids)).delete(synchronize_session=False)
        EventAssignment.query.filter(EventAssignment.event_id.in_(event_ids)).delete(synchronize_session=False)
        EventRSVP.query.filter(EventRSVP.event_id.in_(event_ids)).delete(synchronize_session=False)
        EventSleepingSpot.query.filter(EventSleepingSpot.event_id.in_(event_ids)).delete(synchronize_session=False)
        EventComment.query.filter(EventComment.event_id.in_(event_ids)).delete(synchronize_session=False)
        CarpoolOffer.query.filter(CarpoolOffer.event_id.in_(event_ids)).update(
            {'passenger_of_id': None}, synchronize_session=False)
        CarpoolOffer.query.filter(CarpoolOffer.event_id.in_(event_ids)).delete(synchronize_session=False)
        EventSurveyResponse.query.filter(EventSurveyResponse.event_id.in_(event_ids)).delete(synchronize_session=False)
        EventPaymentRecord.query.filter(EventPaymentRecord.event_id.in_(event_ids)).delete(synchronize_session=False)
        EventPaymentConfig.query.filter(EventPaymentConfig.event_id.in_(event_ids)).delete(synchronize_session=False)
        Event.query.filter_by(family_id=fid).delete(synchronize_session=False)

    if album_ids:
        photo_ids = [p.id for p in Photo.query.filter(Photo.album_id.in_(album_ids)).all()]
        if photo_ids:
            PhotoTag.query.filter(PhotoTag.photo_id.in_(photo_ids)).delete(synchronize_session=False)
        Photo.query.filter(Photo.album_id.in_(album_ids)).delete(synchronize_session=False)
        Album.query.filter_by(family_id=fid).delete(synchronize_session=False)

    ann_ids = [a.id for a in Announcement.query.filter_by(family_id=fid).all()]
    if ann_ids:
        AnnouncementReaction.query.filter(AnnouncementReaction.announcement_id.in_(ann_ids)).delete(synchronize_session=False)
    Announcement.query.filter_by(family_id=fid).delete(synchronize_session=False)

    poll_ids = [p.id for p in Poll.query.filter_by(family_id=fid).all()]
    if poll_ids:
        opt_ids = [o.id for o in PollOption.query.filter(PollOption.poll_id.in_(poll_ids)).all()]
        if opt_ids:
            PollVote.query.filter(PollVote.option_id.in_(opt_ids)).delete(synchronize_session=False)
        PollOption.query.filter(PollOption.poll_id.in_(poll_ids)).delete(synchronize_session=False)
    Poll.query.filter_by(family_id=fid).delete(synchronize_session=False)

    card_ids = [c.id for c in GreetingCard.query.filter_by(family_id=fid).all()]
    if card_ids:
        CardSignature.query.filter(CardSignature.card_id.in_(card_ids)).delete(synchronize_session=False)
    GreetingCard.query.filter_by(family_id=fid).delete(synchronize_session=False)

    checklist_ids = [c.id for c in Checklist.query.filter_by(family_id=fid).all()]
    if checklist_ids:
        ChecklistItem.query.filter(ChecklistItem.checklist_id.in_(checklist_ids)).delete(synchronize_session=False)
    Checklist.query.filter_by(family_id=fid).delete(synchronize_session=False)

    SupportNote.query.filter_by(pod_id=fid).delete(synchronize_session=False)
    FamilyPayoutAccount.query.filter_by(family_id=fid).delete(synchronize_session=False)

    if person_ids:
        ParentRelationship.query.filter(
            db.or_(ParentRelationship.parent_id.in_(person_ids),
                   ParentRelationship.child_id.in_(person_ids))
        ).delete(synchronize_session=False)
        SpouseRelationship.query.filter(
            db.or_(SpouseRelationship.person1_id.in_(person_ids),
                   SpouseRelationship.person2_id.in_(person_ids))
        ).delete(synchronize_session=False)
        Person.query.filter_by(family_id=fid).delete(synchronize_session=False)

    if user_ids:
        NotificationPreference.query.filter(NotificationPreference.user_id.in_(user_ids)).delete(synchronize_session=False)
        Notification.query.filter(Notification.user_id.in_(user_ids)).delete(synchronize_session=False)
        UserDevice.query.filter(UserDevice.user_id.in_(user_ids)).delete(synchronize_session=False)
        OAuthAccount.query.filter(OAuthAccount.user_id.in_(user_ids)).delete(synchronize_session=False)
        UserCredential.query.filter(UserCredential.user_id.in_(user_ids)).delete(synchronize_session=False)
        UserPodMembership.query.filter(UserPodMembership.user_id.in_(user_ids)).delete(synchronize_session=False)
        CalendarToken.query.filter(CalendarToken.user_id.in_(user_ids)).delete(synchronize_session=False)
        User.query.filter_by(family_id=fid).delete(synchronize_session=False)

    db.session.delete(pod)
    db.session.commit()


@platform.route('/pods/<int:pod_id>/notes', methods=['POST'])
@login_required
@platform_admin_required
def add_note(pod_id):
    pod = db.session.get(Family, pod_id)
    if not pod:
        flash('Pod not found.', 'error')
        return redirect(url_for('platform.pods'))
    body = request.form.get('body', '').strip()
    if not body:
        flash('Note cannot be empty.', 'error')
        return redirect(url_for('platform.pod_detail', pod_id=pod_id))
    note = SupportNote(pod_id=pod_id, author_id=current_user.id, body=body)
    db.session.add(note)
    _audit('add_note', 'family', pod_id, body[:100])
    db.session.commit()
    return redirect(url_for('platform.pod_detail', pod_id=pod_id))


# ── Audit log ────────────────────────────────────────────────────────────────

@platform.route('/audit-log')
@login_required
@platform_admin_required
def audit_log():
    action_filter = request.args.get('action', '').strip()
    pod_filter = request.args.get('pod_id', type=int)
    page = request.args.get('page', 1, type=int)

    q = PlatformAuditLog.query.order_by(PlatformAuditLog.created_at.desc())
    if action_filter:
        q = q.filter(PlatformAuditLog.action == action_filter)
    if pod_filter:
        q = q.filter(
            db.or_(
                db.and_(PlatformAuditLog.target_type == 'family', PlatformAuditLog.target_id == pod_filter),
                PlatformAuditLog.detail.ilike(f'%pod {pod_filter}%'),
            )
        )
    pagination = q.paginate(page=page, per_page=50, error_out=False)
    actions = [r[0] for r in db.session.query(PlatformAuditLog.action).distinct().order_by(PlatformAuditLog.action).all()]
    pods = Family.query.order_by(Family.name).all()
    return render_template('platform/audit_log.html',
                           entries=pagination.items,
                           pagination=pagination,
                           actions=actions,
                           pods=pods,
                           action_filter=action_filter,
                           pod_filter=pod_filter)


# ── System Announcements ──────────────────────────────────────────────────────

@platform.route('/announce', methods=['GET', 'POST'])
@login_required
@platform_admin_required
def announce():
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'create':
            body = request.form.get('body', '').strip()
            expires_raw = request.form.get('expires_at', '').strip()
            if not body:
                flash('Announcement body is required.', 'error')
            else:
                expires = None
                if expires_raw:
                    try:
                        expires = datetime.strptime(expires_raw, '%Y-%m-%dT%H:%M')
                    except ValueError:
                        flash('Invalid expiry date format.', 'error')
                        return redirect(url_for('platform.announce'))
                ann = SystemAnnouncement(
                    body=body,
                    expires_at=expires,
                    created_by_id=current_user.id,
                )
                db.session.add(ann)
                db.session.commit()
                _audit('create_announcement', 'system_announcement', ann.id, body[:100])
                flash('Announcement posted.', 'info')
        elif action == 'deactivate':
            ann_id = int(request.form.get('ann_id', 0))
            ann = db.session.get(SystemAnnouncement, ann_id)
            if ann:
                ann.is_active = False
                db.session.commit()
                _audit('deactivate_announcement', 'system_announcement', ann_id)
                flash('Announcement deactivated.', 'info')
        return redirect(url_for('platform.announce'))

    announcements = SystemAnnouncement.query.order_by(SystemAnnouncement.created_at.desc()).limit(20).all()
    return render_template('platform/announce.html', announcements=announcements)


@platform.route('/debug/weather')
@login_required
@platform_admin_required
def debug_weather():
    """Diagnostic endpoint — tests geocoding and WeatherKit for a given location."""
    from flask import jsonify
    from .weather import _geocode, _weatherkit_jwt
    import requests as _req

    location = request.args.get('location', '')
    if not location:
        return jsonify({'error': 'Pass ?location=City, State'}), 400

    result = {'location': location}

    coords = _geocode(location)
    result['geocode'] = {'lat': coords[0], 'lon': coords[1]} if coords else None
    if not coords:
        return jsonify(result)

    token = _weatherkit_jwt()
    result['jwt_generated'] = bool(token)
    if not token:
        result['jwt_error'] = 'Missing WEATHERKIT_KEY_ID, WEATHERKIT_SERVICE_ID, APPLE_TEAM_ID, or APPLE_PRIVATE_KEY'
        return jsonify(result)

    lat, lon = coords
    try:
        resp = _req.get(
            f'https://weatherkit.apple.com/api/v1/weather/en/{lat}/{lon}',
            params={'dataSets': 'forecastDaily', 'timezone': 'UTC'},
            headers={'Authorization': f'Bearer {token}'},
            timeout=5,
        )
        result['weatherkit_status'] = resp.status_code
        if resp.status_code == 200:
            days = resp.json().get('forecastDaily', {}).get('days', [])
            result['forecast_days'] = len(days)
            result['sample'] = days[0] if days else None
        else:
            result['weatherkit_body'] = resp.text[:500]
    except Exception as e:
        result['weatherkit_error'] = str(e)

    return jsonify(result)


@platform.route('/stripe-mode/toggle', methods=['POST'])
@login_required
@platform_admin_required
def toggle_stripe_mode():
    current_mode = get_stripe_mode()
    new_mode = 'test' if current_mode == 'live' else 'live'
    SystemConfig.set('stripe_mode', new_mode)
    _audit('stripe_mode_toggle', detail=f'{current_mode} → {new_mode}')
    flash(f'Stripe switched to {new_mode} mode. {"Use test card 4242 4242 4242 4242." if new_mode == "test" else "Real charges are now active."}', 'info')
    return redirect(url_for('platform.dashboard'))


@platform.route('/dismiss-announcement/<int:ann_id>', methods=['POST'])
@login_required
def dismiss_announcement(ann_id):
    dismissed = session.get('dismissed_announcements', [])
    if ann_id not in dismissed:
        dismissed.append(ann_id)
    session['dismissed_announcements'] = dismissed
    return ('', 204)
