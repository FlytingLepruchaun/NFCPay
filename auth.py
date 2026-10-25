from functools import wraps

from flask import (
    Blueprint,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from models import Admin, Merchant, Parent, Student

auth_bp = Blueprint("auth", __name__)


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if "user_id" not in session or "role" not in session:
            if request.blueprint == "api":
                return jsonify({"error": "Authentication required."}), 401
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


def role_required(role):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if "role" not in session or session["role"] != role:
                if request.blueprint == "api":
                    return jsonify({"error": "Merchant access required."}), 403
                flash(f"Unauthorized access. You must be a {role}.", "error")
                return redirect(url_for("auth.login"))
            return f(*args, **kwargs)

        return decorated_function

    return decorator


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_id_input = request.form.get("user_id")
        password = request.form.get("password")

        if not user_id_input or not password:
            flash("Please provide both ID and password.", "error")
            return render_template("login.html")

        # Try to find the user in all tables
        user = Student.query.filter_by(student_id=user_id_input).first()
        role = "student"

        if not user:
            user = Parent.query.filter_by(parent_id=user_id_input).first()
            role = "parent"
        if not user:
            user = Merchant.query.filter_by(merchant_id=user_id_input).first()
            role = "merchant"
        if not user:
            user = Admin.query.filter_by(admin_id=user_id_input).first()
            role = "admin"

        if user and check_password_hash(user.password_hash, password):
            session.clear()
            session["user_id"] = user.id
            session["role"] = role
            return redirect(url_for(f"routes.{role}_dashboard"))
        else:
            flash("Invalid ID or password.", "error")

    return render_template("login.html")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
