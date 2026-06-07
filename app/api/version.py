from flask import jsonify, current_app
from . import api
from .. import db
from ..models import AppVersion


@api.route('/version')
def version():
    v = AppVersion.query.filter_by(is_current=True).first()
    if not v:
        return jsonify({
            'version': current_app.config.get('APP_VERSION', '1.0.0'),
            'title': '',
            'changes': [],
        })
    return jsonify({
        'version': v.version,
        'title': v.title,
        'changes': v.changes_list(),
    })
