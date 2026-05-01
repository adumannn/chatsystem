import argparse
import json
import re
import select
import socket
import time

from ollama import Client as OllamaClient
from chat_utils import *


class ChatBotClient:
    """
    Connects to the chat server as a normal user and uses Ollama to
    generate responses.  Maintains per-peer conversation context and
    supports a configurable personality (system prompt).
    """

    def __init__(self, name="chatbot", model="qwen3.5:4b",
                 password="chatbot",
                 personality=None,
                 ollama_host="http://localhost:11434",
                 server_addr=None,
                 timeout=45,
                 think=False,
                 generation_options=None,
                 max_context_messages=30):
        # ── Ollama setup ────────────────────────────────────────────────
        self.ollama = OllamaClient(host=ollama_host, timeout=timeout)
        self.model = model
        self.name = name
        self.password = password
        self.think = think
        self.generation_options = generation_options or {
            "temperature": 0.3,
            "num_predict": 80,
            "num_ctx": 2048,
        }
        self.max_context_messages = max_context_messages

        # ── Personality (system prompt) ─────────────────────────────────
        self.default_personality = (
            f"You are {self.name}, a friendly and helpful AI assistant "
            f"in a chat room. Keep replies concise and conversational."
        )
        self.personality = personality or self.default_personality

        # ── Conversation context per peer / group ───────────────────────
        # key → list[dict]  (OpenAI-style messages)
        self.conversations = {}

        # ── Chat-server networking ──────────────────────────────────────
        self.server_addr = server_addr or SERVER
        self.socket = None
        self.state = S_OFFLINE
        self.peer = ""
        self.peers_in_chat = []

    # ── Ollama helpers ──────────────────────────────────────────────────

    def _system_message(self):
        return (
            self.personality + "\n"
            f"Your chat name is {self.name}. Use prior messages as context. "
            "In group chats, respond when someone addresses you by name or "
            f"mentions @{self.name}; otherwise mostly stay quiet."
        )

    def _get_context(self, peer_key="default"):
        """Return (or create) the message list for *peer_key*."""
        if peer_key not in self.conversations:
            self.conversations[peer_key] = [
                {"role": "system", "content": self._system_message()}
            ]
        return self.conversations[peer_key]

    def _trim_context(self, context):
        keep = self.max_context_messages
        if len(context) > keep + 1:
            del context[1:-keep]

    @staticmethod
    def _message_content(message, from_user="user"):
        return f"{from_user}: {message}" if from_user != "user" else message

    def remember_message(self, message, from_user="user", peer_key="default"):
        context = self._get_context(peer_key)
        context.append({
            "role": "user",
            "content": self._message_content(message, from_user),
        })
        self._trim_context(context)

    def generate_response(self, message, from_user="user", peer_key="default",
                          remember=True):
        """Send *message* to Ollama with full conversation context."""
        context = self._get_context(peer_key)

        if remember:
            self.remember_message(message, from_user, peer_key)

        try:
            response = self.ollama.chat(
                model=self.model,
                messages=context,
                think=self.think,
                options=self.generation_options,
            )
            reply = self._response_content(response)
        except Exception as e:
            reply = f"(Sorry, I hit an error: {e})"

        context.append({"role": "assistant", "content": reply})
        self._trim_context(context)
        return reply

    @staticmethod
    def _response_content(response):
        try:
            return response["message"]["content"]
        except (KeyError, TypeError):
            return response.message.content

    def set_personality(self, new_personality):
        """Change the system prompt and reset all contexts."""
        self.personality = new_personality.strip()
        self.conversations.clear()

    def clear_context(self, peer_key="default"):
        """Forget conversation history for one peer / group."""
        self.conversations.pop(peer_key, None)

    @staticmethod
    def _clean_user_name(name):
        return (name or "user").strip().strip("[]") or "user"

    def _update_chat_members(self, msg):
        from_user = self._clean_user_name(msg.get("from"))
        members = msg.get("members") or []
        if members:
            self.peers_in_chat = [
                member for member in members if member != self.name
            ]
        elif from_user not in self.peers_in_chat:
            self.peers_in_chat.append(from_user)
        self.peer = from_user
        return from_user

    def _peer_key(self):
        if len(self.peers_in_chat) > 1:
            members = sorted(self.peers_in_chat + [self.name])
            return "group:" + ",".join(members)
        return self.peer or "default"

    def _is_group_chat(self):
        return len(self.peers_in_chat) > 1

    def _is_addressed(self, message):
        text = (message or "").lower()
        name = re.escape(self.name.lower())
        return bool(re.search(rf"(^|\W)@?{name}(\W|$)", text))

    def _strip_addressing(self, message):
        text = message or ""
        text = re.sub(
            rf"(^|\s)@?{re.escape(self.name)}(?=$|\s|[:,])[:,]?\s*",
            " ",
            text,
            flags=re.IGNORECASE,
        )
        return " ".join(text.split())

    def _should_respond(self, message, addressed):
        if not self._is_group_chat():
            return True
        if addressed:
            return True
        text = (message or "").lower()
        return "anyone" in text and text.strip().endswith("?")

    def _handle_bot_command(self, message, peer_key, addressed):
        if self._is_group_chat() and not addressed:
            return None

        text = self._strip_addressing(message).strip()
        lower = text.lower()

        if lower in ("personality", "personality?", "what is your personality?"):
            return "My current personality is: " + self.personality

        if lower in ("clear context", "reset context", "forget context"):
            self.clear_context(peer_key)
            return "Context cleared for this conversation."

        if lower in ("reset personality", "default personality"):
            self.set_personality(self.default_personality)
            return "Personality reset to the default."

        personality = None
        for prefix, requires_address in (
            ("personality:", False),
            ("set personality:", False),
            ("set personality to ", False),
            ("personality ", False),
            ("be ", True),
        ):
            if lower.startswith(prefix) and (addressed or not requires_address):
                personality = text[len(prefix):].strip()
                break

        if personality:
            self.set_personality(
                f"You are {self.name}. {personality}"
            )
            return "Personality updated."

        return None

    # ── Chat-server protocol ────────────────────────────────────────────

    def _connect_to_server(self):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.connect(self.server_addr)
        print(f"[{self.name}] Connected to server {self.server_addr}")

    def _login(self):
        mysend(self.socket, json.dumps({
            "action": "login",
            "name": self.name,
            "password": self.password,
        }))
        resp = json.loads(myrecv(self.socket))
        if resp["status"] == "ok":
            self.state = S_LOGGEDIN
            print(f"[{self.name}] Logged in")
            return True
        print(f"[{self.name}] Login failed: {resp['status']}")
        return False

    def _send_chat(self, text):
        """Send an exchange message to the current chat group."""
        mysend(self.socket, json.dumps({
            "action": "exchange",
            "from": "[" + self.name + "]",
            "message": text,
        }))

    def _disconnect_chat(self):
        mysend(self.socket, json.dumps({"action": "disconnect"}))
        self.peer = ""
        self.peers_in_chat = []
        self.state = S_LOGGEDIN
        print(f"[{self.name}] Left the chat")

    # ── Incoming-message handler ────────────────────────────────────────

    def _handle(self, raw):
        if not raw:
            return
        msg = json.loads(raw)

        # ── LOGGEDIN state ──────────────────────────────────────────────
        if self.state == S_LOGGEDIN:
            if msg["action"] == "connect":
                from_user = self._update_chat_members(msg)
                self.state = S_CHATTING
                print(f"[{self.name}] {from_user} started a chat")
                greeting = (
                    f"Hi {from_user}, I'm {self.name}. "
                    "Send me a message and I'll help."
                )
                self._send_chat(greeting)

        # ── CHATTING state ──────────────────────────────────────────────
        elif self.state == S_CHATTING:
            if msg["action"] == "connect":
                # new member joined (group chat)
                new_peer = self._update_chat_members(msg)
                print(f"[{self.name}] {new_peer} joined the chat")
                welcome = f"Hi {new_peer}, welcome to the chat."
                self._send_chat(welcome)

            elif msg["action"] == "exchange":
                from_user = self._clean_user_name(msg["from"])
                message = msg["message"]
                print(f"[{self.name}] {from_user}: {message}")

                peer_key = self._peer_key()
                self.remember_message(message, from_user, peer_key)

                addressed = self._is_addressed(message)
                command_reply = self._handle_bot_command(
                    message,
                    peer_key,
                    addressed,
                )
                if command_reply:
                    self._send_chat(command_reply)
                    return

                if not self._should_respond(message, addressed):
                    return

                # ── generate & send reply ───────────────────────────────
                reply = self.generate_response(
                    self._strip_addressing(message),
                    from_user=from_user,
                    peer_key=peer_key,
                    remember=False,
                )
                self._send_chat(reply)

            elif msg["action"] == "disconnect":
                print(f"[{self.name}] Peer disconnected")
                self.peer = ""
                self.peers_in_chat = []
                self.state = S_LOGGEDIN

    # ── Main loop ───────────────────────────────────────────────────────

    def run(self):
        """Connect, log in, then loop forever handling messages."""
        self._connect_to_server()
        if not self._login():
            return

        print(f"[{self.name}] Bot online  |  model={self.model}")
        print(f"[{self.name}] Personality: {self.personality}")
        self.socket.setblocking(False)

        try:
            while True:
                try:
                    readable, _, _ = select.select([self.socket], [], [],
                                                   CHAT_WAIT)
                except (OSError, ValueError):
                    print(f"[{self.name}] Socket error in select")
                    break
                if self.socket in readable:
                    try:
                        raw = myrecv(self.socket)
                    except Exception as e:
                        print(f"[{self.name}] recv error: {e}")
                        break
                    if raw:
                        try:
                            self._handle(raw)
                        except Exception as e:
                            print(f"[{self.name}] Error handling message: {e}")
                    else:
                        print(f"[{self.name}] Server closed the connection")
                        break
        except KeyboardInterrupt:
            print(f"\n[{self.name}] Shutting down…")
        finally:
            self.socket.close()


