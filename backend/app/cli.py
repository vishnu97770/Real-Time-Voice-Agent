"""Administration from the command line.

    python -m app.cli create-user alice@example.com --role admin
    python -m app.cli list-users
    python -m app.cli disable-user alice@example.com
    python -m app.cli set-password alice@example.com
    python -m app.cli check-brain
    python -m app.cli seed-demo
    python -m app.cli import-customers bank customers.json

`import-customers` reads a JSON list of {"ref", "display_name", "data"}; each
"data" must have the shape of that profile's demo data (see app/profiles/).

There is no default account: nobody can sign in until you create one here.
The password is prompted for (never passed on the command line, so it stays out
of shell history). For scripts, set VOICE_AGENT_NEW_PASSWORD instead.
"""

import argparse
import getpass
import json
import os
import sys

from app.auth import ROLES, Auth
from app.config import get_settings
from app.db import Repository
from app.profiles import PROFILES


def read_password() -> str:
    password = os.environ.get("VOICE_AGENT_NEW_PASSWORD")

    if password:
        return password

    first = getpass.getpass("Password (12+ characters): ")

    if first != getpass.getpass("Repeat password: "):
        raise SystemExit("The passwords did not match.")

    return first


def check_brain() -> int:
    """Listing a model is not the same as being allowed to use it (retired models and
    blocked projects both still appear in the list), so make a real call."""
    import asyncio
    import time

    from app.brains import build_brain

    settings = get_settings()
    brain = build_brain(settings)

    if brain is None:
        print("No GEMINI_API_KEY is set, so the server runs with no LLM (the browser uses its local runtime).")
        return 1

    from google.genai import types

    async def probe() -> None:
        started = time.time()
        stream = await brain.client.aio.models.generate_content_stream(
            model=brain.model,
            contents="Reply with the single word: ready",
            config=types.GenerateContentConfig(max_output_tokens=20),
        )
        first = None
        text = ""

        async for chunk in stream:
            for candidate in chunk.candidates or []:
                for part in (candidate.content.parts if candidate.content else []) or []:
                    if part.text and not part.thought:
                        first = first or time.time() - started
                        text += part.text

        print(f"OK: {brain.model} answered {text.strip()!r}, first word after {int((first or 0) * 1000)} ms")

    try:
        asyncio.run(probe())
    except Exception as error:
        print(f"FAILED with model {brain.model}: {str(error)[:400]}")
        print("A 404 usually means the model was retired: set GEMINI_MODEL to a current one.")
        print("A 403 'denied access' means Google has blocked the key's project: fix it in your Google account.")
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description=__doc__.split("\n\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-user", help="add someone who can sign in")
    create.add_argument("email")
    create.add_argument("--role", choices=ROLES, default="operator")
    commands.add_parser("list-users", help="show who can sign in")
    disable = commands.add_parser("disable-user", help="stop someone signing in, immediately")
    disable.add_argument("email")
    commands.add_parser("check-brain", help="make one tiny call to the configured LLM and report what happens")
    commands.add_parser("seed-demo", help="load each profile's demo customer as ref 'demo'")
    imp = commands.add_parser("import-customers", help="load customers for a profile from a JSON file")
    imp.add_argument("profile_id")
    imp.add_argument("file")
    reset = commands.add_parser("set-password", help="change someone's password and sign them out everywhere")
    reset.add_argument("email")

    args = parser.parse_args(argv)
    repo = Repository(get_settings().database_url)
    auth = Auth(repo, get_settings())

    try:
        if args.command == "check-brain":
            return check_brain()

        if args.command == "create-user":
            user = auth.create_user(args.email, read_password(), args.role)
            print(f"Created {user['role']} {user['email']}")

        elif args.command == "seed-demo":
            for profile in PROFILES.values():
                repo.upsert_customer(profile.id, "demo", profile.demo_name or profile.name, profile.data)
                print(f"Loaded {profile.id}/demo ({profile.demo_name})")

        elif args.command == "import-customers":
            profile = PROFILES.get(args.profile_id)

            if profile is None:
                raise SystemExit(f"Unknown profile {args.profile_id}. Choose from: {', '.join(PROFILES)}")

            with open(args.file, encoding="utf-8") as handle:
                rows = json.load(handle)

            # Check everything first so a bad file loads nothing.
            for index, row in enumerate(rows):
                problems = profile.check_customer_data(row.get("data"))

                if problems or not row.get("ref") or not row.get("display_name"):
                    raise SystemExit(f"Row {index} ({row.get('ref')}): {'; '.join(problems) or 'needs ref and display_name'}")

            for row in rows:
                repo.upsert_customer(profile.id, row["ref"], row["display_name"], row["data"])

            print(f"Loaded {len(rows)} customers into {profile.id}")

        elif args.command == "list-users":
            for user in repo.list_users():
                print(f"{user['email']:40} {user['role']:9} {'disabled' if user['disabled'] else 'active'}")

        else:
            user = repo.get_user_by_email(args.email)

            if user is None:
                raise SystemExit(f"No user {args.email}")

            if args.command == "disable-user":
                repo.update_user(user["id"], disabled=1)
                repo.delete_user_sessions(user["id"])
                print(f"Disabled {user['email']}")
            else:
                from app.security import hash_password

                repo.update_user(user["id"], password_hash=hash_password(read_password()))
                repo.delete_user_sessions(user["id"])
                print(f"Password changed for {user['email']}; they are signed out everywhere")
    except ValueError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        if "UNIQUE" in str(error):
            print(f"Error: {args.email} already exists", file=sys.stderr)
            return 1
        raise

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
