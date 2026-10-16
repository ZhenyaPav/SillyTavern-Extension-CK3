import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bridge', ROOT/'companion/bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def packet(kind, **fields):
    return f'STCK3_V1_BEGIN {kind}\n' + ''.join(f'STCK3_V1_FIELD {key}\n{value}\n' for key, value in fields.items()) + 'STCK3_V1_END\n'


class Frames(unittest.TestCase):
    def test_unavailable_companion_does_not_prevent_game_launch(self):
        args = SimpleNamespace(command=[sys.executable, '-c', 'raise SystemExit(7)'])
        with patch.object(bridge, 'ensure', side_effect=RuntimeError('unavailable')):
            self.assertEqual(bridge.run_game(args), 7)

    def test_multiline_localized_text_with_log_prefixes(self):
        parser = bridge.FrameParser()
        data = packet('event', title='Un invité', body='First line.\n\n"Second" [line].', date='873.2.19')
        result = None
        for line in data.splitlines():
            result = parser.feed('[12:34:56][I][effect.cpp:10]: '+line)
        self.assertEqual(result['body'], 'First line.\n\n"Second" [line].')
        self.assertEqual(result['title'], 'Un invité')

    def test_real_engine_source_locations_and_console_echo(self):
        for location in ('effect console command line: 1',
                         'common/decisions/ck3st_test.txt line: 8 (ck3st_test_decision:effect)',
                         'common/on_action/ck3st_on_actions.txt line: 34 (ck3st_war_started:effect)'):
            with self.subTest(location=location):
                parser = bridge.FrameParser()
                self.assertIsNone(parser.feed('[13:17:39][I][console.cpp:205]: Running console command: effect info_log = ck3st_export_event'))
                data = packet('event', key='ck3st_test.1', title='A messenger arrives',
                              body='A messenger bows.\n\n"My liege."', count='2')
                lines = data.splitlines()
                lines[0] = f'[13:17:39][I][jomini_effect_impl.cpp:450]: file: {location}: '+lines[0]
                frames = [frame for line in lines if (frame := parser.feed(line))]
                self.assertEqual(len(frames), 1)
                self.assertEqual(frames[0]['title'], 'A messenger arrives')
                self.assertEqual(frames[0]['body'], 'A messenger bows.\n\n"My liege."')

    def test_interrupted_and_oversized_frames_resynchronize(self):
        parser = bridge.FrameParser()
        parser.feed('STCK3_V1_BEGIN event')
        parser.feed('x' * (bridge.MAX_FRAME + 1))
        result = None
        for line in packet('heartbeat', date='1081.3.19').splitlines():
            result = parser.feed(line)
        self.assertEqual(result, {'kind': 'heartbeat', 'date': '1081.3.19'})

    def test_partial_utf8_rotation_and_truncation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'game.log'
            path.write_text('old log\n')
            tail = bridge.Tail(path)
            data = packet('milestone', text='Élise was born.').encode()
            split = data.index('É'.encode()) + 1
            with path.open('ab') as out:
                out.write(data[:split])
            self.assertEqual(tail.read(), [])
            with path.open('ab') as out:
                out.write(data[split:])
            self.assertEqual(tail.read()[0]['text'], 'Élise was born.')
            path.rename(path.with_suffix('.old'))
            path.write_text(packet('heartbeat', date='1.1.1'))
            self.assertEqual(tail.read()[0]['date'], '1.1.1')
            path.write_text(packet('heartbeat', date='2.1.1'))
            # A same-size overwrite is indistinguishable from no change. Real CK3
            # truncation is observed at startup; test an observable truncate here.
            path.write_text('')
            self.assertEqual(tail.read(), [])
            path.write_text(packet('heartbeat', date='2.1.1'))
            self.assertEqual(tail.read()[0]['date'], '2.1.1')


class Assembly(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = bridge.Store(Path(self.temporary.name))
        self.header = {'kind': 'event', 'key': 'test.1', 'title': 'Court', 'body': 'A visitor.',
                       'date': '1081.3.19', 'player': 'King', 'count': '2'}

    def tearDown(self):
        self.store.db.close()
        self.temporary.cleanup()

    def option(self, index, text='Same label'):
        return {'kind': 'option', 'key': 'test.1', 'title': 'Court', 'index': str(index), 'text': text}

    def test_out_of_order_options_and_choice_wait_for_complete_event(self):
        self.store.accept(self.option(1))
        self.store.accept({'kind': 'choice', 'key': 'test.1', 'title': 'Court', 'text': 'Same label', 'date': '1081.3.20'})
        self.store.accept(self.header)
        self.assertEqual(self.store.events(0, 50), [])
        self.store.accept(self.option(0))
        events = self.store.events(0, 50)
        self.assertEqual([e['kind'] for e in events], ['event', 'choice'])
        self.assertEqual(events[0]['options'], ['Same label', 'Same label'])
        self.assertEqual(events[1]['date'], '1081.3.20')

    def test_replayed_gui_snapshot_deduplicates(self):
        for _ in range(2):
            for frame in [self.header, self.option(0), self.option(1)]:
                self.store.accept(frame)
        self.assertEqual(len(self.store.events(0, 50)), 1)

    def test_incomplete_event_is_diagnostic_not_a_partial_chat_message(self):
        self.store.accept(self.header)
        next(iter(self.store.pending.values()))['created'] -= 4
        self.assertEqual(self.store.status()['incompleteEvents'], 1)
        self.assertEqual(self.store.events(0, 50), [])

    def test_heartbeat_updates_close_date_without_chat_spam(self):
        self.store.lifecycle('opened')
        self.store.accept({'kind': 'heartbeat', 'date': '1081.3.20', 'player': 'King'})
        self.store.lifecycle('closed')
        events = self.store.events(0, 50)
        self.assertEqual([e['kind'] for e in events], ['game_opened', 'game_closed'])
        self.assertEqual(events[-1]['date'], '1081.3.20')

    def test_spool_and_feed_identity_survive_restart(self):
        self.store.emit('milestone', text='Saved')
        feed = self.store.get('feed_id')
        other = bridge.Store(Path(self.temporary.name))
        self.assertEqual(other.get('feed_id'), feed)
        self.assertEqual(other.events(0, 50)[0]['text'], 'Saved')
        other.db.close()

    def test_no_clipboard_or_ui_markup_in_message_fields(self):
        self.assertEqual(bridge.plain('#bold The @gold_icon!King#!\n\n[Label|E]'), 'The King\n\nLabel')

    def test_resolved_engine_links_keep_names_and_punctuation(self):
        text = ('My \x15ONCLICK:CHARACTER,57785 \x15TOOLTIP:CHARACTER,57785 '
                '\x15L Governor\x15!\x15!\x15! found a '
                '\x15TOOLTIP:SECRET_TYPE,secret_lover \x15L; Lover Secret\x15!\x15!.\n\n'
                '\x16warning_icon!\x15weak (A warning)\x15!')
        self.assertEqual(bridge.plain(text), 'My Governor found a Lover Secret.\n\n(A warning)')


if __name__ == '__main__':
    unittest.main()
