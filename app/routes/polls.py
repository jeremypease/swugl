from flask import render_template, redirect, url_for, flash, request, abort, jsonify, current_app
from flask_login import login_required, current_user
from . import main
from .. import db
from datetime import date


# ── Polls ─────────────────────────────────────────────────────────────────────

@main.route('/polls')
@login_required
def polls():
    if not current_user.active_family.enable_polls:
        flash('Polls are disabled for this family.', 'error')
        return redirect(url_for('main.home'))
    from ..models import Poll, PollVote
    from sqlalchemy import func as _func
    all_polls = Poll.query.filter_by(family_id=current_user.active_family_id)\
        .order_by(Poll.created_at.desc()).all()
    poll_ids = [p.id for p in all_polls]
    voter_counts = {}
    if poll_ids:
        rows = db.session.query(
            PollVote.poll_id, _func.count(_func.distinct(PollVote.person_id))
        ).filter(PollVote.poll_id.in_(poll_ids)).group_by(PollVote.poll_id).all()
        voter_counts = {pid: cnt for pid, cnt in rows}
    my_voted_ids = set()
    if current_user.person and poll_ids:
        voted = db.session.query(PollVote.poll_id).filter(
            PollVote.poll_id.in_(poll_ids),
            PollVote.person_id == current_user.person.id
        ).distinct().all()
        my_voted_ids = {r.poll_id for r in voted}
    active_polls = [p for p in all_polls if not p.is_closed]
    closed_polls = [p for p in all_polls if p.is_closed]
    return render_template('polls.html', active_polls=active_polls, closed_polls=closed_polls,
                           voter_counts=voter_counts, my_voted_ids=my_voted_ids,
                           today=date.today())


@main.route('/polls/new', methods=['POST'])
@login_required
def create_poll():
    from ..models import Poll, PollOption
    question = request.form.get('question', '').strip()
    closes_at_str = request.form.get('closes_at', '').strip()
    options = [o.strip() for o in request.form.getlist('options') if o.strip()]
    if not question or len(options) < 2:
        flash('A poll needs a question and at least 2 options.', 'error')
        return redirect(url_for('main.polls'))
    closes_at = None
    if closes_at_str:
        try:
            from datetime import date as dt_date
            closes_at = dt_date.fromisoformat(closes_at_str)
        except ValueError:
            pass  # keep closes_at=None when form value is malformed
    poll = Poll(
        family_id=current_user.active_family_id,
        created_by_id=current_user.person.id if current_user.person else None,
        question=question,
        closes_at=closes_at,
    )
    db.session.add(poll)
    db.session.flush()
    for label in options:
        db.session.add(PollOption(poll_id=poll.id, label=label))
    db.session.commit()
    return redirect(url_for('main.poll_detail', poll_id=poll.id))


@main.route('/polls/<int:poll_id>')
@login_required
def poll_detail(poll_id):
    from ..models import Poll, PollVote
    poll = db.session.get(Poll, poll_id)
    if not poll or poll.family_id != current_user.active_family_id:
        abort(404)
    my_votes = set()
    if current_user.person:
        my_votes = {v.option_id for v in PollVote.query.filter_by(
            poll_id=poll_id, person_id=current_user.person.id
        ).all()}
    return render_template('poll_detail.html', poll=poll, my_votes=my_votes)


@main.route('/polls/<int:poll_id>/vote', methods=['POST'])
@login_required
def vote_poll(poll_id):
    from ..models import Poll, PollVote
    poll = db.session.get(Poll, poll_id)
    if not poll or poll.family_id != current_user.active_family_id or poll.is_closed:
        abort(404)
    if not current_user.person:
        flash('Link your family profile to vote.', 'error')
        return redirect(url_for('main.poll_detail', poll_id=poll_id))
    option_ids = [int(x) for x in request.form.getlist('options') if x.isdigit()]
    valid_ids = {o.id for o in poll.options}
    # Remove all existing votes then re-add selected
    PollVote.query.filter_by(poll_id=poll_id, person_id=current_user.person.id).delete()
    for oid in option_ids:
        if oid in valid_ids:
            db.session.add(PollVote(poll_id=poll_id, option_id=oid,
                                    person_id=current_user.person.id))
    db.session.commit()
    return redirect(url_for('main.poll_detail', poll_id=poll_id))


@main.route('/polls/<int:poll_id>/delete', methods=['POST'])
@login_required
def delete_poll(poll_id):
    from ..models import Poll
    poll = db.session.get(Poll, poll_id)
    if not poll or poll.family_id != current_user.active_family_id:
        abort(404)
    is_creator = current_user.person and poll.created_by_id == current_user.person.id
    if not current_user.active_is_admin and not is_creator:
        abort(403)
    db.session.delete(poll)
    db.session.commit()
    flash('Poll deleted.', 'info')
    return redirect(url_for('main.polls'))


@main.route('/polls/ai-suggest', methods=['POST'])
@login_required
def poll_ai_suggest():
    from flask import jsonify
    from ..ai import suggest_poll
    data = request.json or {}
    topic = data.get('topic', '').strip()
    if not topic:
        return jsonify({'error': 'Missing topic'}), 400
    if not current_app.config.get('ANTHROPIC_API_KEY'):
        return jsonify({'error': 'AI not configured'}), 503
    try:
        result = suggest_poll(topic, current_user.active_family.name)
        return jsonify(result)
    except Exception:
        current_app.logger.exception('AI poll suggest error')
        return jsonify({'error': 'AI suggest failed'}), 500
