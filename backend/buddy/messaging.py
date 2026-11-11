"""Messages: reply to WhatsApp (and other chat apps) from your own account, through the phone's notification.

How: when a WhatsApp, Messages, Telegram or Signal notification arrives, the Buddy app keeps that chat's "Reply"
action (the same one you use from the notification shade). "Tell Neha I'll be 10 minutes late" -> Buddy finds the
chat, shows the exact text, and sends it only after the user's yes. Free: no WhatsApp Business account, no Meta
fees; it works for chats with a recent notification on the phone.

Auto-replies, only by the user's own rules: for a named contact (or anyone saved in Buddy's people), in a
situation (driving, in a meeting, quiet hours, or always), with the user's own text, signed as Buddy. Never to
groups or unknown numbers, at most once per chat every 2 hours, and the user is told each time. Buddy never makes
up an answer on the user's behalf without a yes: for anything beyond the user's own away-message, it drafts a
reply and asks.

Memories: chat messages feed the brain like emails do (connect.py): promises with dates, plans, and facts about
people, read by Claude's quick model, never a free model.
"""

import json
import logging
import re
from datetime import datetime, timedelta, timezone

from .memory import Memory
from .tools import ToolError

log = logging.getLogger(__name__)

CHAT_APPS = {"com.whatsapp": "WhatsApp", "com.whatsapp.w4b": "WhatsApp Business",
             "com.google.android.apps.messaging": "Messages", "com.samsung.android.messaging": "Messages",
             "org.telegram.messenger": "Telegram", "org.thoughtcrime.securesms": "Signal"}
