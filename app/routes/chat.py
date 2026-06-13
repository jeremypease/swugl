from flask import render_template, redirect, url_for, flash, request, current_app, jsonify, abort, Response
from flask_login import login_required, current_user
from ..models import ChatMessage, User, Notification, Family
from .. import db, limiter
from ..notifications import create_notification
from . import main, admin_required
from ..billing import requires_plan, family_has_paid_access
from ..forms import ChatMessageForm
from datetime import datetime, timedelta
import csv
import io

# ── Chat ──────────────────────────────────────────────────────────────────────

def _notify_chat_members(msg):
    """Notify family members not currently viewing chat.

    Collapses into one unread notification per user — if an unread chat
    notification already exists it is updated in place rather than a new row
    created, so a burst of messages doesn't flood the bell.
    """
    from ..notifications import create_notification
    from ..models import Notification
    cutoff = datetime.utcnow() - timedelta(seconds=10)
    recipients = User.query.filter(
        User.family_id == msg.family_id,
        User.id != msg.author_id,
        db.or_(User.chat_last_seen_at == None, User.chat_last_seen_at < cutoff),
    ).all()
    author_name = msg.author.get_full_name()
    for recipient in recipients:
        existing = Notification.query.filter_by(
            user_id=recipient.id,
            event_type='chat_message',
            read_at=None,
        ).first()
        if existing:
            existing.title = f'New message from {author_name}'
            existing.body = msg.body[:120]
            existing.created_at = datetime.utcnow()
            db.session.commit()
        else:
            create_notification(
                recipient,
                'chat_message',
                title=f'New message from {author_name}',
                body=msg.body[:120],
                url='/chat',
            )


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
    threshold = datetime.utcnow() - timedelta(seconds=30)
    if current_user.chat_last_seen_at is None or current_user.chat_last_seen_at < threshold:
        current_user.chat_last_seen_at = datetime.utcnow()
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

