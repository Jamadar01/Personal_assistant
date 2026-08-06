"""Live check of the MCP servers and the read-only tool filter.

    python check_tools.py

Starts both MCP servers, lists their tools, verifies the filter in main.py keeps the
right ones and drops every write tool, then makes a few harmless read calls against
the real Google account. Takes ~20s because npx has to boot two node servers.

Nothing here writes to Gmail or Calendar. Exits 1 on any failure.
"""

import ast
import asyncio
import os
import sys

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()

HOME = os.path.expanduser("~")
OAUTH_KEYS = os.path.join(HOME, ".gmail-mcp", "gcp-oauth.keys.json")
MAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")

SERVERS = {
    "gmail": {
        "transport": "stdio",
        "command": "npx",
        "args": ["@gongrzhe/server-gmail-autoauth-mcp"],
    },
    "calendar": {
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@cocal/google-calendar-mcp"],
        "env": {**os.environ, "GOOGLE_OAUTH_CREDENTIALS": OAUTH_KEYS},
    },
}

# What the agent is supposed to end up holding.
EXPECTED = {
    "read_email",
    "search_emails",
    "list_email_labels",
    "list_filters",
    "get_filter",
    "list-calendars",
    "list-events",
    "search-events",
    "get-event",
    "get-freebusy",
    "list-colors",
    "get-current-time",
}

# Anything that sends, changes or deletes. If one of these survives the filter the
# agent is only as read-only as the model's willingness to obey its prompt.
WRITE_TOOLS = {
    "send_email",
    "draft_email",
    "modify_email",
    "delete_email",
    "batch_modify_emails",
    "batch_delete_emails",
    "create_label",
    "update_label",
    "delete_label",
    "get_or_create_label",
    "create_filter",
    "delete_filter",
    "create_filter_from_template",
    "download_attachment",
    "create-event",
    "create-events",
    "update-event",
    "delete-event",
    "respond-to-event",
    "manage-accounts",
}

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


def filter_set_from_main():
    """Pull the read_only set literal out of main.py.

    The filter lives in main.py, so this reads it from source rather than trusting a
    second copy here. If main.py is edited in a way this cannot parse, the check
    degrades to a warning instead of a false failure.
    """
    try:
        with open(MAIN, encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except (OSError, SyntaxError) as e:
        warn("could not parse main.py (%s), comparing against this file's list" % e)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "read_only" in names:
                try:
                    return set(ast.literal_eval(node.value))
                except ValueError:
                    warn("read_only in main.py is not a plain literal, skipping drift check")
                    return None
    warn("no read_only set found in main.py - is the filter still there?")
    return None


# These servers report failures as ordinary text rather than raising, so a call can
# "succeed" while returning 'Error: invalid_grant'. Match the signatures explicitly -
# a plain substring search for "error" would trip over any email whose subject
# contains the word.
ERROR_MARKERS = (
    "invalid_grant",
    "invalid_client",
    "unauthorized_client",
    "insufficient permission",
    "insufficientpermissions",
    "access_denied",
    "-32600",
    "not authenticated",
    "no authenticated accounts",
    "authentication required",
)


def looks_like_error(text):
    low = text.lower()
    if any(m in low for m in ERROR_MARKERS):
        return True
    # The adapter wraps content blocks, so an error surfaces as "'text': 'Error: ...'".
    return "'text': 'error" in low or low.lstrip().startswith("error:")


async def smoke(tools, name, args, label):
    """Call one read-only tool and report whether it came back with real data."""
    if name not in tools:
        bad("%s is not available, cannot smoke test" % name)
        return
    try:
        result = await tools[name].ainvoke(args)
        text = str(result)
        if not text.strip():
            bad("%s returned nothing" % name)
        elif looks_like_error(text):
            bad(
                "%s returned an error: %s" % (name, text[:200].replace("\n", " ")),
                "if it mentions invalid_grant the token expired - run: python check_auth.py",
            )
        else:
            ok("%s -> %s" % (label, text[:160].replace("\n", " ")))
    except Exception as e:  # noqa: BLE001 - any failure here is a real finding
        bad("%s raised %s: %s" % (name, type(e).__name__, e))


async def main():
    print("\n== starting MCP servers (npx, takes a moment) ==")
    client = MultiServerMCPClient(SERVERS)
    try:
        all_tools = await client.get_tools()
    except Exception as e:  # noqa: BLE001
        bad("could not start the MCP servers: %s: %s" % (type(e).__name__, e))
        return
    ok("both servers responded, %d tools exposed in total" % len(all_tools))

    names = {t.name for t in all_tools}

    print("\n== read-only filter ==")
    declared = filter_set_from_main()
    if declared is not None:
        if declared == EXPECTED:
            ok("filter in main.py matches this script's expected list")
        else:
            only_main = sorted(declared - EXPECTED)
            only_here = sorted(EXPECTED - declared)
            warn("filter in main.py has drifted from this script")
            if only_main:
                print("         only in main.py:     %s" % ", ".join(only_main))
            if only_here:
                print("         only in check_tools: %s" % ", ".join(only_here))

    active = declared if declared is not None else EXPECTED
    kept = names & active

    missing = active - names
    if missing:
        bad(
            "filter expects tools the servers do not expose: %s" % ", ".join(sorted(missing)),
            "a server was updated and renamed them - update the read_only set in main.py",
        )
    else:
        ok("all %d allow-listed tools exist on the servers" % len(active))

    leaked = kept & WRITE_TOOLS
    if leaked:
        bad(
            "WRITE TOOLS SURVIVED THE FILTER: %s" % ", ".join(sorted(leaked)),
            "remove them from the read_only set in main.py",
        )
    else:
        ok("no write tool survives the filter")

    dropped = names - kept
    ok("%d tools dropped before the agent sees them" % len(dropped))
    unknown_writes = dropped - WRITE_TOOLS - active
    if unknown_writes:
        warn(
            "dropped tools this script does not know about: %s"
            % ", ".join(sorted(unknown_writes))
        )
        print("         (harmless - they are dropped - but worth a look if you want them)")

    print("\n== live read calls ==")
    tools = {t.name: t for t in all_tools if t.name in kept}
    await smoke(tools, "get-current-time", {}, "current time")
    await smoke(tools, "list-calendars", {}, "calendars")
    await smoke(
        tools,
        "search_emails",
        {"query": "newer_than:7d", "maxResults": 1},
        "recent mail",
    )


asyncio.run(main())

print("\n" + "=" * 60)
if failures:
    print("FAILED - %d problem(s):" % len(failures))
    for f in failures:
        print("  - " + f)
    print("\nIf these look like auth problems, run: python check_auth.py")
    sys.exit(1)

print("All tool checks passed. The agent is wired up and read-only.")
sys.exit(0)
