/** Conversation storage, in the browser.
 *
 * The server keys history by conversation id and holds it in a plain dict that dies with
 * the process. It has no idea these chats exist and no endpoint to list them - so the
 * sidebar is built entirely from localStorage, and a chat id is simply handed to the
 * server when a turn runs.
 *
 * The consequence is worth knowing: reopening a chat after the server has restarted shows
 * the messages back, but the server answers that thread with no memory of it. Everything
 * on screen is real; only the server's half is forgotten.
 */

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

export type Chat = {
  id: string;
  title: string;
  messages: Message[];
  updatedAt: number;
};

const CHATS_KEY = "sara-chats";
const ACTIVE_KEY = "sara-active-chat";

/** Replies quote email bodies, so a long history is genuinely large. Old chats are the
 *  first thing to go rather than letting a write fail once the quota is reached. */
const MAX_CHATS = 30;

export function newChat(): Chat {
  return { id: crypto.randomUUID(), title: "New chat", messages: [], updatedAt: Date.now() };
}

/** A chat's name, taken from the first thing asked in it. */
export function titleFrom(text: string): string {
  const clean = text.replace(/\s+/g, " ").trim();
  return clean.length > 42 ? `${clean.slice(0, 42)}…` : clean || "New chat";
}

export function loadChats(): Chat[] {
  try {
    const raw = localStorage.getItem(CHATS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    // Anything could be under that key - a half-written value, or a shape from an older
    // build. A bad read should start the sidebar empty, not break the whole app.
    return Array.isArray(parsed) ? (parsed as Chat[]) : [];
  } catch {
    return [];
  }
}

export function saveChats(chats: Chat[]): void {
  const trimmed = [...chats]
    .sort((a, b) => b.updatedAt - a.updatedAt)
    .slice(0, MAX_CHATS);
  try {
    localStorage.setItem(CHATS_KEY, JSON.stringify(trimmed));
  } catch {
    // Out of quota, or storage blocked entirely. The chat on screen still works; only
    // the record of it is lost, which is not worth interrupting the conversation for.
  }
}

export function loadActiveId(): string | null {
  try {
    return localStorage.getItem(ACTIVE_KEY);
  } catch {
    return null;
  }
}

export function saveActiveId(id: string): void {
  try {
    localStorage.setItem(ACTIVE_KEY, id);
  } catch {
    /* see saveChats */
  }
}

/** Split the list into the headings the sidebar shows, newest group first.
 *
 * Empty groups are dropped, so a first-time sidebar shows one heading rather than three.
 */
export function groupByDay(chats: Chat[]): { label: string; chats: Chat[] }[] {
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const today = startOfToday.getTime();
  const yesterday = today - 86_400_000;

  const groups: Record<string, Chat[]> = { Today: [], Yesterday: [], Earlier: [] };
  for (const chat of [...chats].sort((a, b) => b.updatedAt - a.updatedAt)) {
    if (chat.updatedAt >= today) groups.Today.push(chat);
    else if (chat.updatedAt >= yesterday) groups.Yesterday.push(chat);
    else groups.Earlier.push(chat);
  }

  return Object.entries(groups)
    .filter(([, list]) => list.length > 0)
    .map(([label, list]) => ({ label, chats: list }));
}
