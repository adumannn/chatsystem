"""
Test suite for the Chat System
Tests the core components without requiring network connections
"""

import unittest
import sys
import os
import json
import pickle
import socket

# Make the `src/` package importable when tests are run from any cwd.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.abspath(os.path.join(_HERE, os.pardir, 'src'))
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Import modules to test
from chat_utils import *
from chat_group import Group, S_ALONE, S_TALKING
from indexer_good import Index, PIndex
from auth_store import PasswordAuthenticator, is_valid_username
from chat_server import Server

try:
    from chat_bot_client import ChatBotClient
except ImportError:
    ChatBotClient = None


class TestChatUtils(unittest.TestCase):
    """Test chat utility functions"""
    
    def test_print_state_offline(self):
        """Test print_state with offline state"""
        # Just verify it doesn't crash
        try:
            print_state(S_OFFLINE)
        except Exception as e:
            self.fail(f"print_state raised exception: {e}")
    
    def test_print_state_connected(self):
        """Test print_state with connected state"""
        try:
            print_state(S_CONNECTED)
        except Exception as e:
            self.fail(f"print_state raised exception: {e}")
    
    def test_print_state_loggedin(self):
        """Test print_state with logged in state"""
        try:
            print_state(S_LOGGEDIN)
        except Exception as e:
            self.fail(f"print_state raised exception: {e}")
    
    def test_print_state_chatting(self):
        """Test print_state with chatting state"""
        try:
            print_state(S_CHATTING)
        except Exception as e:
            self.fail(f"print_state raised exception: {e}")
    
    def test_constants_defined(self):
        """Test that state constants are defined"""
        self.assertEqual(S_OFFLINE, 0)
        self.assertEqual(S_CONNECTED, 1)
        self.assertEqual(S_LOGGEDIN, 2)
        self.assertEqual(S_CHATTING, 3)
    
    def test_size_spec(self):
        """Test SIZE_SPEC constant"""
        self.assertEqual(SIZE_SPEC, 5)
    
    def test_menu_defined(self):
        """Test that menu is defined and non-empty"""
        self.assertTrue(len(menu) > 0)
        self.assertIn('time', menu)
        self.assertIn('who', menu)
        self.assertIn('connect', menu)


class TestChatGroup(unittest.TestCase):
    """Test chat group functionality"""
    
    def setUp(self):
        """Set up test fixtures"""
        self.group = Group()
    
    def test_join(self):
        """Test user joining"""
        self.group.join('alice')
        self.assertTrue(self.group.is_member('alice'))
        self.assertEqual(self.group.members['alice'], S_ALONE)
    
    def test_join_multiple_users(self):
        """Test multiple users joining"""
        self.group.join('alice')
        self.group.join('bob')
        self.group.join('charlie')
        self.assertTrue(self.group.is_member('alice'))
        self.assertTrue(self.group.is_member('bob'))
        self.assertTrue(self.group.is_member('charlie'))
    
    def test_is_member_nonexistent(self):
        """Test is_member with non-existent user"""
        self.assertFalse(self.group.is_member('nonexistent'))
    
    def test_leave(self):
        """Test user leaving"""
        self.group.join('alice')
        self.group.leave('alice')
        self.assertFalse(self.group.is_member('alice'))
    
    def test_connect_creates_group(self):
        """Test connecting two users creates a group"""
        self.group.join('alice')
        self.group.join('bob')
        self.group.connect('alice', 'bob')
        
        # Both should be talking
        self.assertEqual(self.group.members['alice'], S_TALKING)
        self.assertEqual(self.group.members['bob'], S_TALKING)
        
        # They should be in the same group
        alice_list = self.group.list_me('alice')
        self.assertIn('bob', alice_list)
    
    def test_connect_third_user(self):
        """Test third user joining existing conversation"""
        self.group.join('alice')
        self.group.join('bob')
        self.group.join('charlie')
        
        self.group.connect('alice', 'bob')
        self.group.connect('charlie', 'bob')
        
        # All three should be in the same group
        alice_list = self.group.list_me('alice')
        self.assertIn('bob', alice_list)
        self.assertIn('charlie', alice_list)

    def test_invite_idle_user_into_existing_group(self):
        """Test inviting an idle user into the caller's current group"""
        self.group.join('alice')
        self.group.join('bob')
        self.group.join('chatbot')

        self.group.connect('alice', 'bob')
        self.group.connect('alice', 'chatbot')

        alice_list = self.group.list_me('alice')
        bot_list = self.group.list_me('chatbot')

        self.assertIn('bob', alice_list)
        self.assertIn('chatbot', alice_list)
        self.assertIn('alice', bot_list)
        self.assertIn('bob', bot_list)
    
    def test_disconnect(self):
        """Test user disconnecting from chat"""
        self.group.join('alice')
        self.group.join('bob')
        self.group.connect('alice', 'bob')
        
        self.group.disconnect('alice')
        
        # Alice should be alone
        self.assertEqual(self.group.members['alice'], S_ALONE)
    
    def test_disconnect_last_peer(self):
        """Test disconnecting when only two peers remain"""
        self.group.join('alice')
        self.group.join('bob')
        self.group.connect('alice', 'bob')
        
        self.group.disconnect('alice')
        
        # Bob should also be alone now
        self.assertEqual(self.group.members['bob'], S_ALONE)
    
    def test_list_all(self):
        """Test listing all users and groups"""
        self.group.join('alice')
        self.group.join('bob')
        result = self.group.list_all()
        
        self.assertIn('Users', result)
        self.assertIn('Groups', result)
        self.assertIn('alice', result)
        self.assertIn('bob', result)
    
    def test_list_me_alone(self):
        """Test list_me when user is alone"""
        self.group.join('alice')
        result = self.group.list_me('alice')
        
        self.assertEqual(result, ['alice'])
    
    def test_list_me_nonexistent(self):
        """Test list_me with non-existent user"""
        result = self.group.list_me('nonexistent')
        self.assertEqual(result, [])
    
    def test_find_group(self):
        """Test finding which group a user is in"""
        self.group.join('alice')
        self.group.join('bob')
        self.group.connect('alice', 'bob')
        
        found, group_key = self.group.find_group('alice')
        self.assertTrue(found)
        self.assertGreater(group_key, 0)
    
    def test_find_group_alone(self):
        """Test finding group when user is alone"""
        self.group.join('alice')
        found, group_key = self.group.find_group('alice')
        self.assertFalse(found)


