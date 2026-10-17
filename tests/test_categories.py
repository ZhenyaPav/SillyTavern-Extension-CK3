import json
from pathlib import Path
import tempfile
import unittest

from companion.category_rules import Classifier, build_index, extract_event_id


class Categories(unittest.TestCase):
    def test_real_debug_info_extracts_only_event_id(self):
        key = 'Event: pay_homage.0101 Character: King\nRoot: King\nSaved event targets:\nother.1234'
        self.assertEqual(extract_event_id(key), 'pay_homage.0101')
        self.assertEqual(extract_event_id('hunt.0550|Making Camp'), 'hunt.0550')
        self.assertEqual(extract_event_id('unknown title with secret.1234 in its prose'), '')

    def test_recurring_events_and_consequential_siblings_have_distinct_filters(self):
        classify = Classifier(index_path=None).classify
        cases = {
            'pay_homage.0101': 'vassal_routine',
            'spymaster_task.0346': 'reports',
            'spymaster_task.0399': 'reports',
            'spymaster_task.1001': 'scheme_finales',
            'vassal_interaction.0030': 'vassal_routine',
            'vassal_interaction.0040': 'politics',
            'vassal_interaction.0005': 'politics',
            'char_interaction.0251': 'vassal_routine',
            'char_interaction.0110': 'politics',
            'ep3_decisions_event.2001': 'vassal_routine',
            'ep3_decisions_event.2010': 'vassal_routine',
            'tgp_interaction_event.0010': 'war',
            'court_ongoing.1001': 'scheme_progress',
            'scheme_critical_moments.0011': 'scheme_progress',
            'scheme_critical_moments.1001': 'scheme_finales',
            'hostile_scheme_discovery.3002': 'scheme_finales',
            'murder_outcome.2000': 'scheme_finales',
            'unknown_mod.0001': 'other',
        }
        for event_id, expected in cases.items():
            with self.subTest(event_id=event_id):
                self.assertEqual(classify(event_id), expected)

    def test_generated_index_handles_quotes_comments_overrides_and_zero_padding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            activities = root/'activities'
            activities.mkdir()
            (activities/'events.txt').write_text('''namespace = custom
# ignored.1 = { }
custom.0001 = { desc = "fake.2 = { # not code }" option = { name = x } }
custom.0002 = { desc = x }
''')
            religion = root/'religion_events'
            religion.mkdir()
            (religion/'events.txt').write_text('''custom.1 = { id_override_priority = 2 }
custom.2 = { option = { id_override_priority = 10 } }
''')
            index = build_index(root)
            self.assertEqual(index['events'], {'custom.1': 'religion', 'custom.2': 'other'})
            self.assertEqual(index['ambiguous'], ['custom.2'])
            path = root/'index.json'
            path.write_text(json.dumps(index))
            classify = Classifier(path).classify
            self.assertEqual(classify('custom.0001'), 'religion')
            self.assertEqual(classify('custom.0002'), 'other')

    def test_parent_and_delayed_choice_keep_same_classification(self):
        from test_bridge import bridge
        with tempfile.TemporaryDirectory() as temporary:
            store = bridge.Store(Path(temporary), category_index=None)
            fields = {'key': 'Event: scheme_critical_moments.1001 Character: King', 'title': 'Result'}
            store.accept({'kind': 'event', **fields, 'count': '2', 'body': 'The plot ends.'})
            for i in range(2):
                store.accept({'kind': 'option', **fields, 'index': str(i), 'text': str(i)})
            store.accept({'kind': 'choice', **fields, 'text': '0'})
            parent, choice = store.events(0, 50)
            self.assertEqual(parent['category'], 'scheme_finales')
            self.assertEqual(parent['eventId'], 'scheme_critical_moments.1001')
            for key in ('eventKey', 'category', 'eventId'):
                self.assertEqual(parent[key], choice[key])
            self.assertNotIn('Event:', parent['body'])
            store.db.close()
