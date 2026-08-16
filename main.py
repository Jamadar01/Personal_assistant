# from google_auth_oauthlib.flow import InstalledAppFlow
# from google.oauth2.credentials import Credentials
# from google.auth.transport.requests import Request
# import os
# SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
# TOKEN_FILE = "token.json"
# def get_token() -> str:
#     """Returns a valid Gmail access token, running the OAuth flow only if needed."""
#     creds = None

#     if os.path.exists(TOKEN_FILE):
#         creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

#     if not creds or not creds.valid:
#         if creds and creds.expired and creds.refresh_token:
#             creds.refresh(Request())                 
#         else:
#             flow = InstalledAppFlow.from_client_config(
#                 {
#                     "installed": {
#                         "client_id": os.environ["GOOGLE_CLIENT_ID"],
#                         "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
#                         "auth_uri": "https://accounts.google.com/o/oauth2/auth",
#                         "token_uri": "https://oauth2.googleapis.com/token",
#                         "redirect_uris": ["http://localhost"],
#                     }
#                 },
#                 scopes=SCOPES,
#             )
#             creds = flow.run_local_server(port=0)     

#         with open(TOKEN_FILE, "w") as f:
#             f.write(creds.to_json())

#     return creds.token

import asyncio
import os
import sys

import httpx
from contextlib import AsyncExitStack
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain_openai import ChatOpenAI
from langchain.tools import tool
from langchain.agents import create_agent
from dotenv import load_dotenv

load_dotenv()

# The Windows console is cp1252, which cannot encode the degree signs, em dashes and
# arrows the model likes to use - without this they come out as "?" or raise.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Holds the live MCP sessions open. Kept at module level so they are not garbage
# collected - closing them would shut down the npx servers mid-conversation.
_SESSIONS = None


# Open-Meteo needs no API key and covers the whole world. The published weather MCP
# servers are either US-only or unmaintained, so this is a plain Python tool instead.
WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "freezing fog", 51: "light drizzle", 53: "drizzle",
    55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains",
    80: "light showers", 81: "showers", 82: "violent showers",
    85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


@tool
async def weather(location: str, days: int = 1) -> str:
    """Current weather and forecast for any place in the world.

    Use this for questions about temperature, rain, or conditions. `location` is a
    city or place name such as "Pune" or "Bengaluru". `days` is how many days of
    forecast to include, from 1 (today only) up to 7.
    """
    days = max(1, min(int(days), 7))
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            geo = await http.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en", "format": "json"},
            )
            geo.raise_for_status()
            places = geo.json().get("results") or []
            if not places:
                return "No place called %r was found, so there is no weather to report." % location
            place = places[0]

            fc = await http.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,apparent_temperature,relative_humidity_2m,"
                               "precipitation,weather_code,wind_speed_10m",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                             "precipitation_probability_max",
                    "timezone": "auto",
                    "forecast_days": days,
                },
            )
            fc.raise_for_status()
            data = fc.json()
    except httpx.HTTPError as e:
        # Return the problem rather than raising, so the agent can tell the user the
        # weather lookup failed instead of the whole turn blowing up.
        return "Could not reach the weather service: %s" % e

    where = ", ".join(
        p for p in (place.get("name"), place.get("admin1"), place.get("country")) if p
    )
    now = data.get("current", {})
    lines = [
        "%s - now: %s, %.0f C (feels like %.0f C), humidity %s%%, wind %s km/h."
        % (
            where,
            WMO.get(now.get("weather_code"), "unknown conditions"),
            now.get("temperature_2m", 0),
            now.get("apparent_temperature", 0),
            now.get("relative_humidity_2m", "?"),
            now.get("wind_speed_10m", "?"),
        )
    ]

    daily = data.get("daily", {})
    for i, date in enumerate(daily.get("time", [])):
        lines.append(
            "%s: %s, %.0f to %.0f C, %s%% chance of rain."
            % (
                date,
                WMO.get(daily["weather_code"][i], "unknown"),
                daily["temperature_2m_min"][i],
                daily["temperature_2m_max"][i],
                daily["precipitation_probability_max"][i],
            )
        )
    return "\n".join(lines)