class TestIndex(unittest.TestCase):
    """Test indexing functionality"""
    
    def setUp(self):
        """Set up test index"""
        self.index = Index('test_user')
    
    def test_initial_state(self):
        """Test initial index state"""
        self.assertEqual(self.index.name, 'test_user')
        self.assertEqual(self.index.total_msgs, 0)
        self.assertEqual(self.index.total_words, 0)
        self.assertEqual(len(self.index.msgs), 0)
        self.assertEqual(len(self.index.index), 0)
    
    def test_add_msg(self):
        """Test adding a message"""
        self.index.add_msg('Hello world')
        self.assertEqual(self.index.total_msgs, 1)
        self.assertEqual(self.index.msgs[0], 'Hello world')
    
    def test_add_msg_and_index(self):
        """Test adding message with indexing"""
        self.index.add_msg_and_index('Hello world')
        self.assertEqual(self.index.total_msgs, 1)
        self.assertEqual(self.index.total_words, 2)
        self.assertIn('Hello', self.index.index)
        self.assertIn('world', self.index.index)
    
    def test_search_found(self):
        """Test searching for existing term"""
        self.index.add_msg_and_index('Hello world')
        self.index.add_msg_and_index('Hello again')
        
        results = self.index.search('Hello')
        self.assertEqual(len(results), 2)
        
        # Check structure
        for msg_idx, msg in results:
            self.assertIsInstance(msg_idx, int)
            self.assertIsInstance(msg, str)
    
    def test_search_not_found(self):
        """Test searching for non-existent term"""
        self.index.add_msg_and_index('Hello world')
        results = self.index.search('nonexistent')
        self.assertEqual(len(results), 0)
    
    def test_get_msg(self):
        """Test retrieving message by index"""
        self.index.add_msg('First message')
        self.index.add_msg('Second message')
        
        self.assertEqual(self.index.get_msg(0), 'First message')
        self.assertEqual(self.index.get_msg(1), 'Second message')
    
    def test_get_total_words(self):
        """Test total word count"""
        self.index.add_msg_and_index('Hello world')
        self.index.add_msg_and_index('How are you')
        self.assertEqual(self.index.get_total_words(), 5)
    
    def test_get_msg_size(self):
        """Test message count"""
        self.index.add_msg('msg1')
        self.index.add_msg('msg2')
        self.index.add_msg('msg3')
        self.assertEqual(self.index.get_msg_size(), 3)
    
    def test_indexing_duplicate_words(self):
        """Test indexing with duplicate words"""
        self.index.add_msg_and_index('Hello world')
        self.index.add_msg_and_index('Hello again world')
        
        # 'world' should appear in both messages
        world_indices = self.index.index['world']
        self.assertEqual(len(world_indices), 2)