SITUATIONS = ("driving", "meeting", "quiet", "always")
AUTO_GAP_HOURS = 2
GROUP = re.compile(r"(?i)(\(\d+ messages?\)|\bgroup\b|:\s|@)")  # "Family: Amma", "Team (3 messages)"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS msg_autoreply (
    id INTEGER PRIMARY KEY,
    who TEXT NOT NULL,              -- a contact's name as it shows in the chat, or '*people' (anyone in Buddy's people)
    situation TEXT NOT NULL,        -- driving | meeting | quiet | always
    text TEXT NOT NULL,             -- the user's own words; {name} becomes the contact's first name
    created_at TEXT NOT NULL,
    UNIQUE (who, situation)
);
CREATE TABLE IF NOT EXISTS msg_autoreply_log (
    chat TEXT NOT NULL, at TEXT NOT NULL, text TEXT NOT NULL
);
"""


class Messages:
    def __init__(self, memory: Memory):
        self.memory = memory
        memory.db.executescript(SCHEMA_SQL)

    def recent_chats(self, hours: int = 24) -> list[dict]:
        """Chats with a recent notification that can be replied to (newest first)."""
        since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            rows = self.memory.db.execute(
                "SELECT title, body, data, start_at FROM records WHERE kind = 'notification' AND start_at >= ? "
                "ORDER BY start_at DESC LIMIT 400", (since,)).fetchall()
        except Exception:
            return []
        out, seen = [], set()
        for r in rows:
            meta = json.loads(r["data"] or "{}")
            if meta.get("app") not in CHAT_APPS or not meta.get("can_reply"):
                continue
            chat = r["title"].split(": ", 1)[-1] if ": " in r["title"] else r["title"]
            key = (meta["app"], chat.lower())
            if key in seen:
                continue
            seen.add(key)
            out.append({"app": meta["app"], "app_name": CHAT_APPS[meta["app"]], "chat": chat,
                        "last": " ".join((r["body"] or "").split("] ", 1)[-1].split())[:200],
                        "at": r["start_at"], "group": bool(GROUP.search(chat))})
        return out

    def find_chat(self, who: str, app: str = "") -> dict:
        who_l = who.lower().strip()
        chats = [c for c in self.recent_chats(72) if not app or c["app_name"].lower().startswith(app.lower())]
        exact = [c for c in chats if c["chat"].lower() == who_l]
        part = [c for c in chats if who_l and (who_l in c["chat"].lower() or c["chat"].lower().split()[0] == who_l)]
        found = exact or part or self._via_contacts(who_l, chats)
        if not found:
            raise ToolError(f"No recent {app or 'chat'} notification from {who} to reply to. Replies work through the "
                            "phone's notification, so it needs a message from them in the last few days.")
        return found[0]

    def _via_contacts(self, who_l: str, chats: list[dict]) -> list[dict]:
        """"Reply to Priya" when the chat shows her full name ("Priya Sharma") or only her number: the phone's
        contacts (synced with the owner's permission) say who is who."""
        from . import contacts  # noqa: PLC0415
        try:
            people = contacts.find(self.memory, who_l)
        except Exception:
            return []
        names = {p["name"].lower() for p in people}
        numbers = {n for p in people for n in p["numbers"]}
        return [c for c in chats if c["chat"].lower() in names or (
            len(contacts.key(c["chat"])) >= 7 and contacts.key(c["chat"]) in numbers)]

    def _contact_name(self, chat: str) -> str:
        """The phone contact's name for a chat that shows only a number (or the chat's own name)."""
        from . import contacts  # noqa: PLC0415
        if len(contacts.key(chat)) >= 7:
            try:
                name, how = contacts.name_for(self.memory, chat)
                if name and how == "contact":
                    return name
            except Exception:
                pass
        return chat

    # auto-reply rules ---------------------------------------------------------------------------------------
    def add_rule(self, who: str, situation: str, text: str) -> str:
        if situation not in SITUATIONS:
            raise ToolError("When: driving, meeting, quiet (hours) or always.")
        text = " ".join(str(text or "").split())[:300]
        if len(text) < 5:
            raise ToolError("What should the reply say?")
        who = " ".join(str(who or "").split())[:60] or "*people"
        self.memory.db.execute("INSERT OR REPLACE INTO msg_autoreply (who, situation, text, created_at) VALUES "
                               "(?, ?, ?, ?)", (who, situation, text, datetime.now(timezone.utc).isoformat()))
        self.memory.db.commit()
        whom = "anyone saved in your people" if who == "*people" else who
        return (f"Auto-reply on for {whom} when {situation if situation != 'quiet' else 'in quiet hours'}: \"{text}\" "
                "(signed as Buddy). Never to groups or unknown numbers, once per chat every 2 hours, and I'll tell "
                "you each time.")

    def rules(self) -> list[dict]:
        return [dict(r) for r in self.memory.db.execute("SELECT * FROM msg_autoreply ORDER BY id")]

    def remove_rule(self, who: str = "", situation: str = "") -> str:
        q, args = "DELETE FROM msg_autoreply WHERE 1=1", []
        if who:
            q, args = q + " AND lower(who) = lower(?)", args + [who]
        if situation:
            q, args = q + " AND situation = ?", args + [situation]
        n = self.memory.db.execute(q, args).rowcount
        self.memory.db.commit()
        return f"Removed {n} auto-repl{'ies' if n != 1 else 'y'}." if n else "No auto-reply like that."

    def _known_person(self, chat: str) -> bool:
        if self._contact_name(chat) != chat:  # a number that's in the phone's contacts
            return True
        try:
            from . import contacts  # noqa: PLC0415
            if any(p["name"].lower() == chat.lower() and p["how"] == "contact"
                   for p in contacts.find(self.memory, chat)):
                return True
        except Exception:
            pass
        try:
            first = chat.split()[0].lower()
            return self.memory.db.execute("SELECT 1 FROM entities WHERE kind = 'person' AND (key = ? OR key LIKE ?)",
                                          (chat.lower(), f"{first}%")).fetchone() is not None
        except Exception:
            return False

    def auto_reply_for(self, msg: dict, situation: dict) -> str:
        """The text to send now for an incoming chat message, or '' (no rule, a group, too soon, not the situation)."""
        app, chat = msg.get("app", ""), str(msg.get("title") or "").strip()
        if app not in CHAT_APPS or not msg.get("can_reply") or not chat or GROUP.search(chat):
            return ""
        now = datetime.now(timezone.utc)
        recent = self.memory.db.execute("SELECT 1 FROM msg_autoreply_log WHERE chat = ? AND at > ?",
                                        (chat.lower(), (now - timedelta(hours=AUTO_GAP_HOURS)).isoformat())).fetchone()
        if recent:
            return ""
        state = {"driving": situation.get("driving"), "meeting": bool(situation.get("in_meeting")),
                 "quiet": situation.get("quiet"), "always": True}
        name = self._contact_name(chat)  # a chat that shows only a number answers to the contact's name
        for r in self.rules():
            who_ok = any(r["who"].lower() == n.lower() or r["who"].lower() == n.split()[0].lower()
                         for n in {chat, name}) or (r["who"] == "*people" and self._known_person(chat))
            if who_ok and state.get(r["situation"]):
                first = name.split()[0]
                return r["text"].replace("{name}", first) + " (sent by Buddy, my assistant)"
        return ""

    def log_auto(self, chat: str, text: str) -> None:
        self.memory.db.execute("INSERT INTO msg_autoreply_log (chat, at, text) VALUES (?, ?, ?)",
                               (chat.lower(), datetime.now(timezone.utc).isoformat(), text[:300]))
        self.memory.db.execute("DELETE FROM msg_autoreply_log WHERE at < ?",
                               ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(),))
        self.memory.db.commit()


