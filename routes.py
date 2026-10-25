"""Dashboard and payment routes for students, parents, merchants and admins."""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, update
from sqlalchemy.exc import SQLAlchemyError

from auth import login_required, role_required
from models import (
    Admin,
    Merchant,
    Parent,
    PaymentSession,
    Student,
    Transaction,
    db,
)
from time_utils import utcnow_naive

routes_bp = Blueprint("routes", __name__)

RECENT_TRANSACTIONS_LIMIT = 20
ZERO_AMOUNT = Decimal("0.00")
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("9999999999.99")
MONTHLY_REPORT_MONTHS = 12
TERMINAL_PAYMENT_SESSION_STATUSES = frozenset(
    {
        "completed",
        "failed",
        "expired",
        "cancelled",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login_redirect():
    """Clear stale session state and redirect the user to the login page."""
    _prepare_login_redirect()
    return redirect(url_for("auth.login"))


def _prepare_login_redirect():
    """Clear stale session state and show the login prompt."""
    session.clear()
    flash("Please log in to continue.", "error")


def get_user_id() -> int:
    """Return the authenticated user's ID, aborting if the session is invalid."""
    user_id = session.get("user_id")
    if user_id is None:
        abort(401)
    return user_id


def _sum_successful_payments(*criteria):
    """Sum the amounts of successful payments matching the given criteria."""
    total = (
        db.session.query(func.sum(Transaction.amount))
        .filter(
            Transaction.transaction_type == "payment",
            Transaction.status == "success",
            *criteria,
        )
        .scalar()
    )
    return total if total is not None else ZERO_AMOUNT


def _student_monthly_spending(student_id: int) -> Decimal:
    """Total successful payments made by a student in the current month."""
    now = utcnow_naive()
    month_start = datetime.combine(now.date().replace(day=1), time.min)
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)
    return _sum_successful_payments(
        Transaction.student_id == student_id,
        Transaction.created_at >= month_start,
        Transaction.created_at < next_month,
    )


def _sales_by_period(
    merchant_id: int,
    start: datetime,
    end: datetime,
    period_format: str,
) -> dict[str, Decimal]:
    """Aggregate successful merchant payments into SQLite date buckets."""
    period = func.strftime(period_format, Transaction.created_at)
    rows = (
        db.session.query(period, func.sum(Transaction.amount))
        .filter(
            Transaction.merchant_id == merchant_id,
            Transaction.transaction_type == "payment",
            Transaction.status == "success",
            Transaction.created_at >= start,
            Transaction.created_at < end,
        )
        .group_by(period)
        .all()
    )
    return {
        bucket: amount if amount is not None else ZERO_AMOUNT for bucket, amount in rows
    }


def _chart_rows(
    dates: list[date],
    totals: dict[str, Decimal],
    label_format: str,
    key_format: str,
) -> list[dict[str, object]]:
    """Build labeled chart values and safe integer bar widths."""
    values = [
        (period, totals.get(period.strftime(key_format), ZERO_AMOUNT))
        for period in dates
    ]
    maximum = max((amount for _, amount in values), default=ZERO_AMOUNT)
    return [
        {
            "label": period.strftime(label_format),
            "amount": amount,
            "width": int(amount * 100 / maximum) if maximum else 0,
        }
        for period, amount in values
    ]


def _merchant_sales_reports(merchant_id: int, today: date):
    """Return current-month daily and trailing twelve-month sales buckets."""
    first_daily_date = today.replace(day=1)
    daily_dates = [
        first_daily_date + timedelta(days=offset)
        for offset in range((today - first_daily_date).days + 1)
    ]

    current_month = today.replace(day=1)
    month_dates = []
    for months_ago in reversed(range(MONTHLY_REPORT_MONTHS)):
        month_index = current_month.year * 12 + current_month.month - 1 - months_ago
        year, zero_based_month = divmod(month_index, 12)
        month_dates.append(date(year, zero_based_month + 1, 1))

    first_month = month_dates[0]
    next_month = date(
        current_month.year + (current_month.month == 12),
        current_month.month % 12 + 1,
        1,
    )
    start = datetime.combine(first_daily_date, time.min)
    end = datetime.combine(today + timedelta(days=1), time.min)
    daily_totals = _sales_by_period(merchant_id, start, end, "%Y-%m-%d")
    monthly_totals = _sales_by_period(
        merchant_id,
        datetime.combine(first_month, time.min),
        datetime.combine(next_month, time.min),
        "%Y-%m",
    )
    return (
        _chart_rows(daily_dates, daily_totals, "%a %d", "%Y-%m-%d"),
        _chart_rows(month_dates, monthly_totals, "%b %Y", "%Y-%m"),
    )


def _recent_transactions(**filters):
    """Return the most recent transactions matching the given filters."""
    return (
        Transaction.query.filter_by(**filters)
        .order_by(Transaction.created_at.desc())
        .limit(RECENT_TRANSACTIONS_LIMIT)
        .all()
    )


def _parse_amount(raw_value: Optional[str]) -> Optional[Decimal]:
    """Convert form input to a positive Decimal, or return None if invalid."""
    if raw_value is None:
        return None

    try:
        amount = Decimal(raw_value)
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount <= ZERO_AMOUNT or amount > MAX_AMOUNT:
        return None
    try:
        if amount != amount.quantize(CENT):
            return None
    except InvalidOperation:
        return None
    return amount


# ---------------------------------------------------------------------------
# Student / parent
# ---------------------------------------------------------------------------


@routes_bp.route("/student/dashboard")
@login_required
@role_required("student")
def student_dashboard():
    user_id = get_user_id()
    student = db.session.get(Student, user_id)
    if student is None:
        return _login_redirect()

    return render_template(
        "student_dashboard.html",
        student=student,
        spending=_student_monthly_spending(student.id),
        transactions=_recent_transactions(student_id=student.id),
    )


@routes_bp.route("/parent/dashboard")
@login_required
@role_required("parent")
def parent_dashboard():
    user_id = get_user_id()
    parent = db.session.get(Parent, user_id)
    if parent is None:
        return _login_redirect()

    student = parent.student
    if student is None:
        return _login_redirect()

    return render_template(
        "parent_dashboard.html",
        parent=parent,
        student=student,
        spending=_student_monthly_spending(student.id),
        transactions=_recent_transactions(student_id=student.id),
    )


# ---------------------------------------------------------------------------
# Merchant
# ---------------------------------------------------------------------------


@routes_bp.route("/merchant/dashboard")
@login_required
@role_required("merchant")
def merchant_dashboard():
    user_id = get_user_id()
    merchant = db.session.get(Merchant, user_id)
    if merchant is None:
        return _login_redirect()

    now = utcnow_naive()
    today_start = datetime.combine(now.date(), time.min)
    tomorrow_start = today_start + timedelta(days=1)
    month_start = datetime.combine(now.date().replace(day=1), time.min)
    if now.month == 12:
        next_month_start = datetime(now.year + 1, 1, 1)
    else:
        next_month_start = datetime(now.year, now.month + 1, 1)

    today_sales = _sum_successful_payments(
        Transaction.merchant_id == merchant.id,
        Transaction.created_at >= today_start,
        Transaction.created_at < tomorrow_start,
    )
    month_sales = _sum_successful_payments(
        Transaction.merchant_id == merchant.id,
        Transaction.created_at >= month_start,
        Transaction.created_at < next_month_start,
    )
    daily_sales, monthly_sales = _merchant_sales_reports(merchant.id, now.date())

    return render_template(
        "merchant_dashboard.html",
        merchant=merchant,
        today_sales=today_sales,
        month_sales=month_sales,
        daily_sales=daily_sales,
        monthly_sales=monthly_sales,
        transactions=_recent_transactions(merchant_id=merchant.id),
    )


@routes_bp.route("/merchant/scan")
@login_required
@role_required("merchant")
def merchant_scan():
    user_id = get_user_id()
    merchant = db.session.get(Merchant, user_id)
    if merchant is None:
        return _login_redirect()

    return render_template("merchant_scan.html", merchant=merchant)


@routes_bp.route("/merchant/enter_amount/<session_id>", methods=["GET", "POST"])
@login_required
@role_required("merchant")
def merchant_enter_amount(session_id):
    user_id = get_user_id()
    merchant = db.session.get(Merchant, user_id)
    if merchant is None:
        return _login_redirect()

    payment_session = PaymentSession.query.filter_by(session_id=session_id).first()

    # --- Guard: session must exist ---
    if not payment_session:
        flash("Invalid payment session.", "error")
        return redirect(url_for("routes.merchant_dashboard"))

    # --- Guard: merchant must own this session (Req #7) ---
    if payment_session.merchant_id != merchant.id:
        flash("Unauthorized to access this session.", "error")
        return redirect(url_for("routes.merchant_dashboard"))

    # --- Guard: terminal states cannot be re-processed (Req #8) ---
    if payment_session.status in TERMINAL_PAYMENT_SESSION_STATUSES:
        flash(
            f"Session already {payment_session.status} — cannot be reprocessed.",
            "warning",
        )
        return redirect(url_for("routes.merchant_dashboard"))

    # --- Guard: TTL check (transition to expired) ---
    if payment_session.expires_at < utcnow_naive():
        payment_session.status = "expired"
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Failed to expire a payment session.")
            flash("Unable to update payment status. Please try again.", "error")
            return redirect(url_for("routes.merchant_dashboard"))
        flash("Payment session expired.", "error")
        return redirect(url_for("routes.merchant_dashboard"))

    # --- Guard: only awaiting_amount can accept a new amount ---
    if payment_session.status != "awaiting_amount":
        flash(
            "Session is not ready for amount entry "
            f"(status: {payment_session.status}).",
            "warning",
        )
        return redirect(url_for("routes.merchant_dashboard"))

    student = payment_session.student
    if student is None:
        flash("Student for this payment session was not found.", "error")
        return redirect(url_for("routes.merchant_dashboard"))

    if request.method == "POST":
        amount = _parse_amount(request.form.get("amount"))
        if amount is None:
            flash("Please enter a valid positive amount.", "error")
            return render_template(
                "merchant_enter_amount.html",
                merchant=merchant,
                session_id=session_id,
                student=student,
            )

        try:
            claim = db.session.execute(
                update(PaymentSession)
                .where(
                    PaymentSession.id == payment_session.id,
                    PaymentSession.status == "awaiting_amount",
                    PaymentSession.expires_at > utcnow_naive(),
                )
                .values(status="processing")
                .returning(PaymentSession.id)
            )
            claim_id = claim.scalar_one_or_none()
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Failed to claim a payment session.")
            flash("Unable to start this payment. Please try again.", "error")
            return redirect(url_for("routes.merchant_dashboard"))

        if claim_id is None:
            flash("This payment session is no longer available.", "warning")
            return redirect(url_for("routes.merchant_dashboard"))

        now = utcnow_naive()
        try:
            debit = db.session.execute(
                update(Student)
                .where(
                    Student.id == student.id,
                    Student.balance >= amount,
                )
                .values(balance=Student.balance - amount)
                .returning(Student.id)
            )
            debited_student_id = debit.scalar_one_or_none()
            if debited_student_id is None:
                db.session.rollback()
                _mark_processing_session_failed(payment_session.id)
                flash("Insufficient student balance. Payment was not charged.", "error")
                return redirect(url_for("routes.merchant_dashboard"))

            transaction = Transaction()
            transaction.transaction_id = str(uuid.uuid4())
            transaction.student_id = student.id
            transaction.merchant_id = merchant.id
            transaction.amount = amount
            transaction.transaction_type = "payment"
            transaction.status = "success"
            transaction.created_at = now
            db.session.add(transaction)

            completion = db.session.execute(
                update(PaymentSession)
                .where(
                    PaymentSession.id == payment_session.id,
                    PaymentSession.status == "processing",
                    PaymentSession.expires_at > now,
                )
                .values(
                    amount=amount,
                    status="completed",
                    completed_at=now,
                )
                .returning(PaymentSession.id)
            )
            completed_session_id = completion.scalar_one_or_none()
            if completed_session_id is None:
                db.session.rollback()
                flash("Payment session expired before completion.", "error")
                return redirect(url_for("routes.merchant_dashboard"))

            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Atomic payment transaction failed.")
            _mark_processing_session_failed(payment_session.id)
            flash("Payment failed due to an internal error.", "error")
            return redirect(url_for("routes.merchant_dashboard"))

        flash(f"Payment of ₹{amount:.2f} successful!", "success")
        return redirect(url_for("routes.merchant_dashboard"))

    return render_template(
        "merchant_enter_amount.html",
        merchant=merchant,
        session_id=session_id,
        student=student,
    )


def _mark_processing_session_failed(payment_session_id: int) -> None:
    """Mark a claimed session failed after a rolled-back payment attempt."""
    try:
        db.session.execute(
            update(PaymentSession)
            .where(
                PaymentSession.id == payment_session_id,
                PaymentSession.status == "processing",
            )
            .values(status="failed")
        )
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to finalize an unsuccessful payment.")


@routes_bp.route("/merchant/cancel_session/<session_id>", methods=["POST"])
@login_required
@role_required("merchant")
def cancel_payment_session(session_id):
    """Cancel an owned session only while it is waiting for an amount."""
    merchant = db.session.get(Merchant, get_user_id())
    if merchant is None:
        return _login_redirect()

    now = utcnow_naive()
    try:
        transition = db.session.execute(
            update(PaymentSession)
            .where(
                PaymentSession.session_id == session_id,
                PaymentSession.merchant_id == merchant.id,
                PaymentSession.status == "awaiting_amount",
                PaymentSession.expires_at > now,
            )
            .values(status="cancelled")
            .returning(PaymentSession.id)
        )
        transitioned_session_id = transition.scalar_one_or_none()
        if transitioned_session_id is not None:
            db.session.commit()
            flash("Payment session cancelled.", "success")
        else:
            db.session.rollback()
            flash("This payment session can no longer be cancelled.", "warning")
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.exception("Failed to cancel a payment session.")
        flash("Unable to cancel this payment session. Please try again.", "error")
    return redirect(url_for("routes.merchant_dashboard"))


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


@routes_bp.route("/admin/dashboard", methods=["GET", "POST"])
@login_required
@role_required("admin")
def admin_dashboard():
    user_id = get_user_id()
    admin = db.session.get(Admin, user_id)
    if admin is None:
        return _login_redirect()

    if request.method == "POST":
        student_id_input = request.form.get("student_id", "").strip()
        raw_amount = request.form.get("amount")

        amount = _parse_amount(raw_amount)
        if not amount:
            flash("Please enter a valid positive amount.", "danger")
            return redirect(url_for("routes.admin_dashboard"))

        student = Student.query.filter_by(student_id=student_id_input).first()
        if not student:
            flash(f"Student '{student_id_input}' not found.", "danger")
            return redirect(url_for("routes.admin_dashboard"))

        try:
            balance_update = db.session.execute(
                update(Student)
                .where(
                    Student.id == student.id,
                    Student.balance <= MAX_AMOUNT - amount,
                )
                .values(balance=Student.balance + amount)
                .returning(Student.balance)
            )
            updated_balance = balance_update.scalar_one_or_none()
            if updated_balance is None:
                db.session.rollback()
                flash(
                    "The resulting balance exceeds the supported limit.",
                    "danger",
                )
                return redirect(url_for("routes.admin_dashboard"))

            tx = Transaction()
            tx.transaction_id = str(uuid.uuid4())
            tx.student_id = student.id
            tx.admin_id = admin.id
            tx.amount = amount
            tx.transaction_type = "balance_addition"
            tx.status = "success"
            tx.created_at = utcnow_naive()
            db.session.add(tx)
            db.session.commit()
            flash(
                f"Successfully added ₹{amount:.2f} to {student.name} "
                f"({student.student_id}). "
                f"New balance: ₹{updated_balance:.2f}",
                "success",
            )
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Admin balance addition failed.")
            flash("Unable to add balance. No changes were saved.", "danger")

        return redirect(url_for("routes.admin_dashboard"))

    return render_template("admin_dashboard.html", admin=admin)
