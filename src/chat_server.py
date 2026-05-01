"""
Created on Tue Jul 22 00:47:05 2014

@author: alina, zzhang
"""

import json
import os
import select
import socket
import string
import sys
import time
import pickle as pkl

import chat_group as grp
import indexer
import nlp_utils
from auth_store import PasswordAuthenticator
from chat_utils import *

class Server:
    def __init__(self):
        self.new_clients = [] #list of new sockets of which the user id is not known
        self.logged_name2sock = {} #dictionary mapping username to socket
        self.logged_sock2name = {} # dict mapping socket to user name
        self.all_sockets = []
        self.group = grp.Group()
        self.auth = PasswordAuthenticator()
        #start server
        self.server=socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.bind(SERVER)
        self.server.listen(5)
        self.all_sockets.append(self.server)
        #initialize past chat indices
        self.indices={}
        # active game state
        self.games = {}
        self.player_game = {}
        self.game_scores = {}
        self.game_seq = 0
        # sonnet
        # self.sonnet_f = open('AllSonnets.txt.idx', 'rb')
        # self.sonnet = pkl.load(self.sonnet_f)
        # self.sonnet_f.close()
        self.sonnet = indexer.PIndex("AllSonnets.txt")

    def drop_new_client(self, sock):
        if sock in self.new_clients:
            self.new_clients.remove(sock)
        if sock in self.all_sockets:
            self.all_sockets.remove(sock)
        try:
            sock.close()
        except OSError:
            pass

    def complete_login(self, sock, name):
        self.new_clients.remove(sock)
        self.logged_name2sock[name] = sock
        self.logged_sock2name[sock] = name
        if name not in self.indices.keys():
            idx_path = os.path.join(RUNTIME_DIR, name + '.idx')
            try:
                with open(idx_path, 'rb') as f:
                    self.indices[name] = pkl.load(f)
            except IOError:
                self.indices[name] = indexer.Index(name)
        print(name + ' logged in')
        self.group.join(name)
        mysend(sock, json.dumps({"action":"login", "status":"ok"}))

    def reject_login(self, sock, status, message):
        mysend(sock, json.dumps({
            "action": "login",
            "status": status,
            "message": message,
        }))
    def new_client(self, sock):
        #add to all sockets and to new clients
        print('new client...')
        sock.setblocking(0)
        self.new_clients.append(sock)
        self.all_sockets.append(sock)

    def login(self, sock):
        #read the msg that should have login code plus username/password
        try:
            msg = json.loads(myrecv(sock))
            if len(msg) > 0:

                if msg.get("action") == "login":
                    name = (msg.get("name") or "").strip()
                    password = msg.get("password")
                    ok, status, message = self.auth.authenticate(name, password)
                    if not ok:
                        self.reject_login(sock, status, message)
                        print('login rejected: ' + status)
                    elif self.group.is_member(name):
                        self.reject_login(
                            sock,
                            "duplicate",
                            "Username already taken",
                        )
                        print(name + ' duplicate login attempt')
                    else:
                        self.complete_login(sock, name)
                else:
                    print ('wrong code received')
            else: #client died unexpectedly
                self.drop_new_client(sock)
        except (OSError, ValueError, KeyError):
            self.drop_new_client(sock)

    def logout(self, sock):
        #remove sock from all lists
        name = self.logged_sock2name[sock]
        self.forfeit_game(name)
        idx_path = os.path.join(RUNTIME_DIR, name + '.idx')
        with open(idx_path, 'wb') as f:
            pkl.dump(self.indices[name], f)
        del self.indices[name]
        del self.logged_name2sock[name]
        del self.logged_sock2name[sock]
        self.all_sockets.remove(sock)
        self.group.leave(name)
        sock.close()

    def send_game_error(self, sock, message):
        mysend(sock, json.dumps({
            "action": "game_error",
            "message": message,
        }))

    def next_game_id(self):
        self.game_seq += 1
        return "ttt-" + str(self.game_seq)

    def game_score(self, name):
        if name not in self.game_scores:
            self.game_scores[name] = {
                "player": name,
                "wins": 0,
                "losses": 0,
                "draws": 0,
                "played": 0,
            }
        return self.game_scores[name]

    def game_rankings(self):
        return sorted(
            self.game_scores.values(),
            key=lambda row: (-row["wins"], -row["draws"], row["losses"], row["player"]),
        )

    def broadcast_game_scoreboard(self):
        payload = json.dumps({
            "action": "game_scoreboard",
            "game": "tictactoe",
            "rankings": self.game_rankings(),
        })
        for sock in list(self.logged_sock2name.keys()):
            try:
                mysend(sock, payload)
            except OSError:
                pass

    @staticmethod
    def tictactoe_winner(board):
        wins = (
            (0, 1, 2), (3, 4, 5), (6, 7, 8),
            (0, 3, 6), (1, 4, 7), (2, 5, 8),
            (0, 4, 8), (2, 4, 6),
        )
        for a, b, c in wins:
            if board[a] and board[a] == board[b] == board[c]:
                return board[a]
        if all(board):
            return "draw"
        return None

    def send_game_state(self, game, event="game_update", message=""):
        for player in game["players"]:
            sock = self.logged_name2sock.get(player)
            if not sock:
                continue
            payload = {
                "action": event,
                "game": "tictactoe",
                "game_id": game["id"],
                "board": game["board"],
                "players": game["players"],
                "mark": game["marks"][player],
                "turn": game["turn"],
                "status": game["status"],
                "winner": game.get("winner"),
                "message": message,
                "opponent": next(p for p in game["players"] if p != player),
            }
            try:
                mysend(sock, json.dumps(payload))
            except OSError:
                pass

    def finish_game(self, game, winner=None, draw=False, message=""):
        game["status"] = "finished"
        game["winner"] = winner
        for player in game["players"]:
            score = self.game_score(player)
            score["played"] += 1
            if draw:
                score["draws"] += 1
            elif player == winner:
                score["wins"] += 1
            else:
                score["losses"] += 1
            self.player_game.pop(player, None)
        self.send_game_state(game, message=message)
        self.broadcast_game_scoreboard()

    def forfeit_game(self, name):
        game_id = self.player_game.get(name)
        if not game_id:
            return
        game = self.games.get(game_id)
        if not game or game["status"] != "active":
            self.player_game.pop(name, None)
            return
        winner = next(player for player in game["players"] if player != name)
        self.finish_game(
            game,
            winner=winner,
            message=name + " left the game. " + winner + " wins.",
        )

    def handle_game_msg(self, from_sock, msg):
        from_name = self.logged_sock2name[from_sock]
        action = msg["action"]

        if action == "game_invite":
            to_name = (msg.get("target") or "").strip()
            if not to_name:
                self.send_game_error(from_sock, "Choose an opponent first.")
                return
            if to_name == from_name:
                self.send_game_error(from_sock, "You cannot play against yourself.")
                return
            if not self.group.is_member(to_name):
                self.send_game_error(from_sock, "Opponent is not online.")
                return
            if from_name in self.player_game or to_name in self.player_game:
                self.send_game_error(from_sock, "One of the players is already in a game.")
                return

            game = {
                "id": self.next_game_id(),
                "players": [from_name, to_name],
                "marks": {from_name: "X", to_name: "O"},
                "board": [""] * 9,
                "turn": from_name,
                "status": "active",
                "winner": None,
            }
            self.games[game["id"]] = game
            self.player_game[from_name] = game["id"]
            self.player_game[to_name] = game["id"]
            self.send_game_state(game, event="game_start", message="Game started.")
            return

        if action == "game_scoreboard":
            mysend(from_sock, json.dumps({
                "action": "game_scoreboard",
                "game": "tictactoe",
                "rankings": self.game_rankings(),
            }))
            return

        game_id = msg.get("game_id") or self.player_game.get(from_name)
        game = self.games.get(game_id)
        if not game or from_name not in game["players"]:
            self.send_game_error(from_sock, "Game not found.")
            return
        if game["status"] != "active":
            self.send_game_error(from_sock, "That game is already finished.")
            return

        if action == "game_resign":
            winner = next(player for player in game["players"] if player != from_name)
            self.finish_game(
                game,
                winner=winner,
                message=from_name + " resigned. " + winner + " wins.",
            )
            return

        if action == "game_move":
            if game["turn"] != from_name:
                self.send_game_error(from_sock, "It is not your turn.")
                return
            try:
                cell = int(msg.get("cell"))
            except (TypeError, ValueError):
                self.send_game_error(from_sock, "Invalid move.")
                return
            if cell < 0 or cell >= 9 or game["board"][cell]:
                self.send_game_error(from_sock, "Invalid move.")
                return

            game["board"][cell] = game["marks"][from_name]
            result = self.tictactoe_winner(game["board"])
            if result == "draw":
                self.finish_game(game, draw=True, message="Game ended in a draw.")
            elif result:
                self.finish_game(game, winner=from_name, message=from_name + " wins.")
            else:
                game["turn"] = next(player for player in game["players"] if player != from_name)
                self.send_game_state(game)

