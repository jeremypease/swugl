"""Pod-wide group chat: routes, notification fan-out, and poll throttling."""
from datetime import datetime, timedelta

from flask import render_template, request, redirect, url_for, jsonify, abort, Response
from flask_login import login_required, current_user

from .. import db
from ..models import (
    ChatMessage, User, Notification, NotificationPreference, NOTIFICATION_EVENTS,
)
from ..forms import ChatMessageForm
from ..billing import requires_plan, family_has_paid_access
from . import main, admin_required

# A member is "actively viewing" chat if their last-seen timestamp is within
# this window. It must exceed CHAT_SEEN_THROTTLE (below) so a member who is
# actively polling — but whose timestamp is only written periodically — is never
# mistaken for absent and re-notified.
CHAT_VIEWING_WINDOW = 60  # seconds
CHAT_SEEN_THROTTLE = 25   # seconds — min gap between chat_last_seen_at writes


def _notify_chat_members(msg):
    """Notify family members not currently viewing chat, collapsed to a single
    rolling "N new messages" notification per recipient (no per-message spam)."""
    if not NOTIFICATION_EVENTS.get('chat_message', {}).get('in_app'):
        return
    cutoff = datetime.utcnow() - timedelta(seconds=CHAT_VIEWING_WINDOW)
    recipients = User.query.filter(
        User.family_id == msg.family_id,
        User.id != msg.author_id,
        db.or_(User.chat_last_seen_at == None, User.chat_last_seen_at < cutoff),
    ).all()
    author_name = msg.author.get_full_name()
    for recipient in recipients:
        if not NotificationPreference.is_enabled(recipient.id, 'chat_message', 'in_app'):
            continue
        # Count this recipient's unread messages since they last saw chat.
        q = ChatMessage.query.filter(
            ChatMessage.family_id == msg.family_id,
            ChatMessage.author_id != recipient.id,
        )
        if recipient.chat_last_seen_at:
            q = q.filter(ChatMessage.created_at > recipient.chat_last_seen_at)
        unread_count = q.count()
        title = (f'{unread_count} new messages in chat' if unread_count > 1
                 else f'New message from {author_name}')
        # Collapse any existing unread chat notifications into one rolling row.
        existing = Notification.query.filter_by(
            user_id=recipient.id, event_type='chat_message', read_at=None
        ).order_by(Notification.created_at.desc()).all()
        keep = existing[0] if existing else None
        for extra in existing[1:]:
            db.session.delete(extra)
        if keep:
            keep.title = title
            keep.body = msg.body[:120]
            keep.created_at = datetime.utcnow()
        else:
            db.session.add(Notification(
                user_id=recipient.id, event_type='chat_message',
                title=title, body=msg.body[:120], url='/chat',
            ))
        _send_push(recipient, title, msg.body[:120], '/chat')
    db.session.commit()


def _send_push(user, title, body, url):
    from ..notifications import send_push_notification
    send_push_notification(user, title, body=body, url=url)


@main.route('/chat')
@login_required
def chat():
    family = current_user.active_family
    if not family or not family.enable_chat:
        abort(404)
    if not family_has_paid_access(family):
        return render_template('chat.html', upgrade_required=True)
    messages = (
        ChatMessage.query
        .filter_by(family_id=current_user.active_family_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(50)
        .all()
    )
    messages = list(reversed(messages))
    current_user.chat_last_seen_at = datetime.utcnow()
    # Opening chat clears the rolling chat notification so the next message
    # starts a fresh count.
    Notification.query.filter_by(
        user_id=current_user.id, event_type='chat_message', read_at=None
    ).update({'read_at': datetime.utcnow()})
    db.session.commit()
    form = ChatMessageForm()
    return render_template('chat.html', messages=messages, form=form, upgrade_required=False)


@main.route('/chat/send', methods=['POST'])
@login_required
@requires_plan
def chat_send():
    form = ChatMessageForm()
    if form.validate_on_submit():
        msg = ChatMessage(
            family_id=current_user.active_family_id,
            author_id=current_user.id,
            body=form.body.data.strip(),
        )
        db.session.add(msg)
        db.session.commit()
        _notify_chat_members(msg)
    return redirect(url_for('main.chat'))


@main.route('/chat/poll')
@login_required
@requires_plan
def chat_poll():
    after_id = request.args.get('after', 0, type=int)
    msgs = (
        ChatMessage.query
        .filter_by(family_id=current_user.active_family_id)
        .filter(ChatMessage.id > after_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(100)
        .all()
    )
    # Throttle the bookkeeping write: a 5s poll would otherwise commit on every
    # tick. Only persist when the timestamp is meaningfully stale. The viewing
    # window (CHAT_VIEWING_WINDOW) is wider than this so an active poller is
    # never treated as absent between writes.
    now = datetime.utcnow()
    last = current_user.chat_last_seen_at
    if last is None or (now - last).total_seconds() > CHAT_SEEN_THROTTLE:
        current_user.chat_last_seen_at = now
        db.session.commit()
    return jsonify({
        'messages': [
            {
                'id': m.id,
                'body': m.body,
                'author_name': m.author.get_full_name(),
                'author_id': m.author_id,
                'created_at': m.created_at.isoformat(),
                'edited_at': m.edited_at.isoformat() if m.edited_at else None,
                'can_edit': m.can_edit(current_user),
                'edit_url': url_for('main.chat_edit', msg_id=m.id),
                'can_delete': m.can_delete(current_user),
                'delete_url': url_for('main.chat_delete', msg_id=m.id),
            }
            for m in msgs
        ],
        'current_user_id': current_user.id,
    })


@main.route('/chat/<int:msg_id>/edit', methods=['POST'])
@login_required
@requires_plan
def chat_edit(msg_id):
    msg = ChatMessage.query.filter_by(id=msg_id, family_id=current_user.active_family_id).first_or_404()
    if not msg.can_edit(current_user):
        return jsonify({'error': 'Edit window has closed.'}), 403
    body = (request.form.get('body') or '').strip()
    if not body:
        return jsonify({'error': 'Message cannot be empty.'}), 400
    msg.body = body
    msg.edited_at = datetime.utcnow()
    db.session.commit()
    return jsonify({'ok': True, 'body': msg.body, 'edited_at': msg.edited_at.isoformat()})


@main.route('/chat/<int:msg_id>/delete', methods=['POST'])
@login_required
@requires_plan
def chat_delete(msg_id):
    msg = ChatMessage.query.filter_by(id=msg_id, family_id=current_user.active_family_id).first_or_404()
    if not msg.can_delete(current_user):
        abort(403)
    db.session.delete(msg)
    db.session.commit()
    return redirect(url_for('main.chat'))


@main.route('/chat/export')
@login_required
@admin_required
def chat_export():
    """Download all chat messages for this family as a CSV."""
    import csv, io
    family = current_user.active_family
    if not family or not family.enable_chat:
        abort(404)
    msgs = (
        ChatMessage.query
        .filter_by(family_id=current_user.active_family_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Date (UTC)', 'Time (UTC)', 'Author', 'Message', 'Edited'])
    for m in msgs:
        writer.writerow([
            m.created_at.strftime('%Y-%m-%d'),
            m.created_at.strftime('%H:%M:%S'),
            m.author.get_full_name(),
            m.body,
            'yes' if m.edited_at else '',
        ])
    slug = family.name.lower().replace(' ', '-')
    return Response(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename="chat-{slug}.csv"'},
    )
