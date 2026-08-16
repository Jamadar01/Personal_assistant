/** Talking to the FastAPI backend.
 *
 * `/api` is proxied to :8000 in dev by vite.config.ts, and served by FastAPI itself in
 * production, so these paths work unchanged in both.
 */

/** The events server.py sends. Mirrors run_turn() there. */
export type ChatEvent =
  | { type: "token"; text: string }
  | { type: "tool"; name: string }
  | { type: "error"; message: string }
  | { type: "done" };

/** POST a message and yield each event as it arrives.
 *
 * EventSource would be the obvious tool here, but it can only issue GET requests and we
 * need to POST a body - so this reads the response stream by hand instead.
 */
export async function* streamChat(
  message: string,
  conversationId: string
): AsyncGenerator<ChatEvent> {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, conversation_id: conversationId }),
  });

  if (!res.ok || !res.body) {
    // Failures before the stream opens still arrive as ordinary HTTP errors.
    const detail = await res.text().catch(() => "");
    throw new Error(detail || `${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // SSE separates events with a blank line. A network chunk can end mid-event, so
    // the last piece stays in the buffer until its terminator shows up.
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";

    for (const event of events) {
      const line = event.split("\n").find((l) => l.startsWith("data:"));
      if (line) yield JSON.parse(line.slice(5).trim()) as ChatEvent;
    }
  }
}

/** Forget this conversation's history on the server. */
export async function resetChat(conversationId: string): Promise<void> {
  await fetch("/api/reset", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // message is unused by the endpoint but the request model still asks for it.
    body: JSON.stringify({ message: "", conversation_id: conversationId }),
  });
}
