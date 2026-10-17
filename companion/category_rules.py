"""Classify event IDs from installed scripts, without interpreting event prose."""
import collections
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / 'event-categories.json'
DEFAULT_INDEX = ROOT / '.runtime/mod/ck3st-event-categories.json'
EVENT_ID = r'[A-Za-z_][A-Za-z_0-9]*\.[0-9]+'


def canonical_id(event_id):
    namespace, number = event_id.rsplit('.', 1)
    return f'{namespace}.{int(number)}'


def extract_event_id(key):
    match = re.search(r'(?:^|\n)Event:\s*(' + EVENT_ID + r')(?=\s|\||$)', key)
    if not match:
        match = re.match(r'^(' + EVENT_ID + r')(?=\||$)', key)
    return match[1] if match else ''


class Classifier:
    def __init__(self, index_path=DEFAULT_INDEX):
        self.rules = json.loads(RULES_PATH.read_text())
        self.categories = {c['id'] for c in self.rules['categories']}
        self.index, self.index_loaded = {}, False
        if index_path and Path(index_path).exists():
            try:
                index = json.loads(Path(index_path).read_text())
                if index['version'] == self.rules['version']:
                    self.index = {canonical_id(k): v for k, v in index['events'].items()
                                  if v in self.categories and re.fullmatch(EVENT_ID, k)}
                    self.index_loaded = True
            except (OSError, ValueError, KeyError, TypeError):
                pass  # Known namespace rules still work; unknown events remain enabled.

    def classify(self, event_id, source=''):
        if not re.fullmatch(EVENT_ID, event_id):
            return 'other'
        namespace, number = event_id.rsplit('.', 1)
        number = int(number)
        for rule in self.rules['eventRules']:
            if namespace == rule['namespace'] and (number in rule.get('numbers', []) or
                    any(low <= number <= high for low, high in rule.get('ranges', []))):
                return rule['category']
        for rule in self.rules['namespaceRules']:
            if (namespace in rule.get('names', []) or
                    any(namespace.startswith(p) for p in rule.get('prefixes', [])) or
                    any(namespace.endswith(p) for p in rule.get('suffixes', []))):
                return rule['category']
        if source:
            for rule in self.rules['pathRules']:
                if any(source.startswith(p) for p in rule['prefixes']):
                    return rule['category']
        return self.index.get(canonical_id(event_id), 'other')


def event_definitions(text):
    """Yield top-level IDs and override priorities; ignore quoted/commented code."""
    masked = re.sub(r'"(?:\\.|[^"\\])*"|#[^\n]*',
                    lambda m: '\n' * m[0].count('\n') if '\n' in m[0] else ' ', text)
    depth, start, top = 0, 0, []
    for match in re.finditer(r'[{}]', masked):
        if match[0] == '{':
            if depth == 0:
                head = masked[start:match.start()]
                name = re.search(r'(' + EVENT_ID + r')\s*=\s*$', head)
                event_id = name[1] if name else ''
                top = []
            elif depth == 1:
                top.append(masked[start:match.start()])
            depth += 1
        else:
            if depth == 1:
                top.append(masked[start:match.start()])
            depth -= 1
            if depth == 0 and event_id:
                priority = re.search(r'\bid_override_priority\s*=\s*(-?\d+)', ''.join(top))
                yield event_id, int(priority[1]) if priority else 0
        start = match.end()


def build_index(events_dir):
    classifier = Classifier(index_path=None)
    events, priorities, ambiguous, hashes = {}, {}, set(), {}
    for path in sorted(events_dir.rglob('*.txt')):
        source = path.relative_to(events_dir).as_posix()
        raw = path.read_bytes()
        hashes[source] = hashlib.sha256(raw).hexdigest()
        for event_id, priority in event_definitions(raw.decode('utf-8-sig', errors='replace')):
            event_id = canonical_id(event_id)
            category = classifier.classify(event_id, source)
            previous = priorities.get(event_id)
            if previous is None or priority > previous:
                events[event_id], priorities[event_id] = category, priority
                ambiguous.discard(event_id)
            elif priority == previous and events[event_id] != category:
                events[event_id] = 'other'
                ambiguous.add(event_id)
    return {'version': classifier.rules['version'], 'events': events,
            'counts': dict(collections.Counter(events.values())),
            'ambiguous': sorted(ambiguous), 'source_sha256': hashes}
