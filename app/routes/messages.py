"""Future / scheduled messages: write now, delivered to a family member on a
future date. Hidden from the recipient until delivery."""
from datetime import datetime, date

from flask import render_template, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from .. import db
from ..models import ScheduledMessage, Person
from ..forms import MessageForm
from . import main


def _people_choices():
    people = (Person.query.filter_by(family_id=current_user.active_family_id)
              .order_by(Person.name).all())
    return [(p.id, p.get_display_name()) for p in people]


@main.route('/messages')
@login_required
def messages():
    fid = current_user.active_family_id
    authored = (ScheduledMessage.query
                .filter_by(family_id=fid, author_user_id=current_user.id)
                .order_by(ScheduledMessage.deliver_on.desc()).all())
    received = []
    if current_user.person:
        received = (ScheduledMessage.query
                    .filter_by(family_id=fid, recipient_person_id=current_user.person.id)
                    .filter(ScheduledMessage.delivered_at.isnot(None))
                    .order_by(ScheduledMessage.delivered_at.desc()).all())
    return render_template('messages.html', authored=authored, received=received,
                           today=date.today())


@main.route('/messages/new', methods=['GET', 'POST'])
@login_required
def message_new():
    form = MessageForm()
    form.recipient_id.choices = _people_choices()
    if form.validate_on_submit():
        if form.deliver_on.data <= date.today():
            flash('Pick a delivery date in the future.', 'error')
            return render_template('message_form.html', form=form)
        recipient = db.session.get(Person, form.recipient_id.data)
        if not recipient or recipient.family_id != current_user.active_family_id:
            abort(403)
        msg = ScheduledMessage(
            family_id=current_user.active_family_id,
            author_user_id=current_user.id,
            recipient_person_id=recipient.id,
            subject=(form.subject.data or '').strip() or None,
            body=form.body.data.strip(),
            deliver_on=form.deliver_on.data,
        )
        db.session.add(msg)
        db.session.commit()
        flash(f'Your message to {recipient.get_display_name()} is scheduled for '
              f'{form.deliver_on.data.strftime("%B %-d, %Y")}.', 'info')
        return redirect(url_for('main.messages'))
    return render_template('message_form.html', form=form)


@main.route('/messages/<int:message_id>')
@login_required
def message_detail(message_id):
    msg = db.session.get(ScheduledMessage, message_id)
    if not msg or msg.family_id != current_user.active_family_id:
        abort(404)
    is_author = msg.author_user_id == current_user.id
    is_recipient = bool(current_user.person and msg.recipient_person_id == current_user.person.id)
    # The recipient must not be able to peek before delivery; non-parties never.
    if not is_author and not (is_recipient and msg.is_delivered):
        abort(403)
    return render_template('message_detail.html', msg=msg, is_author=is_author)


@main.route('/messages/<int:message_id>/cancel', methods=['POST'])
@login_required
def message_cancel(message_id):
    msg = db.session.get(ScheduledMessage, message_id)
    if (not msg or msg.family_id != current_user.active_family_id
            or msg.author_user_id != current_user.id):
        abort(403)
    if msg.is_delivered:
        flash("That message has already been delivered and can't be canceled.", 'error')
        return redirect(url_for('main.messages'))
    db.session.delete(msg)
    db.session.commit()
    flash('Scheduled message canceled.', 'info')
    return redirect(url_for('main.messages'))
