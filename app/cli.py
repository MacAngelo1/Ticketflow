import argparse
import getpass
import time

from sqlalchemy import delete, select

from app.database import SessionLocal
from app.models import RateBucket, User
from app.security import password_hasher
from app.services.common import audit


def main():
    parser = argparse.ArgumentParser(description="TicketFlow local administration")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-admin")
    create.add_argument("email")
    commands.add_parser("cleanup-rate-limits")
    args = parser.parse_args()
    with SessionLocal.begin() as db:
        if args.command == "cleanup-rate-limits":
            db.execute(delete(RateBucket).where(RateBucket.window < int(time.time()) // 60 - 1440))
            print("Removed rate counters older than one day.")
            return
        from pydantic import EmailStr, TypeAdapter

        email = str(TypeAdapter(EmailStr).validate_python(args.email)).lower()
        if db.scalar(select(User.id).where(User.email == email)):
            raise SystemExit("Email already registered. Choose a separate admin email.")
        password = getpass.getpass("Admin password (8-128 characters): ")
        if not 8 <= len(password) <= 128:
            raise SystemExit("Invalid password length.")
        if password != getpass.getpass("Confirm password: "):
            raise SystemExit("Passwords do not match.")
        user = User(
            email=email,
            full_name="Platform Admin",
            password_hash=password_hasher.hash(password),
            role="admin",
        )
        db.add(user)
        db.flush()
        audit(db, user, "admin.bootstrap", user.id, "Admin created through local CLI.")
    print("Admin created. Log in using this email in Swagger's username field.")


if __name__ == "__main__":
    main()
