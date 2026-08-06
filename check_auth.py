"""Offline check of credentials and OAuth tokens.

    python check_auth.py

Reads only local files - no network, no MCP servers, runs instantly. Use this first
when something breaks, since an expired token is the usual cause. Exits 1 if anything
is wrong, so it also works in a batch file.

Tokens expire after 7 days while the Google consent screen is in Testing mode, and
Gmail and Calendar were authorised separately, so they expire on different days.
"""

import json
import os
import sys
import time

HOME = os.path.expanduser("~")
OAUTH_KEYS = os.path.join(HOME, ".gmail-mcp", "gcp-oauth.keys.json")
GMAIL_TOKEN = os.path.join(HOME, ".gmail-mcp", "credentials.json")
CALENDAR_TOKEN = os.path.join(HOME, ".config", "google-calendar-mcp", "tokens.json")

REQUIRED_ENV = ["OPENAI_API_KEY", "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]

failures = []


def ok(msg):
    print("  [OK]   " + msg)


def bad(msg, fix=None):
    print("  [FAIL] " + msg)
    if fix:
        print("         fix: " + fix)
    failures.append(msg)


def warn(msg):
    print("  [WARN] " + msg)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def describe_expiry(token, path):
    """Report access-token and refresh-token expiry for one grant."""
    expiry_ms = token.get("expiry_date")
    if expiry_ms:
        mins = (expiry_ms / 1000 - time.time()) / 60
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(expiry_ms / 1000))
        if mins > 0:
            ok("access token valid for %d min (until %s)" % (mins, when))
        else:
            # Not a failure. The client refreshes this automatically on next use.
            warn("access token expired at %s - will auto-refresh" % when)

    # Google does not record when the refresh token was issued, so approximate it from
    # the token file's mtime. Close enough to warn before the 7-day Testing-mode cutoff.
    lifetime = token.get("refresh_token_expires_in")
    if lifetime:
        deadline = os.path.getmtime(path) + lifetime
        days = (deadline - time.time()) / 86400
        when = time.strftime("%Y-%m-%d", time.localtime(deadline))
        if days <= 0:
            bad(
                "refresh token expired around %s - all calls will fail" % when,
                "re-run the auth flow for this server (see README notes below)",
            )
        elif days < 2:
            warn("refresh token expires in %.1f days (around %s) - re-auth soon" % (days, when))
        else:
            ok("refresh token good for %.1f more days (until ~%s)" % (days, when))

    if not token.get("refresh_token"):
        bad(
            "no refresh token stored - every session will need a fresh browser login",
            "re-run auth and make sure you complete the consent screen",
        )


print("\n== .env ==")
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    warn("python-dotenv not installed, reading process environment only")

for key in REQUIRED_ENV:
    val = os.environ.get(key, "").strip()
    if val:
        ok("%s is set (ends ...%s)" % (key, val[-6:]))
    else:
        bad("%s is missing" % key, "add it to .env in the project root")

print("\n== OAuth client (%s) ==" % OAUTH_KEYS)
if not os.path.exists(OAUTH_KEYS):
    bad(
        "OAuth client file not found",
        "download the Desktop-app JSON from Google Cloud Console > Credentials",
    )
else:
    try:
        keys = load(OAUTH_KEYS)
        kind = "installed" if "installed" in keys else ("web" if "web" in keys else None)
        if kind == "installed":
            inner = keys["installed"]
            ok("desktop-app client, project '%s'" % inner.get("project_id", "?"))
            ok("client id ends ...%s" % inner.get("client_id", "")[-24:])
        elif kind == "web":
            bad(
                "this is a 'web' OAuth client, the servers need a Desktop-app client",
                "create a new OAuth client of type 'Desktop app' and download that JSON",
            )
        else:
            bad("unrecognised OAuth client file, expected an 'installed' key")
    except (ValueError, OSError) as e:
        bad("could not read OAuth client file: %s" % e)

print("\n== Gmail grant (%s) ==" % GMAIL_TOKEN)
if not os.path.exists(GMAIL_TOKEN):
    bad(
        "no Gmail token found",
        "npx @gongrzhe/server-gmail-autoauth-mcp auth",
    )
else:
    try:
        tok = load(GMAIL_TOKEN)
        scopes = (tok.get("scope") or "").split()
        for s in scopes:
            ok("scope " + s)
        if not any("gmail" in s for s in scopes):
            bad("no Gmail scope in this grant")
        describe_expiry(tok, GMAIL_TOKEN)
    except (ValueError, OSError) as e:
        bad("could not read Gmail token: %s" % e)

print("\n== Calendar grant (%s) ==" % CALENDAR_TOKEN)
if not os.path.exists(CALENDAR_TOKEN):
    bad(
        "no Calendar token found",
        'set GOOGLE_OAUTH_CREDENTIALS to the file above, then: '
        "npx @cocal/google-calendar-mcp auth",
    )
else:
    try:
        accounts = load(CALENDAR_TOKEN)
        if not accounts:
            bad("token file is empty, no account authorised")
        for name, tok in accounts.items():
            print("  -- account '%s' --" % name)
            scopes = (tok.get("scope") or "").split()
            for s in scopes:
                ok("scope " + s)
            if not any("calendar" in s for s in scopes):
                bad("no Calendar scope in this grant")
            describe_expiry(tok, CALENDAR_TOKEN)
    except (ValueError, OSError) as e:
        bad("could not read Calendar token: %s" % e)

print("\n" + "=" * 60)
if failures:
    print("FAILED - %d problem(s):" % len(failures))
    for f in failures:
        print("  - " + f)
    print("\nRe-auth commands (PowerShell):")
    print('  npx @gongrzhe/server-gmail-autoauth-mcp auth')
    print('  $env:GOOGLE_OAUTH_CREDENTIALS="%s"; npx @cocal/google-calendar-mcp auth' % OAUTH_KEYS)
    sys.exit(1)

print("All credential checks passed. Run check_tools.py to test the servers live.")
sys.exit(0)