class TestPIndex(unittest.TestCase):
    """Test poem indexing functionality"""
    
    def setUp(self):
        """Set up test PIndex"""
        # Only run if files exist
        if os.path.exists('AllSonnets.txt') and os.path.exists('roman.txt.pk'):
            self.sonnet = PIndex('AllSonnets.txt')
            self.has_files = True
        else:
            self.has_files = False
    
    def test_load_poems(self):
        """Test loading poems"""
        if self.has_files:
            self.assertGreater(self.sonnet.total_msgs, 0)
            self.assertGreater(self.sonnet.total_words, 0)
    
    def test_get_poem(self):
        """Test retrieving a poem"""
        if self.has_files:
            poem = self.sonnet.get_poem(1)
            self.assertIsInstance(poem, list)
            self.assertGreater(len(poem), 0)
    
    def test_search_poems(self):
        """Test searching in poems"""
        if self.has_files:
            results = self.sonnet.search('love')
            # Should find some results for 'love' in sonnets
            self.assertGreaterEqual(len(results), 0)


class TestRoman2Num(unittest.TestCase):
    """Test roman to number conversion"""
    
    def setUp(self):
        """Load roman dictionary from pickle file"""
        if os.path.exists('roman.txt.pk'):
            with open('roman.txt.pk', 'rb') as f:
                self.int2roman = pickle.load(f)
                self.roman2int = pickle.load(f)
            self.has_file = True
        else:
            self.has_file = False
    
    def test_int2roman_exists(self):
        """Test that int2roman dictionary exists"""
        if self.has_file:
            self.assertIsInstance(self.int2roman, dict)
            self.assertGreater(len(self.int2roman), 0)
    
    def test_int2roman_contains_one(self):
        """Test int2roman contains 1 -> I"""
        if self.has_file:
            self.assertIn(1, self.int2roman)
            self.assertEqual(self.int2roman[1], 'I')


class TestPasswordAuthenticator(unittest.TestCase):
    """Test basic username/password authentication"""

    def setUp(self):
        import tempfile

        self.tmpdir = tempfile.TemporaryDirectory()
        self.auth_path = os.path.join(self.tmpdir.name, 'users.json')
        self.auth = PasswordAuthenticator(self.auth_path)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_username_validation(self):
        self.assertTrue(is_valid_username('alice_1'))
        self.assertFalse(is_valid_username('../alice'))
        self.assertFalse(is_valid_username('alice bob'))

    def test_creates_and_reuses_password(self):
        ok, status, _ = self.auth.authenticate('alice', 'secret')
        self.assertTrue(ok)
        self.assertEqual(status, 'ok')

        auth2 = PasswordAuthenticator(self.auth_path)
        ok, status, _ = auth2.authenticate('alice', 'secret')
        self.assertTrue(ok)
        self.assertEqual(status, 'ok')

    def test_rejects_missing_or_wrong_password(self):
        ok, status, _ = self.auth.authenticate('alice', '')
        self.assertFalse(ok)
        self.assertEqual(status, 'password-required')

        self.auth.authenticate('alice', 'secret')
        ok, status, _ = self.auth.authenticate('alice', 'wrong')
        self.assertFalse(ok)
        self.assertEqual(status, 'wrong-password')

    def test_password_is_not_stored_plaintext(self):
        self.auth.authenticate('alice', 'secret')
        with open(self.auth_path, 'r', encoding='utf-8') as f:
            raw = f.read()
        self.assertNotIn('"secret"', raw)


class TestTicTacToeServer(unittest.TestCase):
    """Test server-managed Tic-Tac-Toe sessions"""

    def setUp(self):
        self.server = Server.__new__(Server)
        self.server.group = Group()
        self.server.logged_name2sock = {}
        self.server.logged_sock2name = {}
        self.server.games = {}
        self.server.player_game = {}
        self.server.game_scores = {}
        self.server.game_seq = 0

        self.alice_client, self.alice_server = socket.socketpair()
        self.bob_client, self.bob_server = socket.socketpair()
        for sock in (
            self.alice_client, self.alice_server,
            self.bob_client, self.bob_server,
        ):
            sock.settimeout(1)

        self.server.group.join('alice')
        self.server.group.join('bob')
        self.server.logged_name2sock = {
            'alice': self.alice_server,
            'bob': self.bob_server,
        }
        self.server.logged_sock2name = {
            self.alice_server: 'alice',
            self.bob_server: 'bob',
        }

    def tearDown(self):
        for sock in (
            self.alice_client, self.alice_server,
            self.bob_client, self.bob_server,
        ):
            sock.close()

    def recv_json(self, sock):
        return json.loads(myrecv(sock))

    def test_winner_detection(self):
        self.assertEqual(
            Server.tictactoe_winner(['X', 'X', 'X', '', '', '', '', '', '']),
            'X',
        )
        self.assertEqual(
            Server.tictactoe_winner(['X', 'O', 'X', 'X', 'O', 'O', 'O', 'X', 'X']),
            'draw',
        )

    def test_game_invite_moves_and_scoreboard(self):
        self.server.handle_game_msg(
            self.alice_server,
            {'action': 'game_invite', 'target': 'bob'},
        )
        alice_start = self.recv_json(self.alice_client)
        bob_start = self.recv_json(self.bob_client)
        self.assertEqual(alice_start['action'], 'game_start')
        self.assertEqual(bob_start['mark'], 'O')
        game_id = alice_start['game_id']

        for player_sock, cell in (
            (self.alice_server, 0),
            (self.bob_server, 3),
            (self.alice_server, 1),
            (self.bob_server, 4),
            (self.alice_server, 2),
        ):
            self.server.handle_game_msg(
                player_sock,
                {'action': 'game_move', 'game_id': game_id, 'cell': cell},
            )
            alice_update = self.recv_json(self.alice_client)
            bob_update = self.recv_json(self.bob_client)

        self.assertEqual(alice_update['status'], 'finished')
        self.assertEqual(alice_update['winner'], 'alice')
        self.assertEqual(bob_update['winner'], 'alice')

        alice_scores = self.recv_json(self.alice_client)
        bob_scores = self.recv_json(self.bob_client)
        self.assertEqual(alice_scores['action'], 'game_scoreboard')
        self.assertEqual(bob_scores['rankings'][0]['player'], 'alice')
        self.assertEqual(bob_scores['rankings'][0]['wins'], 1)


