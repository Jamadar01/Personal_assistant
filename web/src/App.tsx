import { useEffect, useMemo, useRef, useState } from "react";

import { resetChat, streamChat } from "./api";
import {
  Chat,
  Message,
  groupByDay,
  loadActiveId,
  loadChats,
  newChat,
  saveActiveId,
  saveChats,
  titleFrom,
} from "./chats";

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
  // There is always at least one chat, so `active` below never has to cope with none.
  const [chats, setChats] = useState<Chat[]>(() => {
    const stored = loadChats();
    return stored.length ? stored : [newChat()];
  });
  const [activeId, setActiveId] = useState<string | null>(() => loadActiveId());
  const [input, setInput] = useState("");
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // The chat a reply is currently streaming into, or null when idle. A plain boolean would
  // not survive switching chats mid-turn: the caret has to follow the reply, not the view.
  const [streamingId, setStreamingId] = useState<string | null>(null);
  const busy = streamingId !== null;

  const bottom = useRef<HTMLDivElement>(null);

  // Falling back to the first chat covers a stored id whose chat has since been deleted.
  const active = chats.find((c) => c.id === activeId) ?? chats[0];
  const groups = useMemo(() => groupByDay(chats), [chats]);

  useEffect(() => saveChats(chats), [chats]);
  useEffect(() => saveActiveId(active.id), [active.id]);

  // Keep the newest message in view as the list grows.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [active.messages]);

  /** Rewrite the messages of one specific chat, by id rather than by "the current one". */
  function patchChat(id: string, change: (messages: Message[]) => Message[]) {
    setChats((prev) =>
      prev.map((c) =>
        c.id === id ? { ...c, messages: change(c.messages), updatedAt: Date.now() } : c
      )
    );
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;

    // Captured now: the reply keeps landing in this chat even if the sidebar moves on.
    const id = active.id;
    setInput("");
    setStreamingId(id);

    // The question, plus an empty reply for the events to fill in.
    const question: Message = { role: "user", text };
    const reply: Message = { role: "assistant", text: "" };
    setChats((prev) =>
      prev.map((c) =>
        c.id === id
          ? {
              ...c,
              // The first thing asked names the chat in the sidebar.
              title: c.messages.length === 0 ? titleFrom(text) : c.title,
              messages: [...c.messages, question, reply],
              updatedAt: Date.now(),
            }
          : c
      )
    );

    /** Rewrite the reply being streamed. Every event lands through here. */
    const updateReply = (change: (reply: Message) => Message) =>
      patchChat(id, (messages) => {
        const next = [...messages];
        next[next.length - 1] = change(next[next.length - 1]);
        return next;
      });

    try {
      for await (const event of streamChat(text, id)) {
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
      updateReply((r) => ({ ...r, text: `Could not reach Sara. ${message}`, error: true }));
    } finally {
      // A turn that used only tools, or was cut short, would otherwise leave a blank bubble.
      updateReply((r) => (r.text ? r : { ...r, text: "(no reply)" }));
      setStreamingId(null);
    }
  }

  function startNewChat() {
    const chat = newChat();
    setChats((prev) => [chat, ...prev]);
    setActiveId(chat.id);
    setInput("");
    setSidebarOpen(false);
  }

  function selectChat(id: string) {
    setActiveId(id);
    setSidebarOpen(false);
  }

  function deleteChat(id: string) {
    // Drop the server's copy too, rather than leaving it in memory until the process ends.
    resetChat(id);
    const rest = chats.filter((c) => c.id !== id);
    const next = rest.length ? rest : [newChat()];
    setChats(next);
    if (id === active.id) setActiveId(next[0].id);
  }

  return (
    <div className="app">
      {/* Off-canvas on a phone; the backdrop only exists while it is open. */}
      {sidebarOpen && <div className="backdrop" onClick={() => setSidebarOpen(false)} />}

      <aside className={`sidebar ${sidebarOpen ? "open" : ""}`}>
        <div className="brand">Sara</div>

        <button className="newchat" onClick={startNewChat}>
          New chat
        </button>

        <nav className="chatlist">
          {groups.map((group) => (
            <div className="group" key={group.label}>
              <div className="grouplabel">{group.label}</div>
              {group.chats.map((chat) => (
                <div
                  key={chat.id}
                  className={`chatitem ${chat.id === active.id ? "active" : ""}`}
                >
                  {/* Two buttons side by side rather than one nested in the other, which
                      is invalid HTML and swallows the click on the inner one. */}
                  <button className="chattitle" onClick={() => selectChat(chat.id)}>
                    {chat.title}
                  </button>
                  <button
                    className="del"
                    aria-label={`Delete ${chat.title}`}
                    onClick={() => deleteChat(chat.id)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          ))}
        </nav>

        <div className="sidefoot">Read-only · Gmail, Calendar, weather</div>
      </aside>

      <div className="chat">
        <header>
          <button
            className="burger"
            aria-label="Show chats"
            onClick={() => setSidebarOpen(true)}
          >
            ☰
          </button>
          <h1>{active.messages.length ? active.title : "Sara"}</h1>
          <span className="sub">Gmail, Calendar and weather · read-only</span>
        </header>

        <main>
          {active.messages.length === 0 && (
            <p className="empty">Ask Sara about your mail, your calendar, or the weather.</p>
          )}
          {active.messages.map((m, i) => (
            <Bubble
              key={i}
              message={m}
              streaming={
                streamingId === active.id &&
                i === active.messages.length - 1 &&
                m.role === "assistant"
              }
            />
          ))}
          {streamingId === active.id && <div className="thinking">thinking…</div>}
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
    </div>
  );
}
