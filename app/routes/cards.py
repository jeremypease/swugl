from flask import render_template, redirect, url_for, flash, request, current_app, abort, jsonify
from flask_login import login_required, current_user
from .. import db
from . import main, admin_required
from datetime import date

# ── Greeting Cards ────────────────────────────────────────────────────────────

CARD_OCCASIONS = [
    ('birthday', 'Birthday'),
    ('anniversary', 'Anniversary'),
    ('milestone', 'Milestone'),
    ('custom', 'Other'),
]

@main.route('/cards')
@login_required
def cards():
    if not current_user.active_family.enable_greeting_cards:
        flash('Greeting cards are disabled for this family.', 'error')
        return redirect(url_for('main.home'))
    from ..models import GreetingCard, CardSignature
    all_cards = GreetingCard.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(GreetingCard.created_at.desc()).all()
    people = Person.query.filter_by(
        family_id=current_user.active_family_id, in_directory=True
    ).order_by(Person.name).all()
    my_person_id = current_user.person.id if current_user.person else None
    visible = [c for c in all_cards if c.recipient_id != my_person_id]
    my_signed_ids = set()
    if my_person_id:
        sigs = CardSignature.query.filter(
            CardSignature.card_id.in_([c.id for c in visible]),
            CardSignature.person_id == my_person_id
        ).all()
        my_signed_ids = {s.card_id for s in sigs}
    active_cards = [c for c in visible if not c.sent_at]
    sent_cards = [c for c in visible if c.sent_at]
    return render_template('cards.html', active_cards=active_cards, sent_cards=sent_cards,
                           people=people, occasions=CARD_OCCASIONS,
                           my_signed_ids=my_signed_ids, today=date.today())


@main.route('/cards/new', methods=['POST'])
@login_required
def create_card():
    from ..models import GreetingCard
    recipient_id = request.form.get('recipient_id', type=int)
    occasion = request.form.get('occasion', 'custom')
    title = request.form.get('title', '').strip()
    send_date_str = request.form.get('send_date', '').strip()
    if not recipient_id or not title:
        flash('Please fill in all required fields.', 'error')
        return redirect(url_for('main.cards'))
    recipient = db.session.get(Person, recipient_id)
    if not recipient or recipient.family_id != current_user.active_family_id:
        abort(404)
    send_date = None
    if send_date_str:
        try:
            from datetime import date as dt_date
            send_date = dt_date.fromisoformat(send_date_str)
        except ValueError:
            pass  # keep send_date=None when form value is malformed
    card = GreetingCard(
        family_id=current_user.active_family_id,
        recipient_id=recipient_id,
        created_by_id=current_user.person.id if current_user.person else None,
        occasion=occasion,
        title=title,
        send_date=send_date,
    )
    db.session.add(card)
    db.session.commit()
    flash(f'Card created! Invite the family to sign it.', 'success')
    return redirect(url_for('main.card_detail', card_id=card.id))


@main.route('/cards/<int:card_id>')
@login_required
def card_detail(card_id):
    from ..models import GreetingCard, CardSignature
    card = db.session.get(GreetingCard, card_id)
    if not card or card.family_id != current_user.active_family_id:
        abort(404)
    # Block recipient from viewing their own card
    if current_user.person and card.recipient_id == current_user.person.id:
        flash('This card is for you — no peeking! 🎉', 'info')
        return redirect(url_for('main.cards'))
    my_signature = None
    if current_user.person:
        my_signature = CardSignature.query.filter_by(
            card_id=card_id, person_id=current_user.person.id
        ).first()
    is_creator = current_user.person and card.created_by_id == current_user.person.id
    unsigned_members = []
    if current_user.active_is_admin or is_creator:
        signed_ids = {s.person_id for s in card.signatures}
        all_members = Person.query.filter_by(
            family_id=current_user.active_family_id, in_directory=True
        ).order_by(Person.name).all()
        unsigned_members = [p for p in all_members
                            if p.id not in signed_ids and p.id != card.recipient_id]
    return render_template('card_detail.html', card=card, my_signature=my_signature,
                           occasions=dict(CARD_OCCASIONS),
                           unsigned_members=unsigned_members,
                           is_creator=is_creator)


@main.route('/cards/<int:card_id>/sign', methods=['POST'])
@login_required
def sign_card(card_id):
    from ..models import GreetingCard, CardSignature
    card = db.session.get(GreetingCard, card_id)
    if not card or card.family_id != current_user.active_family_id:
        abort(404)
    if not current_user.person:
        flash('Link your family profile to sign cards.', 'error')
        return redirect(url_for('main.card_detail', card_id=card_id))
    if card.recipient_id == current_user.person.id:
        abort(403)
    message = request.form.get('message', '').strip()
    if not message:
        flash('Please write a message.', 'error')
        return redirect(url_for('main.card_detail', card_id=card_id))
    existing = CardSignature.query.filter_by(
        card_id=card_id, person_id=current_user.person.id
    ).first()
    if existing:
        existing.message = message
    else:
        db.session.add(CardSignature(
            card_id=card_id, person_id=current_user.person.id, message=message
        ))
    db.session.commit()
    flash('Your message has been added to the card!', 'success')
    return redirect(url_for('main.card_detail', card_id=card_id))


@main.route('/cards/<int:card_id>/delete', methods=['POST'])
@login_required
def delete_card(card_id):
    from ..models import GreetingCard
    card = db.session.get(GreetingCard, card_id)
    if not card or card.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and card.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    db.session.delete(card)
    db.session.commit()
    flash('Card deleted.', 'info')
    return redirect(url_for('main.cards'))


@main.route('/cards/<int:card_id>/mark-sent', methods=['POST'])
@login_required
def mark_card_sent(card_id):
    from ..models import GreetingCard
    card = db.session.get(GreetingCard, card_id)
    if not card or card.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and card.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    card.sent_at = datetime.utcnow()
    db.session.commit()
    flash('Card marked as sent.', 'info')
    return redirect(url_for('main.card_detail', card_id=card_id))



@main.route('/cards/ai-draft', methods=['POST'])
@login_required
def card_ai_draft():
    from flask import jsonify
    from ..ai import draft_card_message
    data = request.json or {}
    recipient_name = data.get('recipient_name', '').strip()
    occasion = data.get('occasion', '').strip()
    if not recipient_name or not occasion:
        return jsonify({'error': 'Missing fields'}), 400
    if not current_app.config.get('ANTHROPIC_API_KEY'):
        return jsonify({'error': 'AI not configured'}), 503
    try:
        message = draft_card_message(recipient_name, occasion, current_user.active_family.name)
        return jsonify({'message': message})
    except Exception:
        current_app.logger.exception('AI card draft error')
        return jsonify({'error': 'AI draft failed'}), 500