#==============================================================================
# main command switchboard
#==============================================================================
    def handle_msg(self, from_sock):
        #read msg code
        msg = myrecv(from_sock)
        if len(msg) > 0:
#==============================================================================
# handle connect request
#==============================================================================
            msg = json.loads(msg)
            if msg["action"] == "connect":
                to_name = msg["target"]
                from_name = self.logged_sock2name[from_sock]
                if to_name == from_name:
                    msg = json.dumps({"action":"connect", "status":"self"})
                # connect to the peer
                elif self.group.is_member(to_name):
                    from_was_grouped, _ = self.group.find_group(from_name)
                    peer_was_grouped, _ = self.group.find_group(to_name)
                    self.group.connect(from_name, to_name)
                    the_guys = self.group.list_me(from_name)
                    msg = json.dumps({"action":"connect", "status":"success"})
                    for g in the_guys[1:]:
                        to_sock = self.logged_name2sock[g]
                        notice_from = from_name
                        if from_was_grouped and not peer_was_grouped and g != to_name:
                            notice_from = to_name
                        mysend(to_sock, json.dumps({
                            "action":"connect",
                            "status":"request",
                            "from":notice_from,
                            "members":the_guys,
                        }))
                else:
                    msg = json.dumps({"action":"connect", "status":"no-user"})
                mysend(from_sock, msg)