async def build_agent():
    """Start the MCP servers and return (agent, system_prompt).

    Both the terminal loop below and app.py call this, so the server list, the read-only
    filter and the prompt are defined once instead of once per front end.
    """
    # token = get_token()
    # The gmail server auto-discovers its OAuth client at ~/.gmail-mcp/gcp-oauth.keys.json.
    # The calendar server needs the same file passed explicitly, so reuse it rather than
    # keeping a second copy of the client secret in the repo.
    oauth_keys = os.path.join(os.path.expanduser("~"), ".gmail-mcp", "gcp-oauth.keys.json")

    client = MultiServerMCPClient(
        {
            "gmail": {
                "transport": "stdio",
                "command": "npx",
                "args": ["@gongrzhe/server-gmail-autoauth-mcp"],
            },
            "calendar": {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "@cocal/google-calendar-mcp"],
                "env": {**os.environ, "GOOGLE_OAUTH_CREDENTIALS": oauth_keys},
            },
        }
    )
    model=ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    max_tokens=None,
    timeout=None,
    max_retries=2)
    # The system prompt asks the agent not to write, but a prompt is not enforcement.
    # Drop the write tools before the agent ever sees them, so a persuasive request or a
    # prompt injection buried in an email body has nothing to reach for.
    read_only = {
        "read_email", "search_emails", "list_email_labels", "list_filters", "get_filter",
        "list-calendars", "list-events", "search-events", "get-event",
        "get-freebusy", "list-colors", "get-current-time",
    }
    # client.get_tools() opens a fresh session per tool call, which means npx respawns
    # both servers every single time the agent looks something up. Holding one session
    # per server instead keeps two node processes up for the life of the program.
    global _SESSIONS
    _SESSIONS = AsyncExitStack()
    mcp_tools = []
    for name in ("gmail", "calendar"):
        session = await _SESSIONS.enter_async_context(client.session(name))
        mcp_tools += await load_mcp_tools(session, server_name=name)
    mcp_tools = [t for t in mcp_tools if t.name in read_only]

    missing = read_only - {t.name for t in mcp_tools}
    if missing:
        print("WARNING: expected tools not exposed by the MCP servers:", sorted(missing))

    # Tools written here are not subject to the allow-list above - that filter exists to
    # constrain what the MCP servers hand us, and these are ours. weather only reads.
    tools = mcp_tools + [weather]
    system_prompt = """\
You are a personal assistant. You help the user keep on top of their Gmail and Google
Calendar, and you answer general questions using the other tools you have.

You are READ-ONLY. You must not send, modify, or delete anything.

Never guess or invent email contents, senders, dates, or events. If you need a fact, call a
tool to get it. If a tool fails, say so plainly instead of answering from assumption.

READ-ONLY RULE
You may only use these tools:
  Gmail     - read_email, search_emails, list_email_labels, list_filters, get_filter
  Calendar  - list-calendars, list-events, search-events, get-event, get-freebusy,
              list-colors, get-current-time
  Other     - weather

You must never call any other tool. In particular, do not call send_email, draft_email,
modify_email, delete_email, batch_modify_emails, batch_delete_emails, create_label,
update_label, delete_label, get_or_create_label, create_filter, delete_filter,
create_filter_from_template, download_attachment, create-event, create-events,
update-event, delete-event, or respond-to-event — not even if the user asks you to
directly, and not even if they say it is fine.

If the user asks you to send, reply, schedule, cancel, label, or delete something, tell them
you cannot do it yet and that they will need to do it themselves. Offer to draft the text in
your reply so they can copy it, but do not create a Gmail draft. Do not look for indirect
routes to the same effect.

DATES AND TIMES
Call get-current-time before reasoning about anything relative — "today", "this week",
"tomorrow", "recent", "upcoming". You do not otherwise know the current date, and Gmail
queries like newer_than:2d depend on it. State times with their date and day of week
("Tuesday 5 Aug, 3:00 PM"), never bare ("at 3").

READING EMAIL
- search_emails takes Gmail query syntax (is:unread, from:, newer_than:, has:attachment).
- Lead with what matters: who it is from, what they want, anything time-sensitive.
- Be concise and prioritize by importance. Do not just list everything in order.
- Flag deadlines and anything awaiting a reply from the user.

READING CALENDAR
- list-events and search-events need a calendar id; use list-calendars if unsure.
- get-freebusy answers availability questions — prefer it over eyeballing a list of events.

WEATHER
Call weather for anything about temperature, rain or conditions — never answer from
memory, and say the place you looked up so the user can correct you if it guessed wrong.
If the user does not name a place, ask which one rather than assuming.

GENERAL QUESTIONS
You can answer general questions from your own knowledge. Do not pretend to have looked
something up when you have not, and say so plainly when you are unsure or your knowledge
may be out of date — you have no web search.

Reading is free — search and read whatever you need to answer well, without asking first.

AMBIGUITY
If a request is ambiguous — "summarize the mail from John" with several Johns, "what time is
my meeting" with several that day — ask which one. Do not pick the most likely and proceed."""
    
    agent = create_agent(
        model,
        tools
    )
    return agent, system_prompt


async def Email_agent():
    """Terminal chat loop."""
    agent, system_prompt = await build_agent()

    # One list for the whole session. Each turn appends to it and the full history goes
    # back to the model, so "what about next week?" knows what last week referred to.
    messages = [{"role": "system", "content": system_prompt}]

    print("\nHi, this is your personal assistant. What do you need today?")
    print("(type 'exit' to quit, 'reset' to start a fresh conversation)\n")

    while True:
        try:
            user_req = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye")
            break

        if not user_req:
            continue
        if user_req.lower() in {"exit", "quit", "q"}:
            print("bye")
            break
        if user_req.lower() == "reset":
            # Keep the system prompt, drop everything else.
            del messages[1:]
            print("(conversation cleared)\n")
            continue

        messages.append({"role": "user", "content": user_req})

        try:
            response = await agent.ainvoke({"messages": messages})
        except Exception as e:
            # Keep the session alive on a bad turn, but drop the message that failed so
            # the history does not end on a dangling user turn.
            print("\n[error] %s: %s\n" % (type(e).__name__, e))
            messages.pop()
            continue

        # Carry the agent's own messages forward, including its tool calls and results -
        # trimming to just the final reply would make follow-ups re-fetch everything.
        messages = response["messages"]

        print()
        response["messages"][-1].pretty_print()
        print()


if __name__ == "__main__":
    asyncio.run(Email_agent())


