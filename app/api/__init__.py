from flask import Blueprint

api = Blueprint('api', __name__, url_prefix='/api/v1')

from . import auth, events, members, notifications, push, version
