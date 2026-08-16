import { useEffect, useRef, useState } from "react";

import { resetChat, streamChat } from "./api";

/** One bubble on screen.
 *
 * `tools` holds the names of the tools the agent used while writing this reply, in the
 * order it called them - one entry per `tool` event from the server.
 */
export type Message = {
  role: "user" | "assistant";
  text: string;
  tools?: string[];
  error?: boolean;
};

/** A stable id for this tab's conversation.
 *
 * Kept in sessionStorage so a page refresh rejoins the same thread on the server rather
 * than silently starting a new one, while a second tab gets its own.
 */
function conversationId(): string {
  let id = sessionStorage.getItem("conversation-id");
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem("conversation-id", id);
  }
  return id;
}

function ToolChips({ names }: { names: string[] }) {
  return (
    <div className="tools">
      {names.map((name, i) => (
        <span className="tool" key={i}>
          {name}
        </span>
      ))}
    </div>
  );
}

function Bubble({ message, streaming }: { message: Message; streaming?: boolean }) {
  return (
    <div className={`row ${message.role}`}>
      <div className={`bubble ${message.role} ${message.error ? "error" : ""}`}>
        {message.tools?.length ? <ToolChips names={message.tools} /> : null}
        {message.text}
        {/* A blinking block while the reply is still being written. */}
        {streaming && <span className="caret" />}
      </div>
    </div>
  );
}

export default function App() {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const cid = useRef(conversationId()).current;

  // Keep the newest message in view as the list grows.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  /** Rewrite the reply being streamed. Every event lands through here. */
  function updateReply(change: (reply: Message) => Message) {
    setMessages((prev) => {
      const next = [...prev];
      next[next.length - 1] = change(next[next.length - 1]);
      return next;
    });
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    setBusy(true);

    // The question, plus an empty reply for the events to fill in.
    setMessages((m) => [...m, { role: "user", text }, { role: "assistant", text: "" }]);

    try {
      for await (const event of streamChat(text, cid)) {
        if (event.type === "token") {
          updateReply((r) => ({ ...r, text: r.text + event.text }));
        } else if (event.type === "tool") {
          updateReply((r) => ({ ...r, tools: [...(r.tools ?? []), event.name] }));
        } else if (event.type === "error") {
          updateReply((r) => ({ ...r, text: event.message, error: true }));
        }
        // "done" needs no handling - the loop ends when the stream closes.
      }
    } catch (e) {
      // The request never got off the ground: backend down, or still booting.
      const message = e instanceof Error ? e.message : String(e);
      updateReply((r) => ({ ...r, text: `Could not reach the assistant. ${message}`, error: true }));
    } finally {
      // A turn that used only tools, or was cut short, would otherwise leave a blank bubble.
      updateReply((r) => (r.text ? r : { ...r, text: "(no reply)" }));
      setBusy(false);
    }
  }

  function reset() {
    setMessages([]);
    setInput("");
    resetChat(cid);
  }

  return (
    <div className="app">
      <header>
        <h1>Personal Assistant</h1>
        <span className="sub">Gmail, Calendar and weather · read-only</span>
        <button className="ghost" onClick={reset}>
          New chat
        </button>
      </header>

      <main>
        {messages.length === 0 && (
          <p className="empty">Ask about your mail, your calendar, or the weather.</p>
        )}
        {messages.map((m, i) => (
          <Bubble
            key={i}
            message={m}
            streaming={busy && i === messages.length - 1 && m.role === "assistant"}
          />
        ))}
        {busy && <div className="thinking">thinking…</div>}
        <div ref={bottom} />
      </main>

      <footer>
        <input
          value={input}
          autoFocus
          placeholder="Ask about your mail, calendar or the weather…"
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <button onClick={send} disabled={!input.trim() || busy}>
          Send
        </button>
      </footer>
    </div>
  );
}
