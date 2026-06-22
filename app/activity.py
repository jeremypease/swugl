"""Pod activity feed — a derive-on-read aggregation of recent family activity.

No new tables: we query the existing content models, normalize each row to an
ActivityItem, and merge them newest-first. Cheap and always accurate for a
family-sized pod (and it can never drift out of sync with the real data). If it
ever needs to scale, this is the seam to swap in a materialized feed table.

Sources are limited to rows that carry a real created_at + family_id, so every
item has an honest timestamp. (User/Person have no created_at, so "new member"
is intentionally left out of v1 rather than faking a time.)
"""
from collections import namedtuple, defaultdict

from .models import Announcement, Event, Photo, Poll, Album, StoryResponse, StoryPrompt

ActivityItem = namedtuple('ActivityItem', 'kind icon timestamp text url')


def _trunc(s, n=80):
    s = (s or '').strip()
    return s if len(s) <= n else s[:n - 1] + '…'


def recent_activity(family_id, limit=40):
    """Return up to `limit` ActivityItems for a family, newest first."""
    items = []

    for a in (Announcement.query.filter_by(family_id=family_id)
              .order_by(Announcement.created_at.desc()).limit(limit)):
        who = a.author.get_display_name() if a.author else 'Someone'
        items.append(ActivityItem('announcement', 'megaphone', a.created_at,
                                   f'{who} posted “{_trunc(a.title)}”', '/announcements'))

    for e in (Event.query.filter_by(family_id=family_id)
              .order_by(Event.created_at.desc()).limit(limit)):
        items.append(ActivityItem('event', 'calendar', e.created_at,
                                   f'New event: {_trunc(e.name)}', f'/events/{e.id}'))

    for p in (Poll.query.filter_by(family_id=family_id)
              .order_by(Poll.created_at.desc()).limit(limit)):
        who = p.created_by.get_display_name() if p.created_by else 'Someone'
        items.append(ActivityItem('poll', 'bar-chart-2', p.created_at,
                                   f'{who} started a poll: {_trunc(p.question)}', '/polls'))

    for al in (Album.query.filter_by(family_id=family_id)
               .order_by(Album.created_at.desc()).limit(limit)):
        items.append(ActivityItem('album', 'folder', al.created_at,
                                   f'New album: {_trunc(al.name)}', f'/albums/{al.id}'))

    # Photos — collapse by (album, uploader, calendar day) so a 30-photo upload
    # is one feed item, not thirty.
    groups = defaultdict(lambda: {'count': 0, 'ts': None, 'album_id': None, 'uploader': 'Someone'})
    for ph in (Photo.query.filter_by(family_id=family_id)
               .order_by(Photo.created_at.desc()).limit(limit * 5)):
        if not ph.created_at:
            continue
        key = (ph.album_id, ph.uploaded_by_id, ph.created_at.date())
        g = groups[key]
        g['count'] += 1
        g['album_id'] = ph.album_id
        g['uploader'] = ph.uploaded_by.get_display_name() if ph.uploaded_by else 'Someone'
        if g['ts'] is None or ph.created_at > g['ts']:
            g['ts'] = ph.created_at
    for g in groups.values():
        n = g['count']
        url = f'/albums/{g["album_id"]}' if g['album_id'] else '/albums'
        items.append(ActivityItem('photos', 'image', g['ts'],
                                   f'{g["uploader"]} added {n} photo{"s" if n != 1 else ""}', url))

    for r in (StoryResponse.query
              .join(StoryPrompt, StoryResponse.prompt_id == StoryPrompt.id)
              .filter(StoryPrompt.family_id == family_id)
              .order_by(StoryResponse.created_at.desc()).limit(limit)):
        subject = (r.prompt.person.get_display_name()
                   if r.prompt and r.prompt.person else 'A family member')
        items.append(ActivityItem('story', 'book-open', r.created_at,
                                   f'{subject}’s story was shared', '/stories'))

    items = [i for i in items if i.timestamp]
    items.sort(key=lambda i: i.timestamp, reverse=True)
    return items[:limit]
