import re

from flask import Flask, redirect, url_for

from api import api_bp
from auth import auth_bp
from config import Config
from models import db
from routes import routes_bp
from security import csrf_token, validate_csrf_request


def create_app(config_class=Config):
    """Create and configure the Flask application."""
    app = Flask(__name__)
    app.config.from_object(config_class)
    _validate_security_configuration(app.config)
    db.init_app(app)
    app.register_blueprint(auth_bp, url_prefix="/auth")
    app.register_blueprint(routes_bp)
    app.register_blueprint(api_bp)

    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def protect_browser_requests():
        """Require CSRF tokens for browser-submitted state changes."""
        validate_csrf_request()

    @app.route("/")
    def index():
        """Redirect the root page to the login form."""
        return redirect(url_for("auth.login"))

    return app


def _validate_security_configuration(config):
    """Fail closed rather than starting with predictable application secrets."""
    secret_key = config.get("SECRET_KEY")
    if not isinstance(secret_key, str) or len(secret_key) < 32:
        raise RuntimeError("SECRET_KEY must contain at least 32 characters.")

    card_scan_key = config.get("CARD_SCAN_KEY")
    if not isinstance(card_scan_key, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", card_scan_key
    ):
        raise RuntimeError("CARD_SCAN_KEY must be a 64-character hexadecimal key.")


if __name__ == "__main__":
    app = create_app()
    app.run()
