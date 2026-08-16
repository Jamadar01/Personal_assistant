# Roadmap

Building a React chat UI on top of the agent, then deploying it for **one user** (me).
Single-user is a deliberate scope choice - see [Why single-user](#why-single-user).

## Done

| Step | What | Files |
|---|---|---|
| 1 | FastAPI backend, one non-streaming `/api/chat` | `server.py` |
| 2 | SSE streaming - `token` / `tool` / `error` / `done` events | `server.py` |
| 3 | React scaffold, plain CSS, fake messages | `web/` |
| 4 | Wired React to the SSE stream | `web/src/api.ts`, `web/src/App.tsx` |

`app.py` (Gradio) still works and shares `build_agent()` from `main.py`. It is a second
front end, not a dependency - delete it once React is doing everything.

### Not yet verified

Nothing below step 2 has been run end to end. Check these first:

- [ ] Tokens actually stream, rather than all landing at once when the turn ends.
      If they land at once, `gpt-4o-mini` is not streaming through `astream` - a backend
      problem, not a frontend one.
- [ ] Sending a message before `Assistant ready.` prints returns 503 and shows a red
      bubble. Correct, but ugly.
- [ ] A turn that calls several tools shows each one as a chip, in call order.

## Step 5 - deploy

The remaining build work. Roughly one sitting.

1. **Serve the built frontend from FastAPI.** Mount `web/dist` at `/` *after* the `/api`
   routes so it does not shadow them:
   ```python
   app.mount("/", StaticFiles(directory="web/dist", html=True), name="web")
   ```
   One process, one port, no CORS. The `/api` paths in `api.ts` already work unchanged.
2. **Password gate.** Read `APP_PASSWORD` from the environment; if set, require it on
   every `/api` request and unset means open (which is what you want on localhost and
   nowhere else). Without this, anyone who finds the URL reads my inbox and spends my
   OpenAI credit.
3. **Dockerfile.** Needs **both** Python and Node in the runtime image - the MCP servers
   are `npx` subprocesses (`main.py:161`), so a `python:3.14-slim` base alone will not
   boot. Multi-stage: build `web/` with a Node image, copy `dist` into the Python image,
   install Node in it.
4. **Mount the OAuth files.** `~/.gmail-mcp/gcp-oauth.keys.json` and `token.json` are on
   my laptop, not in the image, and must never be committed. Secret or mounted volume.
5. **Deploy.** `replicas: 1`, `strategy: Recreate`, memory limit ~1GB.

### Why single-replica

Three things in the design assume one process, and all three break silently with two:

- `CONVERSATIONS` is a process-local dict - turn 2 hitting a different pod answers with
  no memory of turn 1.
- The MCP servers are stdio children of *this* process. They cannot be shared.
- `TURN_LOCK` only serializes within one process.

Not a compromise for one user. It is a hard limit to remember if that ever changes.

### Deployment facts that bite

- **Pods restart more often than expected**: every deploy, OOMKill, node drain, spot
  reclaim. Each restart loses all history and pays the ~20s `npx` cold start again.
- **Scale-to-zero** (Render free tier, Fly `auto_stop_machines`, Cloud Run
  `min-instances=0`) means the app is *designed* to die when idle. Set min-instances to 1
  to avoid the cold start, or accept it and save the money.
- **Google OAuth "Testing" mode expires refresh tokens after 7 days.** The deployed app
  will stop working weekly until the OAuth app is published or made Workspace-internal.
  This is the single most likely reason a working deploy breaks later.

## Backlog

Deliberately skipped so far. Roughly in the order they will start to hurt.

### Worth doing soon

- **Trim history.** `CONVERSATIONS[id]` only ever grows, and it holds whole email bodies
  from `read_email`. Every turn re-sends the lot to the model: rising cost first, context
  window later, OOMKill eventually. Trim or summarise old turns before they go back in.
- **Health check on load.** Poll `/api/health` and disable the input until `ready` is
  true, instead of letting the first message fail with a 503.

### Nice to have

- **Stop button.** Needs `await request.is_disconnected()` back inside `run_turn`,
  otherwise pressing stop only stops the browser *listening* - the server finishes the
  turn anyway and saves it to history.
- **Show tool results, not just names.** The events carry only `name` today. Adding
  `tool_start` / `tool_end` with the arguments, the (truncated) result and a duration
  gives a collapsible panel to check the agent's work against.
- **Markdown rendering.** Replies are plain text in a `white-space: pre-wrap` bubble.
  Lists and bold would read better.
- **History that survives restarts.** Smallest version: persist the dict to SQLite on a
  mounted volume. Proper version: a LangGraph checkpointer, which replaces
  `CONVERSATIONS` entirely. Only the volume mount has to be decided before picking a host.

### Explicitly not doing

- **Multi-user.** Needs per-user OAuth, per-session MCP servers or direct Google API
  calls, a login layer, and rate limiting. `gmail.readonly` is a Google *restricted*
  scope: public external users require app verification plus a CASA security assessment.
  Months, not days.
- **Write access.** The agent is read-only by construction, not just by prompt - the
  write tools are filtered out in `build_agent()` (`main.py:185`) before the model can
  see them. Undoing that means the prompt is the only thing standing between a
  prompt-injected email and a sent reply.
