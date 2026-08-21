"""
BeatHub - Create Administrator Account

Run this script once to create an administrator account.

Usage:
    python create_admin.py

The script uses the same database, User model, UserRole,
and password hashing system as the BeatHub application.
"""

import getpass
import sys
import uuid

from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models.user import User, UserRole
from app.utils.security import hash_password


def create_admin():
    print()
    print("=" * 60)
    print("BeatHub Administrator Account Setup")
    print("=" * 60)
    print()

    email = input("Admin email: ").strip().lower()

    if not email:
        print("ERROR: Admin email is required.")
        sys.exit(1)

    if "@" not in email:
        print("ERROR: Please enter a valid email address.")
        sys.exit(1)

    password = getpass.getpass("Admin password: ")

    if not password:
        print("ERROR: Password is required.")
        sys.exit(1)

    if len(password) < 8:
        print("ERROR: Password must be at least 8 characters.")
        sys.exit(1)

    confirm_password = getpass.getpass(
        "Confirm admin password: "
    )

    if password != confirm_password:
        print("ERROR: Passwords do not match.")
        sys.exit(1)

    db = SessionLocal()

    try:
        # ----------------------------------------------------------
        # Check whether the email already exists
        # ----------------------------------------------------------

        existing_user = (
            db.query(User)
            .filter(User.email == email)
            .first()
        )

        if existing_user:

            existing_role = getattr(
                existing_user.role,
                "value",
                existing_user.role,
            )

            print()
            print(
                f"An account already exists for: {email}"
            )
            print(
                f"Current role: {existing_role}"
            )
            print()

            if str(existing_role).lower() == "admin":
                print(
                    "This account is already an administrator."
                )
                print(
                    "No changes were made."
                )
                return

            promote = input(
                "Promote this existing account to ADMIN? "
                "[y/N]: "
            ).strip().lower()

            if promote != "y":
                print("No changes were made.")
                return

            existing_user.role = UserRole.ADMIN
            existing_user.is_active = True

            db.commit()
            db.refresh(existing_user)

            print()
            print("=" * 60)
            print("ADMIN ACCOUNT CREATED")
            print("=" * 60)
            print()
            print(f"Email: {existing_user.email}")
            print("Role: ADMIN")
            print("Status: ACTIVE")
            print()
            print("Admin login:")
            print("/admin/login")
            print()
            print("You can also use the normal:")
            print("/login")
            print()
            print("=" * 60)
            return

        # ----------------------------------------------------------
        # Create brand-new administrator
        # ----------------------------------------------------------

        admin = User(
            id=str(uuid.uuid4()),
            email=email,
            hashed_password=hash_password(password),
            role=UserRole.ADMIN,
            is_active=True,
            is_verified=True,
        )

        db.add(admin)

        try:
            db.commit()

        except IntegrityError:
            db.rollback()

            print()
            print(
                "ERROR: Could not create administrator."
            )
            print(
                "An account with this email may already exist."
            )
            sys.exit(1)

        db.refresh(admin)

        print()
        print("=" * 60)
        print("ADMIN ACCOUNT CREATED SUCCESSFULLY")
        print("=" * 60)
        print()
        print(f"Email: {admin.email}")
        print("Role: ADMIN")
        print("Status: ACTIVE")
        print("Verified: YES")
        print()
        print("Admin login:")
        print("/admin/login")
        print()
        print("The administrator can also use:")
        print("/login")
        print()
        print("=" * 60)

    except KeyboardInterrupt:
        db.rollback()

        print()
        print()
        print("Operation cancelled.")

    except Exception as exc:
        db.rollback()

        print()
        print("=" * 60)
        print("ERROR CREATING ADMIN")
        print("=" * 60)
        print()
        print(str(exc))
        print()

        sys.exit(1)

    finally:
        db.close()


if __name__ == "__main__":
    create_admin()
