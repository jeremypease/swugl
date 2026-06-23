"""Single source of truth for which product features are exposed.

MVP cut: only a handful of features are live. Hidden features stay fully in the
codebase (routes, templates, models) but are gated out of the nav and blocked at
the route layer, so they can be switched back on later without rebuilding them.

To bring a feature back: add its key to DEFAULT_FEATURES below, or set the
ENABLED_FEATURES env var (comma-separated) which overrides the default —
e.g. ENABLED_FEATURES="activity,events,photos,members,chat".
"""
import os

# Features live in the current product (the MVP cut).
DEFAULT_FEATURES = {'activity', 'events', 'photos', 'members'}

# Every gateable content feature → the URL prefix(es) it owns, so one
# before_request hook can block the disabled ones. Chrome (home, notifications,
# profile, support, admin, billing, platform) and the mobile API are
# intentionally absent here — they are never gated.
FEATURE_PREFIXES = {
    'activity':      ('/activity',),
    'events':        ('/events',),
    'photos':        ('/albums',),
    'members':       ('/members',),
    'timeline':      ('/timeline',),
    'documents':     ('/documents',),
    'announcements': ('/announcements',),
    'messages':      ('/messages',),
    'cards':         ('/cards',),
    'polls':         ('/polls',),
    'gifts':         ('/registries',),
    'chat':          ('/chat',),
    'stories':       ('/stories',),
}

# Convenience: the full set (used by the test suite, which exercises every
# feature regardless of the production MVP cut).
ALL_FEATURES = set(FEATURE_PREFIXES)


def resolve_enabled_features():
    """Resolve the live feature set from env (override) or the MVP default."""
    raw = os.environ.get('ENABLED_FEATURES')
    if raw:
        return {f.strip().lower() for f in raw.split(',') if f.strip()}
    return set(DEFAULT_FEATURES)


def path_feature(path):
    """Return the feature key that owns `path`, or None when `path` isn't a
    gateable feature route (chrome / api / static all return None)."""
    for feat, prefixes in FEATURE_PREFIXES.items():
        for pre in prefixes:
            if path == pre or path.startswith(pre + '/'):
                return feat
    return None
