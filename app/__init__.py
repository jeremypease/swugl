from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf.csrf import CSRFProtect
from dotenv import load_dotenv
import os

load_dotenv()
import certifi
os.environ['SSL_CERT_FILE'] = certifi.where()

db = SQLAlchemy()
login_manager = LoginManager()
migrate = Migrate()
csrf = CSRFProtect()

def create_app():
    app = Flask(__name__)

    secret = os.environ.get('SECRET_KEY')
    if not secret:
        raise RuntimeError("SECRET_KEY environment variable is not set")
    app.config['SECRET_KEY'] = secret
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///family.db'
    app.config['SENDGRID_API_KEY'] = os.environ.get('SENDGRID_API_KEY')
    app.config['SENDGRID_FROM_EMAIL'] = os.environ.get('SENDGRID_FROM_EMAIL')
    app.config['MAIL_ENABLED'] = os.environ.get('MAIL_ENABLED', '').lower() == 'true'

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    login_manager.login_view = 'main.login'
    login_manager.login_message_category = 'error'
    csrf.init_app(app)

    from .models import User, Person
    from .routes import main
    app.register_blueprint(main)

    return app
