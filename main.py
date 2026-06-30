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
from langchain_mcp_adapters.client import MultiServerMCPClient  
from langchain_openai import ChatOpenAI
from langchain.tools import tool
from langchain.agents import create_agent
from dotenv import load_dotenv
import json

load_dotenv()
   
async def Email_agent(user_req):
    # token = get_token()
    client = MultiServerMCPClient(
        {
            "gmail": {
                "transport": "stdio",
                "command": "npx",
                "args": ["@gongrzhe/server-gmail-autoauth-mcp"],
            }
        }
    )
    model=ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0,
    max_tokens=None,
    timeout=None,
    max_retries=2)
    tools = await client.get_tools()
    with open("token.json") as f:
        print("GRANTED SCOPES:", json.load(f).get("scopes"))
    # print("token",token)
    # read_only = {"search_threads", "get_thread", "list_labels", "list_drafts"}
    # tools = [t for t in tools if t.name in read_only]
    system_prompt="""You are a personal assistant that helps the user manage their email and calendar.
                    You have tools to read and search the user's Gmail and read their Google Calendar. Use them to answer the user's requests with accurate, current information — never guess or make up email contents, senders, dates, or events. If you need information, call the appropriate tool to get it.

                    When summarizing email:
                    - Lead with what matters: who it's from, what they want, and anything time-sensitive or requiring a response.
                    - Be concise. Group or prioritize by importance, not just list everything.
                    - Flag anything that looks urgent or has a deadline.

                    When answering calendar questions, state the specific times and dates clearly.

                    If a request is ambiguous (e.g. "reply to John" when there are multiple Johns), ask a clarifying question instead of guessing.

                    You can currently only READ. You cannot send email or create events yet."""
    
    agent = create_agent(
        model,
        tools
    )
    messages=[{"role": "system", "content": system_prompt},{"role": "user", "content": user_req}]
    gmail_response = await agent.ainvoke({"messages":messages})
    for m in gmail_response["messages"]:
        m.pretty_print()


  
user=input("Hi this is your personal assistant what you need today?")
asyncio.run(Email_agent(user))


