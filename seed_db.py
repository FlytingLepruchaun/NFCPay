import os
from decimal import Decimal

from werkzeug.security import generate_password_hash

from app import create_app
from models import (
    Admin,
    Card,
    Merchant,
    Parent,
    PaymentSession,
    Student,
    Transaction,
    UsedNonce,
    db,
)


def _read_seed_password(variable_name: str) -> str:
    """Read a required account password without a repository default."""
    password = os.environ.get(variable_name)
    if not password:
        raise RuntimeError(f"Set {variable_name} before seeding.")
    return password


def seed_database():
    passwords = {
        "student": _read_seed_password("SEED_STUDENT_PASSWORD"),
        "parent": _read_seed_password("SEED_PARENT_PASSWORD"),
        "merchant": _read_seed_password("SEED_MERCHANT_PASSWORD"),
        "admin": _read_seed_password("SEED_ADMIN_PASSWORD"),
    }

    app = create_app()
    with app.app_context():
        if any(
            model.query.first() is not None
            for model in (
                Student,
                Parent,
                Merchant,
                Admin,
                Card,
                Transaction,
                PaymentSession,
                UsedNonce,
            )
        ):
            print("Database already has account data; refusing to seed over it.")
            return

        # 1. Create Student
        student1 = Student(
            student_id="student1",
            name="John Doe",
            password_hash=generate_password_hash(passwords["student"]),
            balance=Decimal("50.00"),
        )
        db.session.add(student1)
        db.session.flush()

        # 2. Create Parent
        parent1 = Parent(
            parent_id="parent1",
            student_id=student1.id,
            name="Jane Doe",
            password_hash=generate_password_hash(passwords["parent"]),
        )
        db.session.add(parent1)

        # 3. Create Merchant
        merchant1 = Merchant(
            merchant_id="merchant1",
            name="Campus Cafeteria",
            password_hash=generate_password_hash(passwords["merchant"]),
        )
        db.session.add(merchant1)

        # 4. Create Admin
        admin1 = Admin(
            admin_id="admin1",
            name="System Admin",
            password_hash=generate_password_hash(passwords["admin"]),
        )
        db.session.add(admin1)

        # 5. Create Card
        card1 = Card(
            student_id=student1.id,
            card_uid="DEADBEEF1234",
            card_roll_number="student1",
            is_active=True,
        )
        db.session.add(card1)

        db.session.commit()
        print("Dummy data successfully seeded!")
        print("Seeded account passwords were read from environment variables.")
        print("- Student ID: student1")
        print("- Parent ID: parent1")
        print("- Merchant ID: merchant1")
        print("- Admin ID: admin1")


if __name__ == "__main__":
    seed_database()