@unittest.skipIf(ChatBotClient is None, "ollama package is not installed")
class TestChatBotClient(unittest.TestCase):
    """Test chatbot context, personality, and group mention behavior"""

    class FakeChatBot(ChatBotClient):
        def __init__(self):
            super().__init__(name='chatbot', password='chatbot', timeout=1)
            self.socket, self.peer_socket = socket.socketpair()
            self.generated = []

        def generate_response(self, message, from_user='user',
                              peer_key='default', remember=True):
            self.generated.append((message, from_user, peer_key))
            context = self._get_context(peer_key)
            context.append({'role': 'assistant', 'content': 'bot reply'})
            return 'bot reply'

        def close(self):
            self.socket.close()
            self.peer_socket.close()

    def setUp(self):
        self.bot = self.FakeChatBot()
        self.bot.state = S_CHATTING
        self.bot.peer = 'alice'
        self.bot.peers_in_chat = ['alice', 'bob']

    def tearDown(self):
        self.bot.close()

    def read_bot_send(self):
        return json.loads(myrecv(self.bot.peer_socket))

    def test_group_context_is_kept_until_bot_is_mentioned(self):
        self.bot._handle(json.dumps({
            'action': 'exchange',
            'from': '[alice]',
            'message': 'Bob and I picked SQLite for the project.',
        }))
        self.assertEqual(self.bot.generated, [])

        self.bot._handle(json.dumps({
            'action': 'exchange',
            'from': '[bob]',
            'message': '@chatbot what database did we pick?',
        }))
        sent = self.read_bot_send()

        self.assertEqual(sent['action'], 'exchange')
        self.assertEqual(sent['message'], 'bot reply')
        self.assertEqual(len(self.bot.generated), 1)
        peer_key = self.bot.generated[0][2]
        context_text = '\n'.join(
            item['content'] for item in self.bot.conversations[peer_key]
        )
        self.assertIn('SQLite', context_text)

    def test_personality_can_be_changed_from_chat(self):
        self.bot._handle(json.dumps({
            'action': 'exchange',
            'from': '[alice]',
            'message': '@chatbot personality: answer like a concise mentor',
        }))
        sent = self.read_bot_send()

        self.assertEqual(sent['message'], 'Personality updated.')
        self.assertIn('concise mentor', self.bot.personality)
        self.assertEqual(self.bot.conversations, {})


class TestIntegration(unittest.TestCase):
    """Integration tests for the chat system"""
    
    def test_index_persistence_simulation(self):
        """Test that index can be serialized/deserialized"""
        index = Index('testuser')
        index.add_msg_and_index('Test message 1')
        index.add_msg_and_index('Test message 2')
        
        # Serialize
        import pickle
        import io
        buffer = io.BytesIO()
        pickle.dump(index, buffer)
        buffer.seek(0)
        
        # Deserialize
        loaded_index = pickle.load(buffer)
        
        self.assertEqual(loaded_index.name, 'testuser')
        self.assertEqual(loaded_index.total_msgs, 2)
        results = loaded_index.search('Test')
        self.assertEqual(len(results), 2)


if __name__ == '__main__':
    print("=" * 60)
    print("Chat System Test Suite")
    print("=" * 60)
    
    # Run tests with verbosity
    unittest.main(verbosity=2)
