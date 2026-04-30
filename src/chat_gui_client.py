import argparse
import json
import queue
import select
import socket
import threading
import time
import tkinter as tk
from tkinter import scrolledtext, simpledialog

from chat_utils import (
    SERVER, CHAT_PORT, CHAT_WAIT,
    S_OFFLINE, S_LOGGEDIN, S_CHATTING,
    menu, mysend, myrecv,
)
import client_state_machine as csm

#emojis
EMOJIS = ['😀', '😂', '😍', '😎', '🥳', '😢', '😡', '🤔',
          '👍', '👎', '❤️', '🔥', '✨', '🎉', '🙏', '💯',
          '😴', '🤝', '🚀', '🌈']


class ChatGUI:
    def __init__(self, server_ip=None):
        self.server_ip = server_ip
        self.input_queue = queue.Queue()
        self.output_queue = queue.Queue()
        self.socket = None
        self.sm = None
        self.name = ''
        self.logged_in = False
        self.running = True
        self.login_dialog = None
        self.login_btn_dialog = None
        self.login_entry = None
        self.login_error_var = None
        self.emoji_window = None
        self.chat_widgets = []

        self.root = tk.Tk()
        self.root.title("ICDS Chat")
        self.root.geometry("760x560")
        self.root.minsize(520, 380)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_widgets()
        self._set_chat_widgets_enabled(False)

    # ui
    def _build_widgets(self):
        self.status_var = tk.StringVar(value="Connecting...")
        status = tk.Label(
            self.root, textvariable=self.status_var, anchor='w',
            bg='#1f2937', fg='#f9fafb', padx=10, pady=6,
            font=('Helvetica', 10, 'bold'),
        )
        status.pack(fill=tk.X)

        self.display = scrolledtext.ScrolledText(
            self.root, state='disabled', wrap=tk.WORD,
            font=('Helvetica', 11), bg='#fafafa', padx=8, pady=8,
        )
        self.display.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        self.display.tag_config('system', foreground='#6b7280',
                                font=('Helvetica', 10, 'italic'))
        self.display.tag_config('me', foreground='#047857',
                                font=('Helvetica', 11, 'bold'))
        self.display.tag_config('peer', foreground='#1d4ed8')
        self.display.tag_config('error', foreground='#b91c1c',
                                font=('Helvetica', 10, 'bold'))

        toolbar = tk.Frame(self.root)
        toolbar.pack(fill=tk.X, padx=8)
        toolbar_buttons = [
            ('Time',   lambda: self.toolbar_send('time')),
            ('Who',    lambda: self.toolbar_send('who')),
            ('Poem',   self.prompt_poem),
            ('Search', self.prompt_search),
            ('Menu',   lambda: self.append(menu, 'system')),
            ('Quit',   lambda: self.toolbar_send('q')),
        ]
        for label, action in toolbar_buttons:
            b = tk.Button(toolbar, text=label, width=8, command=action)
            b.pack(side=tk.LEFT, padx=2, pady=4)
            self.chat_widgets.append(b)

        inframe = tk.Frame(self.root)
        inframe.pack(fill=tk.X, padx=8, pady=(4, 8))
        self.emoji_btn = tk.Button(
            inframe, text='😊', font=('Helvetica', 14),
            width=3, command=self.show_emoji_picker,
        )
        self.emoji_btn.pack(side=tk.LEFT, padx=(0, 4))
        self.chat_widgets.append(self.emoji_btn)

        self.entry = tk.Entry(inframe, font=('Helvetica', 11))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                        ipady=6, padx=(0, 6))
        self.entry.bind('<Return>', lambda e: self.on_send())
        self.chat_widgets.append(self.entry)

        self.send_btn = tk.Button(
            inframe, text='Send', command=self.on_send,
            width=10, bg='#2563eb', fg='white',
            activebackground='#1d4ed8', activeforeground='white',
            font=('Helvetica', 10, 'bold'),
        )
        self.send_btn.pack(side=tk.RIGHT, ipady=4)
        self.chat_widgets.append(self.send_btn)

    def _set_chat_widgets_enabled(self, enabled):
        state = 'normal' if enabled else 'disabled'
        for w in self.chat_widgets:
            try:
                w.config(state=state)
            except tk.TclError:
                pass
        if enabled:
            self.entry.focus_set()

    def append(self, text, tag='system'):
        if not text:
            return
        self.display.config(state='normal')
        self.display.insert(tk.END, text, tag)
        if not text.endswith('\n'):
            self.display.insert(tk.END, '\n', tag)
        self.display.see(tk.END)
        self.display.config(state='disabled')

    def set_status(self, txt):
        self.root.after(0, lambda: self.status_var.set(txt))

    # Input handlers 
    def on_send(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, tk.END)
        if not self.logged_in:
            self.append(f'Logging in as "{text}"...', 'system')
        elif self.sm and self.sm.get_state() == S_CHATTING:
            self.append(f'[{self.name}] {text}', 'me')
        else:
            self.append(f'> {text}', 'me')
        self.input_queue.put(('msg', text))

    def toolbar_send(self, cmd):
        if cmd == '?menu':
            self.append(menu, 'system')
            return
        if not self.logged_in:
            return
        chatting = self.sm and self.sm.get_state() == S_CHATTING
        # Quit while chatting: disconnect from peer first, then quit.
        if cmd == 'q' and chatting:
            self.append('> bye (disconnecting peer first)', 'me')
            self.input_queue.put(('msg', 'bye'))
        self.append(f'> {cmd}', 'me')
        self.input_queue.put(('cmd', cmd))

    # Login dialog 
    def show_login_dialog(self, error=None):
        if self.login_dialog and self.login_dialog.winfo_exists():
            self.login_dialog.lift()
            return
        d = tk.Toplevel(self.root)
        d.title("Login")
        d.geometry("340x210")
        d.resizable(False, False)
        d.transient(self.root)
        d.grab_set()
        d.protocol("WM_DELETE_WINDOW", self.on_close)

        tk.Label(d, text="ICS Chat", font=('Helvetica', 16, 'bold'),
                 pady=12).pack()
        tk.Label(d, text="Choose a username:",
                 font=('Helvetica', 10)).pack()

        self.login_entry = tk.Entry(d, font=('Helvetica', 11),
                                    justify='center')
        self.login_entry.pack(padx=24, pady=8, fill=tk.X, ipady=6)
        self.login_entry.focus_set()
        self.login_entry.bind('<Return>', lambda e: self.submit_login())

        self.login_error_var = tk.StringVar(value=error or '')
        tk.Label(d, textvariable=self.login_error_var,
                 fg='#b91c1c', font=('Helvetica', 9)).pack()

        self.login_btn_dialog = tk.Button(
            d, text="Login", command=self.submit_login,
            bg='#2563eb', fg='white', font=('Helvetica', 10, 'bold'),
            activebackground='#1d4ed8', activeforeground='white',
        )
        self.login_btn_dialog.pack(pady=8, ipady=3, ipadx=18)

        self.login_dialog = d

    def submit_login(self):
        if not self.login_entry:
            return
        name = self.login_entry.get().strip()
        if not name:
            self.login_error_var.set('Username cannot be empty')
            return
        self.login_btn_dialog.config(state='disabled', text='Logging in...')
        self.login_error_var.set('')
        self.input_queue.put(('msg', name))

    def on_login_success(self):
        if self.login_dialog and self.login_dialog.winfo_exists():
            self.login_dialog.destroy()
        self.login_dialog = None
        self._set_chat_widgets_enabled(True)

    def on_login_failed(self, msg):
        if self.login_dialog and self.login_dialog.winfo_exists():
            self.login_btn_dialog.config(state='normal', text='Login')
            self.login_error_var.set(msg)
            self.login_entry.focus_set()
        else:
            self.show_login_dialog(error=msg)

    # ---------- Emoji picker ----------
    def show_emoji_picker(self):
        if self.emoji_window and self.emoji_window.winfo_exists():
            self.emoji_window.lift()
            return
        w = tk.Toplevel(self.root)
        w.title("Emojis")
        w.resizable(False, False)
        w.transient(self.root)
        cols = 5
        for i, e in enumerate(EMOJIS):
            tk.Button(
                w, text=e, font=('Helvetica', 16), width=3,
                command=lambda emo=e: self.insert_emoji(emo),
            ).grid(row=i // cols, column=i % cols, padx=2, pady=2)
        self.emoji_window = w

    def insert_emoji(self, emo):
        self.entry.insert(tk.INSERT, emo)
        self.entry.focus_set()

    # ---------- Poem / Search prompts ----------
    def prompt_poem(self):
        if not self.logged_in:
            return
        num = simpledialog.askstring(
            "Sonnet", "Sonnet number (1-154):", parent=self.root,
        )
        if num is None:
            return
        num = num.strip()
        if not num.isdigit():
            self.append('Poem: please enter a number.', 'error')
            return
        self._submit_query_command(f'p{num}')

    def prompt_search(self):
        if not self.logged_in:
            return
        term = simpledialog.askstring(
            "Search chat history", "Search term:", parent=self.root,
        )
        if term is None:
            return
        term = term.strip()
        if not term:
            return
        self._submit_query_command(f'?{term}')

    def _submit_query_command(self, cmd):
        self.append(f'> {cmd}', 'me')
        if self.sm and self.sm.get_state() == S_CHATTING:
            self.input_queue.put(('cmd', cmd))
        else:
            self.input_queue.put(('msg', cmd))

    def on_close(self):
        self.running = False
        try:
            if self.logged_in:
                self.input_queue.put(('msg', 'q'))
            time.sleep(0.1)
            if self.socket:
                try:
                    self.socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                self.socket.close()
        except Exception:
            pass
        self.root.destroy()

    # ---------- Output pump (UI thread) ----------
    def pump(self):
        try:
            while True:
                tag, text = self.output_queue.get_nowait()
                self.append(text, tag)
        except queue.Empty:
            pass
        if self.running:
            self.root.after(80, self.pump)

    def output(self, text, tag='system'):
        if text:
            self.output_queue.put((tag, text))

    # Query helpers (used while chatting)
    def _direct_query(self, cmd):
        try:
            if cmd == 'time':
                payload = {"action": "time"}
            elif cmd == 'who':
                payload = {"action": "list"}
            elif cmd.startswith('p') and cmd[1:].isdigit():
                payload = {"action": "poem", "target": cmd[1:]}
            elif cmd.startswith('?') and len(cmd) > 1:
                payload = {"action": "search", "target": cmd[1:].strip()}
            else:
                return
            mysend(self.socket, json.dumps(payload))
        except OSError as e:
            self.output(f'Send failed: {e}', 'error')

    def _maybe_handle_query_reply(self, peer_msg):
        try:
            parsed = json.loads(peer_msg)
        except (ValueError, TypeError):
            return peer_msg
        action = parsed.get("action")
        if action == "time":
            self.output('Time: ' + parsed.get("results", ""), 'system')
            return ''
        if action == "list":
            self.output('Users online:\n' + parsed.get("results", ""), 'system')
            return ''
        if action == "poem":
            poem = parsed.get("results", "") or '(sonnet not found)'
            self.output('Sonnet:\n' + poem, 'system')
            return ''
        if action == "search":
            results = parsed.get("results", "").strip()
            self.output('Search results:\n' + (results or '(no matches)'),
                        'system')
            return ''
        return peer_msg

    # ---------- Network thread ----------
    def chat_thread(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            svr = SERVER if self.server_ip is None else (self.server_ip, CHAT_PORT)
            self.socket.connect(svr)
        except Exception as e:
            self.output(f'Failed to connect to server: {e}', 'error')
            self.set_status('Connection failed')
            return

        self.sm = csm.ClientSM(self.socket)
        self.set_status('Connected — please log in')
        self.root.after(0, self.show_login_dialog)

        # Login loop
        while self.running and not self.logged_in:
            try:
                item = self.input_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            name = item[1] if isinstance(item, tuple) else item
            try:
                mysend(self.socket, json.dumps({"action": "login", "name": name}))
                response = json.loads(myrecv(self.socket))
            except Exception as e:
                self.output(f'Login error: {e}', 'error')
                self.root.after(0, self.on_login_failed,
                                f'Connection error: {e}')
                return
            if response.get("status") == 'ok':
                self.name = name
                self.sm.set_state(S_LOGGEDIN)
                self.sm.set_myname(self.name)
                self.logged_in = True
                self.root.after(0, self.on_login_success)
                self.output(f'Welcome, {self.name}!', 'system')
                self.output(menu, 'system')
                self.set_status(f'Logged in as {self.name}')
            else:
                self.root.after(0, self.on_login_failed,
                                'Username already taken — try another.')

        # Main proc loop — mirrors chat_client_class.run_chat()
        last_state = self.sm.get_state()
        while self.running and self.sm.get_state() != S_OFFLINE:
            my_msg = ''
            try:
                item = self.input_queue.get_nowait()
                kind, val = item if isinstance(item, tuple) else ('msg', item)
                # Toolbar commands while chatting: send the raw query to the
                # server directly so it doesn't get sent to the peer as chat.
                if kind == 'cmd' and self.sm.get_state() == S_CHATTING:
                    self._direct_query(val)
                else:
                    my_msg = val
            except queue.Empty:
                pass

            peer_msg = ''
            try:
                r, _, _ = select.select([self.socket], [], [], 0)
                if self.socket in r:
                    peer_msg = myrecv(self.socket)
                    if peer_msg == '':
                        self.output('Server closed the connection.', 'error')
                        break
            except (OSError, ValueError):
                break

            # Intercept query replies that arrive while chatting — the state
            # machine's S_CHATTING branch assumes peer_msg is a chat exchange
            # and would crash on action=time/list responses.
            if peer_msg and self.sm.get_state() == S_CHATTING:
                peer_msg = self._maybe_handle_query_reply(peer_msg)

            try:
                out = self.sm.proc(my_msg, peer_msg)
            except Exception as e:
                self.output(f'Error: {e}', 'error')
                break

            if out:
                tag = 'peer' if peer_msg else 'system'
                self.output(out, tag)

            new_state = self.sm.get_state()
            if new_state != last_state:
                if new_state == S_CHATTING:
                    self.set_status(f'Chatting with {self.sm.peer} as {self.name}')
                elif new_state == S_LOGGEDIN:
                    self.set_status(f'Logged in as {self.name}')
                last_state = new_state

            time.sleep(CHAT_WAIT)

        self.set_status('Disconnected')
        self.output('Disconnected. You can close the window.', 'system')

    def run(self):
        threading.Thread(target=self.chat_thread, daemon=True).start()
        self.root.after(80, self.pump)
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="icds chat GUI client")
    parser.add_argument('-d', type=str, default=None, help='server IP address')
    args = parser.parse_args()
    ChatGUI(args.d).run()


if __name__ == '__main__':
    main()