#==============================================================================
# handle messeage exchange: one peer for now. will need multicast later
#==============================================================================
            elif msg["action"] == "exchange":
                from_name = self.logged_sock2name[from_sock]
                the_guys = self.group.list_me(from_name)
                #said = msg["from"]+msg["message"]
                said2 = text_proc(msg["message"], from_name)
                self.indices[from_name].add_msg_and_index(said2)
                for g in the_guys[1:]:
                    to_sock = self.logged_name2sock[g]
                    self.indices[g].add_msg_and_index(said2)
                    mysend(to_sock, json.dumps({"action":"exchange", "from":msg["from"], "message":msg["message"]}))
            elif isinstance(msg.get("action"), str) and msg["action"].startswith("game_"):
                self.handle_game_msg(from_sock, msg)
            
#==============================================================================
#                 listing available peers
#==============================================================================
            elif msg["action"] == "list":
                from_name = self.logged_sock2name[from_sock]
                msg = self.group.list_all()
                mysend(from_sock, json.dumps({"action":"list", "results":msg}))
#==============================================================================
#             retrieve a sonnet
#==============================================================================
            elif msg["action"] == "poem":
                poem_indx = int(msg["target"])
                from_name = self.logged_sock2name[from_sock]
                print(from_name + ' asks for ', poem_indx)
                poem = self.sonnet.get_poem(poem_indx)
                poem = '\n'.join(poem).strip()
                print('here:\n', poem)
                mysend(from_sock, json.dumps({"action":"poem", "results":poem}))
#==============================================================================
#                 time
#==============================================================================
            elif msg["action"] == "time":
                ctime = time.strftime('%d.%m.%y,%H:%M', time.localtime())
                mysend(from_sock, json.dumps({"action":"time", "results":ctime}))
#==============================================================================
#                 search
#==============================================================================
            elif msg["action"] == "search":
                term = msg["target"]
                from_name = self.logged_sock2name[from_sock]
                print('search for ' + from_name + ' for ' + term)
                # search_rslt = (self.indices[from_name].search(term))
                search_rslt = '\n'.join([x[-1] for x in self.indices[from_name].search(term)])
                print('server side search: ' + search_rslt)
                mysend(from_sock, json.dumps({"action":"search", "results":search_rslt}))
#==============================================================================
#                 keywords
#==============================================================================
            elif msg["action"] == "keywords":
                from_name = self.logged_sock2name[from_sock]
                print(from_name + ' requested keywords')
                msgs = self.indices[from_name].msgs
                result = nlp_utils.format_keywords(msgs)
                mysend(from_sock, json.dumps({"action":"keywords", "results":result}))
#==============================================================================
#                 summary
#==============================================================================
            elif msg["action"] == "summary":
                from_name = self.logged_sock2name[from_sock]
                print(from_name + ' requested summary')
                msgs = self.indices[from_name].msgs
                result = nlp_utils.generate_summary(msgs)
                mysend(from_sock, json.dumps({"action":"summary", "results":result}))
#==============================================================================
# the "from" guy has had enough (talking to "to")!
#==============================================================================
            elif msg["action"] == "disconnect":
                from_name = self.logged_sock2name[from_sock]
                the_guys = self.group.list_me(from_name)
                self.group.disconnect(from_name)
                the_guys.remove(from_name)
                if len(the_guys) == 1:  # only one left
                    g = the_guys.pop()
                    to_sock = self.logged_name2sock[g]
                    mysend(to_sock, json.dumps({"action":"disconnect"}))
#==============================================================================
#                 the "from" guy really, really has had enough
#==============================================================================

        else:
            #client died unexpectedly
            self.logout(from_sock)

#==============================================================================
# main loop, loops *forever*
#==============================================================================
    def run(self):
        print ('starting server...')
        while(1):
           read,write,error=select.select(self.all_sockets,[],[])
           print('checking logged clients..')
           for logc in list(self.logged_name2sock.values()):
               if logc in read:
                   self.handle_msg(logc)
           print('checking new clients..')
           for newc in self.new_clients[:]:
               if newc in read:
                   self.login(newc)
           print('checking for new connections..')
           if self.server in read :
               #new client request
               sock, address=self.server.accept()
               self.new_client(sock)

def main():
    server=Server()
    server.run()

if __name__ == '__main__':
    main()
