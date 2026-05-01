import argparse
import json
import os
import queue
import select
import socket
import threading
import time
import tkinter as tk
from tkinter import filedialog, scrolledtext, simpledialog

from PIL import Image as PILImage, ImageTk

from image_gen import generate_image, ImageGenError, IMAGES_DIR

from chat_bot_client import ChatBotClient
from sentiment import analyze as analyze_sentiment

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


class TicTacToeWindow:
    def __init__(self, gui, payload):
        self.gui = gui
        self.game_id = payload["game_id"]
        self.mark = payload["mark"]
        self.window = tk.Toplevel(gui.root)
        self.window.title("Tic-Tac-Toe")
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.window.withdraw)

        self.title_var = tk.StringVar()
        tk.Label(
            self.window,
            textvariable=self.title_var,
            font=('Helvetica', 12, 'bold'),
            pady=8,
        ).pack(fill=tk.X)

        board_frame = tk.Frame(self.window)
        board_frame.pack(padx=12, pady=8)
        self.buttons = []
        for idx in range(9):
            btn = tk.Button(
                board_frame,
                text='',
                width=4,
                height=2,
                font=('Helvetica', 24, 'bold'),
                command=lambda cell=idx: self.play(cell),
            )
            btn.grid(row=idx // 3, column=idx % 3, padx=3, pady=3)
            self.buttons.append(btn)

        self.status_var = tk.StringVar()
        tk.Label(
            self.window,
            textvariable=self.status_var,
            font=('Helvetica', 10),
            pady=4,
        ).pack(fill=tk.X)

        actions = tk.Frame(self.window)
        actions.pack(pady=(4, 12))
        tk.Button(actions, text='Scoreboard',
                  command=self.gui.request_game_scoreboard).pack(
                      side=tk.LEFT, padx=4)
        tk.Button(actions, text='Resign', command=self.resign).pack(
            side=tk.LEFT, padx=4)

        self.update(payload)

    def play(self, cell):
        self.gui.input_queue.put((
            'game_move',
            {'game_id': self.game_id, 'cell': cell},
        ))

    def resign(self):
        self.gui.input_queue.put((
            'game_resign',
            {'game_id': self.game_id},
        ))

    def update(self, payload):
        board = payload.get("board", [""] * 9)
        status = payload.get("status", "active")
        turn = payload.get("turn")
        opponent = payload.get("opponent", "opponent")
        message = payload.get("message", "")

        self.title_var.set(
            f"You are {self.mark} vs {opponent}"
        )
        my_turn = status == "active" and turn == self.gui.name
        for idx, btn in enumerate(self.buttons):
            btn.config(
                text=board[idx],
                state='normal' if my_turn and not board[idx] else 'disabled',
            )

        if status == "finished":
            winner = payload.get("winner")
            if winner == self.gui.name:
                status_text = "You won."
            elif winner:
                status_text = f"{winner} won."
            else:
                status_text = "Draw."
        elif my_turn:
            status_text = "Your turn."
        else:
            status_text = f"Waiting for {turn}."
        if message:
            status_text = message + " " + status_text
        self.status_var.set(status_text)
        self.window.deiconify()
        self.window.lift()


class ChatGUI:
    def __init__(self, server_ip=None):
        #networking and state
        self.server_ip = server_ip
        self.input_queue = queue.Queue()
        self.output_queue = queue.Queue()
        self.socket = None
        self.sm = None
        self.name = ''
        self.bot_name = os.environ.get("CHATBOT_NAME", "chatbot")
        self.pending_bot_msg = ''
        self.bot_in_group = False
        self.game_windows = {}

        #login variables
        self.logged_in = False
        self.running = True
        self.login_dialog = None
        self.login_btn_dialog = None

        self.login_entry = None
        self.login_error_var = None

        self.password_entry = None
        self.password_error_var = None

        self.emoji_window = None
        self.image_gen_window = None
        self._image_refs = []

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
        self.display.tag_config('image_caption', foreground='#7c3aed',
                                font=('Helvetica', 10, 'italic'))
        # Sentiment tags
        self.display.tag_config('sentiment_positive',
                                foreground='#16a34a',
                                font=('Helvetica', 9, 'bold'))
        self.display.tag_config('sentiment_neutral',
                                foreground='#737373',
                                font=('Helvetica', 9, 'italic'))
        self.display.tag_config('sentiment_negative',
                                foreground='#dc2626',
                                font=('Helvetica', 9, 'bold'))

        toolbar = tk.Frame(self.root)
        toolbar.pack(fill=tk.X, padx=8)
        toolbar_buttons = [
            ('Time',   lambda: self.toolbar_send('time')),
            ('Who',    lambda: self.toolbar_send('who')),
            ('Game',   self.prompt_game),
            ('Scores', self.request_game_scoreboard),
            ('Image',  self.prompt_image_gen),
            ('Poem',   self.prompt_poem),
            ('Search', self.prompt_search),
            ('Keywords', lambda: self.toolbar_send('/keywords')),
            ('Summary',  lambda: self.toolbar_send('/summary')),
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

    def append(self, text, tag='system', sentiment_text=None):
        if not text:
            return
        self.display.config(state='normal')
        self.display.insert(tk.END, text, tag)
        # Append sentiment tag inline after the message
        if sentiment_text is not None:
            result = analyze_sentiment(sentiment_text)
            stag = f'sentiment_{result.label}'
            self.display.insert(tk.END, f'  [{result.tag_text}]', stag)
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
            if text in ('/keywords', '/summary'):
                self.append(f'> {text}', 'me')
                self.input_queue.put(('cmd', text))
                return
            self.append(f'[{self.name}] {text}', 'me', sentiment_text=text)
        elif self._is_loggedin_command(text):
            self.append(f'> {text}', 'me')
        else:
            self.append(f'[{self.name}] {text}', 'me', sentiment_text=text)
            self.input_queue.put(('bot_msg', text))
            return
        self.input_queue.put(('msg', text))

    def _is_loggedin_command(self, text):
        return (
            text in ('q', 'time', 'who', '/keywords', '/summary')
            or text.startswith('c ')
            or text.startswith('?')
            or (text.startswith('p') and text[1:].isdigit())
        )

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
        d.geometry("340x240")
        d.resizable(False, False)
        d.transient(self.root)
        d.grab_set()
        d.protocol("WM_DELETE_WINDOW", self.on_close)

        tk.Label(d, text="ICDS Chat", font=('Helvetica', 16, 'bold'),
                 pady=12).pack()
        tk.Label(d, text="Username:",
                 font=('Helvetica', 10)).pack()
        self.login_entry = tk.Entry(d, font=('Helvetica', 11),
                                    justify='center')
        self.login_entry.pack(padx=24, pady=8, fill=tk.X, ipady=6)
        self.login_entry.focus_set()
        self.login_entry.bind('<Return>', lambda e: self.submit_login())

        tk.Label(d, text="Password:",
                 font=('Helvetica', 10)).pack()
        self.password_entry = tk.Entry(d, font=('Helvetica', 11),
                                       justify='center', show='*')
        self.password_entry.pack(padx=24, pady=8, fill=tk.X, ipady=6)
        self.password_entry.bind('<Return>', lambda e: self.submit_login())

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

    #login
    def submit_login(self):
        if not self.login_entry:
            return
        name = self.login_entry.get().strip()
        if not name:
            self.login_error_var.set('Username cannot be empty')
            return
        
        password = self.password_entry.get().strip()
        if not password:
            self.login_error_var.set('Password cannot be empty')
            return
        
        self.login_btn_dialog.config(state='disabled', text='Logging in...')
        self.login_error_var.set('')
        self.input_queue.put(('login', {"name": name, "password": password}))

    #login_handle
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

    #password
    def on_password_required(self):
        if self.login_dialog and self.login_dialog.winfo_exists():
            self.login_btn_dialog.config(state='normal', text='Login')
            self.login_error_var.set('Password required')
            self.login_entry.focus_set()
        else:
            self.show_login_dialog(error='Password required')

    def on_password_wrong(self):
        if self.login_dialog and self.login_dialog.winfo_exists():
            self.login_btn_dialog.config(state='normal', text='Login')
            self.login_error_var.set('Password is wrong')
            self.login_entry.focus_set()
        else:
            self.show_login_dialog(error='Password is wrong')

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

    def prompt_game(self):
        if not self.logged_in:
            return
        opponent = simpledialog.askstring(
            "Tic-Tac-Toe",
            "Opponent username:",
            parent=self.root,
        )
        if opponent is None:
            return
        opponent = opponent.strip()
        if not opponent:
            return
        self.append(f'> invite {opponent} to Tic-Tac-Toe', 'me')
        self.input_queue.put(('game_invite', opponent))

    def request_game_scoreboard(self):
        if not self.logged_in:
            return
        self.input_queue.put(('game_scoreboard', {}))

    # ---------- Image generation ----------
    def prompt_image_gen(self):
        if not self.logged_in:
            return
        if self.image_gen_window and self.image_gen_window.winfo_exists():
            self.image_gen_window.lift()
            return

        w = tk.Toplevel(self.root)
        w.title("Generate Image")
        w.geometry("420x380")
        w.resizable(False, False)
        w.transient(self.root)

        tk.Label(w, text="Image Generation", font=('Helvetica', 14, 'bold'),
                 pady=8).pack()

        # Prompt
        tk.Label(w, text="Prompt:", font=('Helvetica', 10), anchor='w').pack(
            fill=tk.X, padx=16)
        prompt_text = tk.Text(w, height=4, font=('Helvetica', 11), wrap=tk.WORD)
        prompt_text.pack(fill=tk.X, padx=16, pady=(4, 8))
        prompt_text.focus_set()

        # Attach image for editing
        attach_frame = tk.Frame(w)
        attach_frame.pack(fill=tk.X, padx=16)

        attached_path = tk.StringVar(value='')
        attach_label = tk.Label(attach_frame, text="No image attached",
                                font=('Helvetica', 9), fg='#6b7280')
        attach_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        thumb_label = tk.Label(w)
        thumb_label.pack(pady=4)

        def attach_image():
            path = filedialog.askopenfilename(
                parent=w,
                title="Select source image",
                filetypes=[
                    ("Images", "*.png *.jpg *.jpeg *.webp *.bmp"),
                    ("All files", "*.*"),
                ],
            )
            if not path:
                return
            attached_path.set(path)
            attach_label.config(text=os.path.basename(path), fg='#047857')
            try:
                img = PILImage.open(path)
                img.thumbnail((100, 100))
                photo = ImageTk.PhotoImage(img)
                thumb_label.config(image=photo)
                thumb_label._photo = photo
            except Exception:
                pass

        def clear_image():
            attached_path.set('')
            attach_label.config(text="No image attached", fg='#6b7280')
            thumb_label.config(image='')
            thumb_label._photo = None

        tk.Button(attach_frame, text="Attach", width=8,
                  command=attach_image).pack(side=tk.LEFT, padx=4)
        tk.Button(attach_frame, text="Clear", width=8,
                  command=clear_image).pack(side=tk.LEFT, padx=4)

        # Status & generate button
        status_var = tk.StringVar(value='')
        tk.Label(w, textvariable=status_var, fg='#b91c1c',
                 font=('Helvetica', 9)).pack(pady=4)

        gen_btn = tk.Button(
            w, text="Generate", bg='#7c3aed', fg='white',
            activebackground='#6d28d9', activeforeground='white',
            font=('Helvetica', 10, 'bold'),
        )
        gen_btn.pack(pady=8, ipady=4, ipadx=20)

        def on_generate():
            prompt = prompt_text.get('1.0', tk.END).strip()
            if not prompt:
                status_var.set('Prompt cannot be empty')
                return
            img_path = attached_path.get() or None
            gen_btn.config(state='disabled', text='Generating...')
            status_var.set('')
            threading.Thread(
                target=self._image_gen_worker,
                args=(prompt, img_path, gen_btn, status_var, w),
                daemon=True,
            ).start()

        gen_btn.config(command=on_generate)
        prompt_text.bind('<Command-Return>', lambda e: on_generate())
        prompt_text.bind('<Control-Return>', lambda e: on_generate())

        self.image_gen_window = w

    def _image_gen_worker(self, prompt, image_path, gen_btn, status_var, window):
        try:
            output_path = generate_image(prompt, image_path)
            self.root.after(0, self._on_image_gen_done,
                            output_path, prompt, gen_btn, status_var, window)
        except ImageGenError as e:
            self.root.after(0, self._on_image_gen_error,
                            str(e), gen_btn, status_var)
        except Exception as e:
            self.root.after(0, self._on_image_gen_error,
                            f'Unexpected error: {e}', gen_btn, status_var)

    def _on_image_gen_done(self, path, prompt, gen_btn, status_var, window):
        gen_btn.config(state='normal', text='Generate')
        status_var.set('')
        self.append(f'Image generated: "{prompt}"', 'image_caption')
        self._display_image_thumbnail(path)
        self.append(f'Saved to {path}', 'system')
        if window and window.winfo_exists():
            window.destroy()
        self.image_gen_window = None

    def _on_image_gen_error(self, msg, gen_btn, status_var):
        gen_btn.config(state='normal', text='Generate')
        status_var.set(msg)
        self.output(f'Image generation failed: {msg}', 'error')

    def _display_image_thumbnail(self, path, max_size=300):
        try:
            img = PILImage.open(path)
            img.thumbnail((max_size, max_size))
            photo = ImageTk.PhotoImage(img)
            self._image_refs.append(photo)

            self.display.config(state='normal')
            self.display.image_create(tk.END, image=photo, padx=4, pady=4)
            self.display.insert(tk.END, '\n')

            tag_name = f'img_{len(self._image_refs)}'
            self.display.tag_add(tag_name, 'end-2c', 'end-1c')
            self.display.tag_bind(
                tag_name, '<Button-1>',
                lambda e, p=path: self._open_full_image(p),
            )
            self.display.tag_config(tag_name, relief='raised', borderwidth=1)

            self.display.see(tk.END)
            self.display.config(state='disabled')
        except Exception as e:
            self.append(f'(Could not display thumbnail: {e})', 'error')

    def _open_full_image(self, path):
        viewer = tk.Toplevel(self.root)
        viewer.title(os.path.basename(path))
        try:
            img = PILImage.open(path)
            photo = ImageTk.PhotoImage(img)
            self._image_refs.append(photo)
            lbl = tk.Label(viewer, image=photo)
            lbl.pack()
        except Exception as e:
            tk.Label(viewer, text=f'Error: {e}', fg='red').pack(padx=20, pady=20)
            return

        def save_as():
            dest = filedialog.asksaveasfilename(
                parent=viewer,
                title="Save image as",
                defaultextension=".png",
                initialfile=os.path.basename(path),
                filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("All", "*.*")],
            )
            if dest:
                PILImage.open(path).save(dest)

        btn_frame = tk.Frame(viewer)
        btn_frame.pack(pady=8)
        tk.Button(btn_frame, text="Save As...", command=save_as).pack(
            side=tk.LEFT, padx=4)
        tk.Button(btn_frame, text="Close", command=viewer.destroy).pack(
            side=tk.LEFT, padx=4)

    def open_or_update_game(self, parsed):
        game_id = parsed["game_id"]
        window = self.game_windows.get(game_id)
        if not window:
            self.game_windows[game_id] = TicTacToeWindow(self, parsed)
        else:
            window.update(parsed)

    def handle_game_message(self, parsed):
        action = parsed.get("action")
        if action == "game_start":
            self.root.after(0, self.open_or_update_game, parsed)
            self.output('Tic-Tac-Toe started vs ' +
                        parsed.get("opponent", "opponent"), 'system')
            return True
        if action == "game_update":
            self.root.after(0, self.open_or_update_game, parsed)
            return True
        if action == "game_error":
            self.output('Game: ' + parsed.get("message", "Unknown error"),
                        'error')
            return True
        if action == "game_scoreboard":
            rankings = parsed.get("rankings", [])
            if not rankings:
                self.output('Tic-Tac-Toe scoreboard is empty.', 'system')
                return True
            lines = ['Tic-Tac-Toe leaderboard:']
            for idx, row in enumerate(rankings, start=1):
                lines.append(
                    f'{idx}. {row["player"]}: '
                    f'{row["wins"]}W {row["losses"]}L {row["draws"]}D'
                )
            self.output('\n'.join(lines), 'system')
            return True
        return False

    
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
                item = self.output_queue.get_nowait()
                if len(item) == 3:
                    tag, text, sentiment_text = item
                else:
                    tag, text = item
                    sentiment_text = None
                self.append(text, tag, sentiment_text=sentiment_text)
        except queue.Empty:
            pass
        if self.running:
            self.root.after(80, self.pump)

    def output(self, text, tag='system', sentiment_text=None):
        if text:
            self.output_queue.put((tag, text, sentiment_text))

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
            elif cmd == '/keywords':
                payload = {"action": "keywords"}
            elif cmd == '/summary':
                payload = {"action": "summary"}
            else:
                return
            mysend(self.socket, json.dumps(payload))
        except OSError as e:
            self.output(f'Send failed: {e}', 'error')

    def _send_game_action(self, action, payload=None):
        payload = dict(payload or {})
        payload["action"] = action
        payload["game"] = "tictactoe"
        try:
            mysend(self.socket, json.dumps(payload))
        except OSError as e:
            self.output(f'Game send failed: {e}', 'error')

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
        if action == "keywords":
            self.output(parsed.get("results", "(no keywords)"), 'system')
            return ''
        if action == "summary":
            self.output(parsed.get("results", "(no summary)"), 'system')
            return ''
        return peer_msg

    # ---------- Chatbot auto-start ----------
    def _start_bot(self):
        """Spin up the Ollama chatbot in a background thread."""
        svr = SERVER if self.server_ip is None else (self.server_ip, CHAT_PORT)
        self.bot = ChatBotClient(
            name=self.bot_name,
            password=os.environ.get("CHATBOT_PASSWORD", "chatbot"),
            model=os.environ.get("CHATBOT_MODEL", "qwen3.5:4b"),
            ollama_host=os.environ.get(
                "CHATBOT_OLLAMA_HOST",
                "http://localhost:11434",
            ),
            server_addr=svr,
            timeout=float(os.environ.get("CHATBOT_TIMEOUT", "45")),
        )
        self.bot_thread = threading.Thread(target=self.bot.run, daemon=True)
        self.bot_thread.start()
        print("[GUI] Chatbot started in background")

    def _request_bot_join(self):
        """Send a connect request to pull the chatbot into the current group."""
        if self.bot_in_group:
            return
        try:
            mysend(self.socket, json.dumps({
                "action": "connect",
                "target": self.bot_name,
            }))
            self.bot_in_group = True
        except OSError:
            pass

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

        # Start the chatbot once we know the server is reachable
        self._start_bot()

        self.sm = csm.ClientSM(self.socket)
        self.set_status('Connected — please log in')
        self.root.after(0, self.show_login_dialog)

        # Login loop
        while self.running and not self.logged_in:
            try:
                item = self.input_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            kind, payload = item if isinstance(item, tuple) else ('msg', item)
            if kind == 'login' and isinstance(payload, dict):
                name = payload.get("name", "").strip()
                password = payload.get("password", "")
            else:
                raw_login = str(payload)
                if ':' in raw_login:
                    name, password = raw_login.split(':', 1)
                    name = name.strip()
                else:
                    name, password = raw_login.strip(), ''
            try:
                mysend(self.socket, json.dumps({
                    "action": "login",
                    "name": name,
                    "password": password,
                }))
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
                status = response.get("status")
                if status == "password-required":
                    self.root.after(0, self.on_password_required)
                elif status == "wrong-password":
                    self.root.after(0, self.on_password_wrong)
                else:
                    self.root.after(
                        0,
                        self.on_login_failed,
                        response.get("message", "Login failed"),
                    )

        # Main proc loop — mirrors chat_client_class.run_chat()
        last_state = self.sm.get_state()
        while self.running and self.sm.get_state() != S_OFFLINE:
            my_msg = ''
            connecting_to_bot = False
            if (
                self.pending_bot_msg
                and self.sm.get_state() == S_CHATTING
                and self.sm.peer == self.bot_name
            ):
                my_msg = self.pending_bot_msg
                self.pending_bot_msg = ''
            else:
                try:
                    item = self.input_queue.get_nowait()
                    kind, val = item if isinstance(item, tuple) else ('msg', item)
                    # Toolbar commands while chatting: send the raw query to the
                    # server directly so it doesn't get sent to the peer as chat.
                    if kind == 'cmd' and self.sm.get_state() == S_CHATTING:
                        self._direct_query(val)
                    elif kind == 'game_invite':
                        self._send_game_action(
                            'game_invite',
                            {'target': val},
                        )
                    elif kind == 'game_move':
                        self._send_game_action('game_move', val)
                    elif kind == 'game_resign':
                        self._send_game_action('game_resign', val)
                    elif kind == 'game_scoreboard':
                        self._send_game_action('game_scoreboard')
                    elif kind == 'bot_msg' and self.sm.get_state() == S_LOGGEDIN:
                        self.pending_bot_msg = val
                        my_msg = f'c {self.bot_name}'
                        connecting_to_bot = True
                    elif kind == 'bot_msg':
                        my_msg = val
                    else:
                        my_msg = val
                except queue.Empty:
                    pass

            if (
                self.pending_bot_msg
                and self.sm.get_state() == S_CHATTING
                and self.sm.peer != self.bot_name
            ):
                self.pending_bot_msg = ''

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
            if peer_msg:
                try:
                    parsed_peer = json.loads(peer_msg)
                except (ValueError, TypeError):
                    parsed_peer = {}
                if self.handle_game_message(parsed_peer):
                    peer_msg = ''
                elif (
                    parsed_peer.get("action") == "connect"
                    and "from" not in parsed_peer
                ):
                    status = parsed_peer.get("status")
                    if status == "success":
                        self.output(f'({self.bot_name} joined the chat)', 'system')
                    elif status == "no-user":
                        self.output(f'{self.bot_name} is not online', 'error')
                        self.bot_in_group = False
                    peer_msg = ''

            if peer_msg and self.sm.get_state() == S_CHATTING:
                peer_msg = self._maybe_handle_query_reply(peer_msg)

            if (
                my_msg
                and self.sm.get_state() == S_CHATTING
                and self.bot_name
                and f'@{self.bot_name}'.lower() in my_msg.lower()
            ):
                self._request_bot_join()

            try:
                out = self.sm.proc(my_msg, peer_msg)
            except Exception as e:
                self.output(f'Error: {e}', 'error')
                break

            if connecting_to_bot and self.sm.get_state() != S_CHATTING:
                self.pending_bot_msg = ''

            if out:
                if connecting_to_bot and self.sm.get_state() == S_CHATTING:
                    out = out + f'Sending to {self.bot_name}...\n'
                tag = 'peer' if peer_msg else 'system'
                # Extract raw message text for sentiment analysis on peer msgs
                peer_sentiment_text = None
                if peer_msg and tag == 'peer':
                    try:
                        _pmsg = json.loads(peer_msg)
                        if _pmsg.get("action") == "exchange":
                            peer_sentiment_text = _pmsg.get("message", "")
                    except (ValueError, TypeError):
                        pass
                self.output(out, tag, sentiment_text=peer_sentiment_text)

            new_state = self.sm.get_state()
            if new_state != last_state:
                if new_state == S_CHATTING:
                    self.set_status(f'Chatting with {self.sm.peer} as {self.name}')
                elif new_state == S_LOGGEDIN:
                    self.bot_in_group = False
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
