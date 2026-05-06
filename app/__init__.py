from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager

db = SQLAlchemy()
login_manager = LoginManager()

def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'dev-secret-key-change-later'
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///family.db'

    db.init_app(app)
    login_manager.init_app(app)

    from .routes import main
    app.register_blueprint(main)

    return app
