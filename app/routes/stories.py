"""Family Stories: AI-prompted life questions, answered in-app (self or proxy)."""
from datetime import datetime

from flask import render_template, request, redirect, url_for, flash, abort
from flask_login import login_required, current_user

from .. import db
from ..models import StoryPrompt, StoryResponse, Person
from ..forms import StoryAnswerForm
from ..billing import requires_plan, family_has_paid_access
from . import main


def _can_manage(person):
    """May this viewer enable/answer stories for `person`? Subject themselves,
    or an admin/contributor recording on their behalf (proxy capture)."""
    if current_user.active_is_admin or current_user.active_is_delegate:
        return True
    return bool(current_user.person and current_user.person.id == person.id)


def _recent_questions(person, limit=10):
    rows = (StoryPrompt.query
            .filter_by(family_id=person.family_id, person_id=person.id)
            .order_by(StoryPrompt.created_at.desc())
            .limit(limit).all())
    return [r.question for r in rows]


@main.route('/stories')
@login_required
def stories():
    family = current_user.active_family
    if not family or not family.enable_stories:
        abort(404)
    if not family_has_paid_access(family):
        return render_template('stories.html', upgrade_required=True)
    fid = current_user.active_family_id
    can_manage_all = current_user.active_is_admin or current_user.active_is_delegate
    open_q = StoryPrompt.query.filter_by(family_id=fid, answered_at=None)
    if not can_manage_all:
        my_pid = current_user.person.id if current_user.person else -1
        open_q = open_q.filter_by(person_id=my_pid)
    open_prompts = open_q.order_by(StoryPrompt.created_at.desc()).all()
    answered = (StoryPrompt.query
                .filter(StoryPrompt.family_id == fid, StoryPrompt.answered_at.isnot(None))
                .order_by(StoryPrompt.answered_at.desc()).limit(20).all())
    # People an admin/contributor could enroll (or the viewer's own opt-in state)
    participants = Person.query.filter_by(family_id=fid, stories_enabled=True).all()
    return render_template('stories.html', upgrade_required=False,
                           open_prompts=open_prompts, answered=answered,
                           participants=participants, can_manage_all=can_manage_all)


@main.route('/stories/<int:prompt_id>')
@login_required
def story_detail(prompt_id):
    family = current_user.active_family
    if not family or not family.enable_stories:
        abort(404)
    prompt = StoryPrompt.query.filter_by(
        id=prompt_id, family_id=current_user.active_family_id).first_or_404()
    can_answer = family_has_paid_access(family) and _can_manage(prompt.person)
    form = StoryAnswerForm(answer=prompt.response.answer if prompt.response else None)
    return render_template('story_detail.html', prompt=prompt, form=form,
                           can_answer=can_answer)


@main.route('/stories/<int:prompt_id>/answer', methods=['POST'])
@login_required
@requires_plan
def story_answer(prompt_id):
    prompt = StoryPrompt.query.filter_by(
        id=prompt_id, family_id=current_user.active_family_id).first_or_404()
    if not _can_manage(prompt.person):
        abort(403)
    form = StoryAnswerForm()
    if not form.validate_on_submit():
        flash('Please write an answer before saving.', 'error')
        return redirect(url_for('main.story_detail', prompt_id=prompt_id))
    answer_text = form.answer.data.strip()
    recorder_id = current_user.person.id if current_user.person else None
    if prompt.response:
        prompt.response.answer = answer_text
        prompt.response.answered_by_id = recorder_id
        new_story = False
    else:
        db.session.add(StoryResponse(prompt_id=prompt.id, answer=answer_text,
                                     answered_by_id=recorder_id))
        prompt.answered_at = datetime.utcnow()
        new_story = True
    db.session.commit()
    if new_story:
        from ..notifications import notify_family
        notify_family(
            prompt.family_id, 'new_story',
            title=f'{prompt.person.get_display_name()} shared a family story',
            body=prompt.question[:120],
            url=url_for('main.story_detail', prompt_id=prompt.id),
            exclude_user_id=current_user.id,
        )
        flash('Story saved — your family can read it now.', 'success')
    else:
        flash('Story updated.', 'success')
    return redirect(url_for('main.story_detail', prompt_id=prompt_id))


@main.route('/stories/person/<int:person_id>/enable', methods=['POST'])
@login_required
def story_enable(person_id):
    person = Person.query.filter_by(
        id=person_id, family_id=current_user.active_family_id).first_or_404()
    if not _can_manage(person):
        abort(403)
    person.stories_enabled = True
    db.session.commit()
    flash(f'{person.get_display_name()} is now part of Family Stories.', 'info')
    return redirect(request.referrer or url_for('main.stories'))


@main.route('/stories/person/<int:person_id>/disable', methods=['POST'])
@login_required
def story_disable(person_id):
    person = Person.query.filter_by(
        id=person_id, family_id=current_user.active_family_id).first_or_404()
    if not _can_manage(person):
        abort(403)
    person.stories_enabled = False
    db.session.commit()
    flash(f'{person.get_display_name()} removed from Family Stories.', 'info')
    return redirect(request.referrer or url_for('main.stories'))


@main.route('/stories/person/<int:person_id>/new-prompt', methods=['POST'])
@login_required
@requires_plan
def story_new_prompt(person_id):
    person = Person.query.filter_by(
        id=person_id, family_id=current_user.active_family_id).first_or_404()
    if not _can_manage(person):
        abort(403)
    from ..ai import generate_story_prompt
    question = generate_story_prompt(person, recent_questions=_recent_questions(person))
    if not question:
        flash('Story prompts need AI to be configured. Please try again later.', 'error')
        return redirect(request.referrer or url_for('main.stories'))
    prompt = StoryPrompt(family_id=person.family_id, person_id=person.id,
                         question=question.strip(), source='manual')
    person.stories_enabled = True
    person.story_last_prompted_at = datetime.utcnow()
    db.session.add(prompt)
    db.session.commit()
    # Nudge the subject if they have their own account.
    if person.user:
        from ..notifications import create_notification
        create_notification(person.user, 'story_prompt',
                            title='You have a new family story prompt',
                            body=question[:120],
                            url=url_for('main.story_detail', prompt_id=prompt.id))
    return redirect(url_for('main.story_detail', prompt_id=prompt.id))
