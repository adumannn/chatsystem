# ICDS Chat System

A multi-user chat system written in Python. It uses raw TCP sockets with a
length-prefixed JSON wire protocol, a `select`-driven server, and ships with
both a command-line client and a Tkinter GUI client.

Beyond plain chat the server also supports:

- **`who`** — list users and active chat groups.
- **`time`** — server clock.
- **`p <n>`** — fetch sonnet number `n` from Shakespeare's sonnets.
- **`? <term>`** — full-text search across your own chat history (per-user
  inverted index, persisted between sessions).

## Repository layout

```
Chat_System_Full/
├── README.md
├── .gitignore
├── src/                       # Application source code
│   ├── chat_utils.py          # Shared protocol helpers, constants, paths
│   ├── chat_group.py          # In-memory chat group / room bookkeeping
│   ├── chat_server.py         # Server entry point (run this first)
│   ├── chat_client_class.py   # Core CLI client class
│   ├── chat_cmdl_client.py    # CLI client entry point
│   ├── chat_gui_client.py     # Tkinter GUI client entry point
│   ├── client_state_machine.py # Client-side protocol state machine
│   ├── indexer.py             # Inverted index + Shakespeare sonnet loader
│   ├── indexer_good.py        # Cleaner reference implementation of indexer
│   └── roman2num.py           # Builds the roman.txt.pk lookup table
├── data/                      # Static data shipped with the project
│   ├── AllSonnets.txt         # Source text for the `p` command
│   ├── roman.txt              # Roman numeral ↔ integer mapping (text)
│   ├── roman.txt.pk           # Pickled lookup loaded by indexer.PIndex
│   └── p1.txt                 # Sample sonnet (test fixture)
├── tests/
│   └── test_chat_system.py    # Offline unit tests (no network)
├── demo/                      # Standalone teaching demos for sockets/JSON
│   ├── client_demo.py
│   ├── client_demo_multi_client.py
│   ├── server_demo.py
│   ├── server_demo_multi_clients.py
│   ├── json-demo.py
│   └── parser.py
└── runtime/                   # (auto-created) per-user chat-history indices
```

`runtime/` is created automatically the first time anything in `src/` is
imported. Each user's persisted chat index is written there as `<name>.idx`
when they log out, and reloaded on next login.
Login credentials are stored in `runtime/users.json` as salted password
hashes. A new username is created the first time it logs in with a password;
later logins for that username must use the same password.

## Requirements

- Python 3.8+ (developed on 3.13)
- Standard-library modules for the core chat system.
- Optional chatbot support requires Ollama running locally plus the Python
  `ollama` package.
- `tkinter` is required for the GUI client (bundled with most Python
  installs; on Debian/Ubuntu install `python3-tk`).

## Running

Open two (or more) terminals from the repository root.

### 1. Start the server

```bash
python3 src/chat_server.py
```

The server binds to `0.0.0.0:1112` (see `CHAT_PORT` in `src/chat_utils.py`).

### 2. Start a client

GUI client (recommended):

```bash
python3 src/chat_gui_client.py            # connect to localhost
python3 src/chat_gui_client.py -d 1.2.3.4 # connect to a remote server
```

CLI client:

```bash
python3 src/chat_cmdl_client.py
python3 src/chat_cmdl_client.py -d 1.2.3.4
```

Once logged in, available commands are:

| Command       | Effect                                                |
| ------------- | ----------------------------------------------------- |
| `time`        | Show the server's current time                        |
| `who`         | List users and active chat groups                     |
| `c <peer>`    | Connect to `<peer>` and start chatting                |
| `? <term>`    | Search your own chat history for `<term>`             |
| `p <n>`       | Print Shakespeare's sonnet number `<n>` (1–154)       |
| `bye`         | Leave the current conversation, stay logged in        |
| `q`           | Quit the chat system                                  |

The GUI client exposes the same commands as toolbar buttons plus an emoji
picker.

The GUI also includes a graphical Tic-Tac-Toe game. Both players log in through
the normal chat client, then one player clicks **Game**, enters an online
opponent's username, and the server creates a game session. Moves are sent to
the server as `game_move` messages; the server enforces turns, validates wins or
draws, and broadcasts the updated board to both players. Finished games update a
server-side leaderboard that is broadcast to connected clients and can be shown
with the **Scores** button.

Login requires both a username and password. Usernames may contain letters,
numbers, dots, dashes, and underscores, up to 32 characters.

The GUI starts a `chatbot` user automatically. After login, ordinary text that
is not a chat command is sent to the chatbot; use `c <peer>` when you want to
chat with another online user instead. Chatbot settings can be overridden with
`CHATBOT_NAME`, `CHATBOT_PASSWORD`, `CHATBOT_MODEL`, `CHATBOT_OLLAMA_HOST`, and
`CHATBOT_TIMEOUT` (defaults to 45 seconds).

The chatbot keeps short in-memory conversation context per direct chat or group.
In a group chat, mention it by name to trigger a reply, for example
`@chatbot what do you think about our project?`. You can change its behavior
from chat with `@chatbot personality: answer like a concise mentor`, inspect it
with `@chatbot personality?`, and clear local conversation memory with
`@chatbot clear context`.

## Running the tests

```bash
python3 -m unittest discover -s tests -v
```

The suite (`tests/test_chat_system.py`) covers the chat utilities, the group
manager, and the indexer / sonnet loader. It does not require the server to
be running.

## Wire protocol (quick reference)

Every message on the socket is sent through `mysend` / `myrecv` in
`chat_utils.py`:

```
+----------------+-----------------------+
| 5-byte length  | UTF-8 JSON payload    |
| (zero-padded)  |                       |
+----------------+-----------------------+
```

Typical actions exchanged in the JSON payload:

- `{"action": "login", "name": "<user>", "password": "<password>"}`
- `{"action": "list"}` / `{"action": "time"}`
- `{"action": "connect", "target": "<peer>"}`
- `{"action": "exchange", "from": "[user]", "message": "..."}`
- `{"action": "game_invite", "game": "tictactoe", "target": "<peer>"}`
- `{"action": "game_move", "game": "tictactoe", "game_id": "<id>", "cell": 0}`
- `{"action": "search", "target": "<term>"}`
- `{"action": "poem",   "target": "<n>"}`
- `{"action": "disconnect"}`

## Regenerating the roman-numeral table

`data/roman.txt.pk` is a pickled `dict` that the sonnet loader uses to map
sonnet numbers to their roman-numeral headings. If you ever need to rebuild
it from `data/roman.txt`:

```bash
python3 src/roman2num.py
```

The script resolves bare filenames against `data/`, so the regenerated
pickle lands in the right place.

## Notes

- The server keeps user chat history under `runtime/<name>.idx`. Delete the
  file (or the whole `runtime/` directory) to reset a user's search index.
- `src/indexer.py` and `src/indexer_good.py` are two implementations of the
  same `Index` / `PIndex` API; the production server uses `indexer.py`,
  while the tests pin against `indexer_good.py` as a reference.
- The original course attribution lives in the file headers; this README
  only documents the layout and how to run things.
