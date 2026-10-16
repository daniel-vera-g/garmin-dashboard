#!/usr/bin/env python3
"""One-time interactive Garmin Connect login.

You type your own credentials here — they are never stored by this script.
Only the resulting OAuth tokens are saved (to ~/.garminconnect), so every
later sync runs without a password or MFA prompt.
"""
import sys
from getpass import getpass
from pathlib import Path

import settings

from garminconnect import Garmin

TOKENSTORE = str(Path(settings.load_athlete()["sync"]["tokenstore"]).expanduser())


def main() -> int:
    print("Garmin Connect login")
    print("-" * 46)
    print("Credentials go straight to Garmin and are not saved to disk.\n")

    email = input("Garmin email: ").strip()
    if not email:
        print("No email entered, aborting.")
        return 1
    password = getpass("Garmin password (hidden): ")
    if not password:
        print("No password entered, aborting.")
        return 1

    def prompt_mfa() -> str:
        return input("\nMFA code from your authenticator/email: ").strip()

    print("\nAuthenticating...")
    try:
        client = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
        client.login(tokenstore=TOKENSTORE)
    except Exception as exc:
        print(f"\nLogin failed: {type(exc).__name__}: {exc}")
        print("\nIf this says the credentials are wrong, check that you can sign in")
        print("at connect.garmin.com with the same email and password.")
        return 1
    finally:
        del password

    try:
        name = client.get_full_name()
    except Exception:
        name = "(name unavailable)"

    print(f"\nLogged in as: {name}")
    print(f"Tokens saved to: {TOKENSTORE}")
    print("\nDone. You can now run ./run.sh to sync and build the dashboard.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
