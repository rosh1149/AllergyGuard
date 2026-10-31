"""Administrative commands.

    python -m app.cli create-admin --email reviewer@example.com --name "Knowledge Reviewer"
(prompts for the password)
"""

import argparse
import getpass
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import User, UserRole


def create_admin(email: str, name: str, password: str) -> None:
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email == email.lower())):
            sys.exit(f"An account with {email} already exists.")
        db.add(User(email=email.lower(), full_name=name, password_hash=hash_password(password), role=UserRole.admin))
        db.commit()
    print(f"Knowledge administrator {email} created.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-admin", help="Create a knowledge-base administrator account")
    c.add_argument("--email", required=True)
    c.add_argument("--name", default="Knowledge Administrator")
    args = parser.parse_args()
    if args.cmd == "create-admin":
        password = getpass.getpass("Password (8+ characters): ")
        if len(password) < 8 or len(password.encode()) > 72:
            sys.exit("Password must be 8-72 characters.")
        create_admin(args.email, args.name, password)


if __name__ == "__main__":
    main()
