from flask import Flask, render_template, request, redirect, url_for, g
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, current_user
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
from datetime import datetime, timedelta
import sentry_sdk
from sentry_sdk.integrations.flask import FlaskIntegration
import secrets
import os

load_dotenv()
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()

sentry_dsn = os.environ.get('SENTRY_DSN')
if sentry_dsn:
    sentry_sdk.init(
        dsn=sentry_dsn,
        integrations=[FlaskIntegration()],
        traces_sample_rate=0.1,
        send_default_pii=False,
    )

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
csrf = CSRFProtect()
_redis_url = os.environ.get('REDIS_URL')
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_redis_url or 'memory://',
)

def create_app(test_config=None):
    app = Flask(__name__)

    secret = os.environ.get('SECRET_KEY')
    if not secret:
        raise RuntimeError("SECRET_KEY environment variable is not set")
    app.config['SECRET_KEY'] = secret

    # Database — read from env so Railway/Postgres works; fall back to local SQLite
    database_url = os.environ.get('DATABASE_URL') or 'sqlite:///family.db'
    # SQLAlchemy 1.4+ requires postgresql:// not postgres://
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
    }

    # Apply test overrides before any extension touches the config
    if test_config:
        app.config.update(test_config)

    app.config['REGISTRATION_OPEN'] = os.environ.get('REGISTRATION_OPEN', '').lower() == 'true'

    app.config['RESEND_API_KEY'] = os.environ.get('RESEND_API_KEY')
    app.config['RESEND_FROM_EMAIL'] = os.environ.get('RESEND_FROM_EMAIL', 'Peavines <noreply@ourpeapod.com>')
    app.config['MAIL_ENABLED'] = os.environ.get('MAIL_ENABLED', '').lower() == 'true'

    app.config['STRIPE_SECRET_KEY'] = os.environ.get('STRIPE_SECRET_KEY')
    app.config['STRIPE_PUBLISHABLE_KEY'] = os.environ.get('STRIPE_PUBLISHABLE_KEY')
    app.config['STRIPE_WEBHOOK_SECRET'] = os.environ.get('STRIPE_WEBHOOK_SECRET')
    app.config['STRIPE_MONTHLY_PRICE_ID'] = os.environ.get('STRIPE_MONTHLY_PRICE_ID')
    app.config['STRIPE_ANNUAL_PRICE_ID'] = os.environ.get('STRIPE_ANNUAL_PRICE_ID')

    app.config['SUPPORT_EMAIL'] = os.environ.get('SUPPORT_EMAIL', 'jeremypease@me.com')

    app.config['R2_ACCOUNT_ID'] = os.environ.get('R2_ACCOUNT_ID')
    app.config['R2_ACCESS_KEY_ID'] = os.environ.get('R2_ACCESS_KEY_ID')
    app.config['R2_SECRET_ACCESS_KEY'] = os.environ.get('R2_SECRET_ACCESS_KEY')
    app.config['R2_BUCKET_NAME'] = os.environ.get('R2_BUCKET_NAME')
    app.config['R2_PUBLIC_URL'] = os.environ.get('R2_PUBLIC_URL', '')

    app.config['WEBAUTHN_RP_ID'] = os.environ.get('WEBAUTHN_RP_ID', 'localhost')
    app.config['WEBAUTHN_RP_NAME'] = os.environ.get('WEBAUTHN_RP_NAME', 'OurPeaPod')
    app.config['WEBAUTHN_ORIGIN'] = os.environ.get('WEBAUTHN_ORIGIN', 'http://localhost:5000')

    app.config['GOOGLE_CLIENT_ID'] = os.environ.get('GOOGLE_CLIENT_ID')
    app.config['GOOGLE_CLIENT_SECRET'] = os.environ.get('GOOGLE_CLIENT_SECRET')

    # Session lifetime
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=14)
    app.config['REMEMBER_COOKIE_DURATION'] = timedelta(days=30)

    # Secure cookies in production (HTTPS only)
    if os.environ.get('FLASK_ENV') == 'production':
        app.config['SESSION_COOKIE_SECURE'] = True
        app.config['SESSION_COOKIE_HTTPONLY'] = True
        app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    login_manager.login_message_category = 'error'
    csrf.init_app(app)
    limiter.init_app(app)

    from .models import User, Person, SystemAnnouncement
    from .routes import main
    from .billing import billing
    from .two_factor import tf
    from .platform_routes import platform
    from .oauth import oauth_bp, init_oauth
    from .storage import photo_url
    from .commands import email_sequence, digest, rsvp_reminders
    app.register_blueprint(main)
    app.register_blueprint(billing)
    app.register_blueprint(tf)
    app.register_blueprint(platform)
    app.register_blueprint(oauth_bp)
    init_oauth(app)
    csrf.exempt(app.view_functions['billing.webhook'])
    app.cli.add_command(email_sequence)
    app.cli.add_command(digest)
    app.cli.add_command(rsvp_reminders)

    app.jinja_env.globals['photo_url'] = photo_url

    @app.context_processor
    def inject_globals():
        from flask import session as s
        ann = SystemAnnouncement.get_current()
        dismissed = s.get('dismissed_announcements', [])
        active_ann = ann if ann and ann.id not in dismissed else None
        return {
            'now': datetime.utcnow(),
            'system_announcement': active_ann,
            'support_mode': s.get('support_mode', False),
        }

    @app.template_filter('datetime_format')
    def datetime_format(ts):
        return datetime.utcfromtimestamp(int(ts)).strftime('%b %d, %Y')

    # Block unauthenticated access to uploaded family files
    @app.before_request
    def protect_uploads():
        if request.path.startswith('/static/uploads/'):
            if not current_user.is_authenticated:
                return redirect(url_for('main.login', next=request.path))

    # Block writes when a platform admin is browsing in support mode
    @app.before_request
    def block_support_mode_writes():
        from flask_login import current_user as cu
        from flask import session as s
        if (cu.is_authenticated and s.get('support_mode')
                and request.method in ('POST', 'PUT', 'DELETE', 'PATCH')
                and not request.path.startswith('/platform/exit-support')
                and not request.path.startswith('/platform/dismiss-announcement')):
            from flask import abort
            abort(403)

    # Generate a fresh CSP nonce for every request so inline scripts can be
    # whitelisted without 'unsafe-inline'.
    @app.before_request
    def set_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    # Security headers on every response
    @app.after_request
    def set_security_headers(response):
        response.headers['X-Frame-Options'] = 'SAMEORIGIN'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
        if os.environ.get('FLASK_ENV') == 'production':
            response.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains'
        nonce = getattr(g, 'csp_nonce', '')
        r2_url = app.config.get('R2_PUBLIC_URL', '').rstrip('/')
        img_src = f"'self' data: {r2_url}" if r2_url else "'self' data:"
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; "
            f"script-src 'self' https://unpkg.com 'nonce-{nonce}'; "
            "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
            "font-src 'self' https://fonts.gstatic.com; "
            f"img-src {img_src}; "
            "connect-src 'self';"
        )
        return response

    @app.errorhandler(404)
    def not_found(e):
        return render_template('errors/404.html'), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template('errors/500.html'), 500

    return app