MESSAGE_TOOL = {
    "name": "messages",
    "description": "Reply to WhatsApp, Messages, Telegram or Signal from the user's own account, through the phone's "
    "notification (free; works for chats with a recent notification). chats: recent chats you can reply to. "
    "reply (to: the contact's name, matched against the phone's contacts too, text, app?): Buddy shows the exact text and sends only after the user's yes. "
    "auto_reply (who: a contact, or empty for anyone in the user's people; when: driving | meeting | quiet | always; "
    "text: the user's own words, {name} for their first name): a standing away-message, only when the user asks for "
    "one; never to groups or unknown numbers. auto_replies (list), stop_auto_reply (who?, when?). Never invent a "
    "reply's content on the user's behalf: draft it and ask.",
    "input_schema": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["chats", "reply", "auto_reply", "auto_replies", "stop_auto_reply"]},
        "to": {"type": "string"}, "text": {"type": "string"}, "app": {"type": "string"},
        "who": {"type": "string"}, "when": {"type": "string", "enum": list(SITUATIONS)}},
        "required": ["action"], "additionalProperties": False},
}


async def run_tool(memory: Memory, args: dict, phone_request, thrift) -> str:
    msgs = Messages(memory)
    a = args.get("action")
    if a == "chats":
        chats = msgs.recent_chats()
        return json.dumps(chats, ensure_ascii=False) if chats else "No recent chats to reply to."
    if a == "auto_reply":
        return msgs.add_rule(args.get("who") or "", args.get("when") or "", args.get("text") or "")
    if a == "auto_replies":
        return json.dumps(msgs.rules(), ensure_ascii=False) or "[]"
    if a == "stop_auto_reply":
        return msgs.remove_rule(args.get("who") or "", args.get("when") or "")
    if a != "reply":
        raise ToolError("Unknown action.")
    text = " ".join(str(args.get("text") or "").split())[:1000]
    if not text:
        raise ToolError("What should the reply say?")
    chat = msgs.find_chat(str(args.get("to") or ""), str(args.get("app") or ""))
    if phone_request is None:
        raise ToolError("The Buddy app isn't connected right now.")

    async def send() -> str:
        got = await phone_request({"type": "reply", "app": chat["app"], "chat": chat["chat"], "text": text})
        if got.get("ok"):
            return f"Sent to {chat['chat']} on {chat['app_name']}."
        return f"Couldn't send: {got.get('error') or 'the phone did not confirm'}."
    return thrift.ask_action(f"Send to {chat['chat']} on {chat['app_name']}: \"{text}\"?", send)
