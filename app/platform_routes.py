from flask import Blueprint, render_template, redirect, url_for, flash, request, session, current_app
from flask_login import login_required, current_user
from functools import wraps
from datetime import datetime, timedelta
from . import db
from .models import (Family, User, Person, Event, Album, Photo,
                     PlatformAuditLog, SystemAnnouncement, UserPodMembership)

platform = Blueprint('platform', __name__, url_prefix='/platform')


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
        'active_subs':  Family.query.filter(Family.plan.in_(['active', 'trial'])).count(),
        'pods_this_week':  Family.query.filter(Family.created_at >= week_ago).count(),
        'pods_this_month': Family.query.filter(Family.created_at >= month_ago).count(),
        'users_this_week': User.query.filter(
            User.status == 'approved', User.approved_date >= week_ago.date()
        ).count(),
    }
    recent_pods = Family.query.order_by(Family.created_at.desc()).limit(10).all()
    audit_log = PlatformAuditLog.query.order_by(PlatformAuditLog.created_at.desc()).limit(20).all()
    return render_template('platform/dashboard.html',
                           stats=stats, recent_pods=recent_pods, audit_log=audit_log)


# ── Pods ─────────────────────────────────────────────────────────────────────

@platform.route('/pods')
@login_required
@platform_admin_required
def pods():
    q = request.args.get('q', '').strip()
    query = Family.query
    if q:
        query = query.filter(Family.name.ilike(f'%{q}%'))
    families = query.order_by(Family.created_at.desc()).all()
    # Annotate with member count
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
    return render_template('platform/pod_detail.html',
                           pod=pod, members=members,
                           event_count=event_count, photo_count=photo_count,
                           album_count=album_count, person_count=person_count)


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
    flash(f'You are now viewing {pod.name} in support mode (read-only).', 'info')
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


@platform.route('/dismiss-announcement/<int:ann_id>', methods=['POST'])
@login_required
def dismiss_announcement(ann_id):
    dismissed = session.get('dismissed_announcements', [])
    if ann_id not in dismissed:
        dismissed.append(ann_id)
    session['dismissed_announcements'] = dismissed
    return ('', 204)