# ── CLI entry point ─────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ollama Chat Bot Client")
    parser.add_argument("-n", "--name", default="chatbot",
                        help="Bot username (default: chatbot)")
    parser.add_argument("-m", "--model", default="qwen3.5:4b",
                        help="Ollama model (default: qwen3.5:4b)")
    parser.add_argument("-p", "--personality", default=None,
                        help="Custom personality / system prompt")
    parser.add_argument("--password", default="chatbot",
                        help="Chat password for the bot user")
    parser.add_argument("-d", "--server", default=None,
                        help="Chat server IP address")
    parser.add_argument("--ollama-host", default="http://localhost:11434",
                        help="Ollama API URL (default: http://localhost:11434)")
    parser.add_argument("--timeout", type=float, default=45,
                        help="Ollama request timeout in seconds")
    parser.add_argument("--max-tokens", type=int, default=80,
                        help="Maximum tokens for each bot reply")
    parser.add_argument("--think", action="store_true",
                        help="Enable model thinking mode when supported")
    args = parser.parse_args()

    server_addr = (args.server, CHAT_PORT) if args.server else None

    bot = ChatBotClient(
        name=args.name,
        password=args.password,
        model=args.model,
        personality=args.personality,
        ollama_host=args.ollama_host,
        server_addr=server_addr,
        timeout=args.timeout,
        think=args.think,
        generation_options={
            "temperature": 0.3,
            "num_predict": args.max_tokens,
            "num_ctx": 2048,
        },
    )
    bot.run()
