"""Merge two duplicate Person records into one.

`merge_person_records(keep, remove)` transfers every relationship, piece of
content, and the account from `remove` onto `keep`, then deletes `remove`. It
returns a list of human-readable action strings. The caller controls the
transaction: commit to apply, or roll back for a dry-run preview.

Completeness is enforced generically: the unique-constrained tables (where the
same person could appear twice) are de-duplicated explicitly below; EVERY other
column that references people.id is reassigned by a metadata-driven sweep, so a
new feature that adds a person FK is covered automatically (and the merge test
exercises a person with a row in every such table to prove it).
"""
from sqlalchemy import text

from . import db
from .models import (
    SpouseRelationship, ParentRelationship, EventRSVP, PollVote, CardSignature,
    AnnouncementReaction, PhotoTag, CarpoolOffer, EventSurveyResponse,
)

# (table, column) pairs handled explicitly below — the generic sweep skips these.
_EXPLICIT = {
    ('parent_relationships', 'parent_id'), ('parent_relationships', 'child_id'),
    ('spouse_relationships', 'person1_id'), ('spouse_relationships', 'person2_id'),
    ('event_rsvps', 'person_id'), ('poll_votes', 'person_id'),
    ('card_signatures', 'person_id'), ('announcement_reactions', 'person_id'),
    ('photo_tags', 'person_id'), ('carpool_offers', 'person_id'),
    ('event_survey_responses', 'person_id'),
    ('event_sleeping_assignments', 'person_id'),
}


def _person_fk_columns():
    """Every (table_name, column_name) in the schema that references people.id."""
    out = []
    for table in db.metadata.sorted_tables:
        for col in table.columns:
            for fk in col.foreign_keys:
                tgt = fk.column
                if tgt.table.name == 'people' and tgt.name == 'id':
                    out.append((table.name, col.name))
    return out


def _dedup_transfer(model, group_cols, keep_id, remove_id, log, label):
    """For a table keyed by (person_id + group_cols), move remove's rows to keep,
    dropping any that would collide with an existing keep row."""
    moved = dropped = 0
    for row in model.query.filter_by(person_id=remove_id).all():
        filt = {c: getattr(row, c) for c in group_cols}
        dup = model.query.filter_by(person_id=keep_id, **filt).first()
        if dup:
            db.session.delete(row)
            dropped += 1
        else:
            row.person_id = keep_id
            moved += 1
    if moved:
        log.append(f'Transferred {moved} {label} → kept record')
    if dropped:
        log.append(f'Dropped {dropped} duplicate {label}')


def merge_person_records(keep, remove):
    """Merge `remove` into `keep`. Returns a list of action strings. Does NOT
    commit — the caller commits to apply or rolls back to preview."""
    if keep.id == remove.id:
        raise ValueError('Cannot merge a person into themselves.')
    if keep.family_id != remove.family_id:
        raise ValueError('Both people must belong to the same family.')

    kid, rid = keep.id, remove.id
    log = []

    # ── Spouse relationships (skip self, de-dup) ─────────────────────────────
    for sr in SpouseRelationship.query.filter(
            (SpouseRelationship.person1_id == rid) |
            (SpouseRelationship.person2_id == rid)).all():
        other = sr.person2_id if sr.person1_id == rid else sr.person1_id
        if other == kid:
            db.session.delete(sr)  # the duplicates were "married" to each other
            log.append('Removed spouse link between the two duplicates')
            continue
        dup = SpouseRelationship.query.filter(
            ((SpouseRelationship.person1_id == kid) & (SpouseRelationship.person2_id == other)) |
            ((SpouseRelationship.person1_id == other) & (SpouseRelationship.person2_id == kid))
        ).first()
        if dup:
            db.session.delete(sr)
            log.append('Dropped a duplicate spouse relationship')
        else:
            if sr.person1_id == rid:
                sr.person1_id = kid
            else:
                sr.person2_id = kid
            log.append('Transferred a spouse relationship → kept record')

    # ── Parent/child relationships (skip self, de-dup) ───────────────────────
    for pr in ParentRelationship.query.filter(
            (ParentRelationship.parent_id == rid) |
            (ParentRelationship.child_id == rid)).all():
        if pr.parent_id == rid:
            if pr.child_id == kid:
                db.session.delete(pr); continue
            dup = ParentRelationship.query.filter_by(parent_id=kid, child_id=pr.child_id).first()
            target_attr = 'parent_id'
        else:
            if pr.parent_id == kid:
                db.session.delete(pr); continue
            dup = ParentRelationship.query.filter_by(parent_id=pr.parent_id, child_id=kid).first()
            target_attr = 'child_id'
        if dup:
            db.session.delete(pr)
            log.append('Dropped a duplicate parent/child link')
        else:
            setattr(pr, target_attr, kid)
            log.append('Transferred a parent/child link → kept record')

    # ── Other unique-per-person tables ───────────────────────────────────────
    _dedup_transfer(EventRSVP, ['event_id'], kid, rid, log, 'event RSVP(s)')
    _dedup_transfer(PollVote, ['option_id'], kid, rid, log, 'poll vote(s)')
    _dedup_transfer(CardSignature, ['card_id'], kid, rid, log, 'card signature(s)')
    _dedup_transfer(AnnouncementReaction, ['announcement_id', 'emoji'], kid, rid, log, 'reaction(s)')
    _dedup_transfer(PhotoTag, ['photo_id'], kid, rid, log, 'photo tag(s)')
    _dedup_transfer(CarpoolOffer, ['event_id'], kid, rid, log, 'carpool offer(s)')
    _dedup_transfer(EventSurveyResponse, ['event_id'], kid, rid, log, 'survey response(s)')

    # sleeping assignments (association table, no model — raw SQL de-dup)
    spots = db.session.execute(
        text('SELECT spot_id FROM event_sleeping_assignments WHERE person_id = :r'),
        {'r': rid}).fetchall()
    for (spot_id,) in spots:
        exists = db.session.execute(
            text('SELECT 1 FROM event_sleeping_assignments WHERE spot_id=:s AND person_id=:k'),
            {'s': spot_id, 'k': kid}).fetchone()
        if exists:
            db.session.execute(
                text('DELETE FROM event_sleeping_assignments WHERE spot_id=:s AND person_id=:r'),
                {'s': spot_id, 'r': rid})
        else:
            db.session.execute(
                text('UPDATE event_sleeping_assignments SET person_id=:k WHERE spot_id=:s AND person_id=:r'),
                {'k': kid, 's': spot_id, 'r': rid})
    if spots:
        log.append(f'Handled {len(spots)} sleeping assignment(s)')

    # ── Generic sweep: every remaining person FK is plain attribution ─────────
    # (created_by / author / uploader / assigned_to / story / account, etc.) —
    # none have per-person uniqueness, so a blanket reassign is safe and also
    # future-proofs against new tables.
    for table, col in _person_fk_columns():
        if (table, col) in _EXPLICIT:
            continue
        res = db.session.execute(
            text(f'UPDATE {table} SET {col} = :k WHERE {col} = :r'),
            {'k': kid, 'r': rid})
        if res.rowcount:
            label = 'account' if (table, col) == ('users', 'person_id') else f'{table}.{col}'
            log.append(f'Reassigned {res.rowcount} {label} → kept record')

    # ── Delete the duplicate ─────────────────────────────────────────────────
    db.session.flush()  # apply the raw UPDATEs before the ORM delete
    db.session.delete(remove)
    log.append(f'Deleted duplicate record #{rid} ({remove.name})')
    return log
