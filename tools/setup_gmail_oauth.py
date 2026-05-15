"""Interactive Gmail OAuth setup — run once to obtain send-as token.

Usage:
    python tools/setup_gmail_oauth.py

This opens a browser so you can grant the app permission to send
emails via Gmail API on your behalf.  The token is saved to
var/gmail_token.json and refreshed automatically thereafter.

Prerequisites:
    pip install google-auth-oauthlib google-api-python-client
    Place your OAuth client secret at var/gmail_client_secret.json
"""

from __future__ import annotations

from pathlib import Path

from googleapiclient.discovery import build

from tools.gmail_sender import _get_creds

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOKEN_PATH = _REPO_ROOT / "var" / "gmail_token.json"
_CLIENT_SECRET_PATH = _REPO_ROOT / "var" / "gmail_client_secret.json"


def main() -> None:
    if not _CLIENT_SECRET_PATH.is_file():
        print(f"[ERROR] OAuth client secret not found at: {_CLIENT_SECRET_PATH}")
        print("Download it from Google Cloud Console → APIs & Services → Credentials")
        print("Choose 'Desktop application' type, then save the JSON to the path above.")
        return

    print("[INFO] Obtaining Gmail API credentials …")
    creds = _get_creds()
    print(f"[OK] Token saved to: {_TOKEN_PATH}")

    # Quick verification
    try:
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        print(f"[OK] Authenticated as: {profile.get('emailAddress', '?')}")
    except Exception as e:
        print(f"[WARN] Could not verify token: {e}")
        print("The token may still work — try sending a test email.")


if __name__ == "__main__":
    main()
