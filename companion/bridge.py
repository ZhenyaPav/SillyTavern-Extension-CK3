#!/usr/bin/env python3
"""Local, read-only CK3 log bridge. Python 3.11+, standard library only."""
import argparse
import collections
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOGS = Path.home() / '.local/share/Paradox Interactive/Crusader Kings III/logs'
DEFAULT_STATE = ROOT / '.runtime/bridge'
DEFAULT_ORIGIN = 'https://nova-st.pve.realm.bz'
PREFIX = re.compile(r'^\[\d{2}:\d{2}:\d{2}\](?:\[[^\]\n]*\])+: ?')
SOURCE_PREFIX = re.compile(r'^file: .+? line: \d+(?: \([^\r\n]*\))?: (?=STCK3_V1_BEGIN [a-z_]+$)')
MAX_FRAME = 262144


def plain(text):
    """Remove CK3 formatting while retaining prose, paragraphs, and link labels."""
    # info_log serializes resolved GUI markup using control bytes, rather than
    # the source localization's #format and @icon spellings.
    text = re.sub(r'\x15(?:!|[^\s\x15]+[ \t]?)', '', str(text))
    text = re.sub(r'[@\x16][^!\s]+!', '', text)
    text = re.sub(r'#(?:!|[A-Za-z_][A-Za-z_0-9;=.,+-]*\s*)', '', text)
    text = re.sub(r'\[([^\[\]|]+)\|[^\[\]]+\]', r'\1', text)
    return '\n'.join(line.rstrip() for line in text.splitlines()).strip()


class FrameParser:
    def __init__(self):
        self.kind = None
        self.fields = {}
        self.field = None
        self.size = 0

    def feed(self, line):
        line = PREFIX.sub('', line.rstrip('\r\n'))
        line = SOURCE_PREFIX.sub('', line)
        if line.startswith('STCK3_V1_BEGIN '):
            self.kind = line.split(' ', 1)[1].strip()
            self.fields, self.field, self.size = {}, None, 0
        elif self.kind:
            self.size += len(line)
            if self.size > MAX_FRAME:
                self.kind = None
                logging.warning('Discarded oversized game frame')
            elif line == 'STCK3_V1_END':
                result = {'kind': self.kind, **{k: '\n'.join(v).strip() for k, v in self.fields.items()}}
                self.kind = None
                return result
            elif line.startswith('STCK3_V1_FIELD '):
                self.field = line.split(' ', 1)[1].strip()
                self.fields[self.field] = []
            elif self.field:
                self.fields[self.field].append(line)
        return None


class Store:
    def __init__(self, directory):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.lock = threading.RLock()
        self.path = directory
        token = directory / 'token'
        if not token.exists():
            try:
                with open(token, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as out:
                    out.write(secrets.token_urlsafe(32))
            except FileExistsError:
                pass
        self.token = token.read_text().strip()
        self.db = sqlite3.connect(directory / 'events.sqlite3', check_same_thread=False)
        (directory / 'events.sqlite3').chmod(0o600)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, payload TEXT NOT NULL);
        ''')
        if not self.get('feed_id'):
            self.set('feed_id', secrets.token_hex(16))
        self.context = self.get('context') or {}
        self.game_running = False
        self.deadline = None
        self.last_frame = None
        self.frame_count = 0
        self.frame_counts = collections.Counter()
        self.pending = collections.OrderedDict()
        self.emitted = {}
        self.recent = {}
        self.frames_seen = {}
        self.stopping = False

    def get(self, key):
        with self.lock:
            row = self.db.execute('SELECT value FROM metadata WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def set(self, key, value):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', (key, json.dumps(value)))
            self.db.commit()

    def emit(self, kind, **fields):
        data = {'kind': kind, 'receivedAt': time.time(), **fields}
        with self.lock:
            cursor = self.db.execute('INSERT INTO events(payload) VALUES (?)', (json.dumps(data),))
            self.db.commit()
            return cursor.lastrowid

    def events(self, after, limit):
        with self.lock:
            rows = self.db.execute('SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?', (after, limit)).fetchall()
            return [{'seq': seq, **json.loads(payload)} for seq, payload in rows]

    def status(self):
        with self.lock:
            latest = self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
            return {'version': 1, 'feedId': self.get('feed_id'), 'latest': latest,
                    'gameRunning': self.game_running, 'context': self.context,
                    'lastFrameAt': self.last_frame, 'framesReceived': self.frame_count,
                    'frameCounts': dict(self.frame_counts),
                    'pendingEvents': len(self.pending),
                    'incompleteEvents': sum(time.monotonic()-v['created'] > 3 for v in self.pending.values())}

    def lifecycle(self, action):
        with self.lock:
            if action == 'opened':
                if self.game_running:
                    raise ValueError('Another game launch is already active')
                self.context = {}
                self.pending.clear()
                self.emitted.clear()
                self.recent.clear()
                self.set('context', {})
                self.game_running, self.deadline = True, None
                self.emit('game_opened')
            elif action == 'closed':
                if self.game_running:
                    self.emit('game_closed', **self.context)
                self.game_running = False
                # Persist the outbox after exit; leave time for the browser to receive it.
                self.deadline = time.monotonic() + 120
            else:
                raise ValueError('Unknown lifecycle action')

    def accept(self, frame):
        with self.lock:
            now = time.monotonic()
            fingerprint = hashlib.sha256(json.dumps(frame, sort_keys=True).encode()).hexdigest()
            self.frames_seen = {k: v for k, v in self.frames_seen.items() if now-v < 2}
            if fingerprint in self.frames_seen:
                return
            self.frames_seen[fingerprint] = now
            self.last_frame, self.frame_count = time.time(), self.frame_count + 1
            kind = frame.get('kind')
            self.frame_counts[kind] += 1
            for field in ('date', 'player'):
                if frame.get(field):
                    self.context[field] = plain(frame[field])
            self.set('context', self.context)
            if kind in ('heartbeat', 'diagnostic'):
                return
            if kind in ('session_loaded', 'character_changed'):
                if kind == 'session_loaded':
                    self.pending.clear()
                    self.emitted.clear()
                    self.recent.clear()
                self.emit(kind, **self.context)
                return
            if kind == 'milestone':
                self.emit(kind, **self.context, text=plain(frame.get('text', '')))
                return
            if kind not in ('event', 'option', 'choice'):
                logging.warning('Unknown frame kind: %s', kind)
                return
            key = hashlib.sha256((frame.get('key', '')+'\0'+frame.get('title', '')).encode()).hexdigest()
            pending = self.pending.setdefault(key, {'created': time.monotonic(), 'options': {}, 'choices': []})
            if kind == 'event':
                pending['header'] = frame
                try:
                    pending['expected'] = int(frame.get('count', '0'))
                    if not 0 <= pending['expected'] <= 256:
                        raise ValueError()
                except ValueError:
                    logging.warning('Invalid option count; frame retained for diagnostics')
                    return
            elif kind == 'option':
                try:
                    index = int(frame['index'])
                    if not 0 <= index < 256:
                        raise ValueError()
                except (KeyError, ValueError):
                    logging.warning('Option frame has invalid index')
                    return
                pending['options'][index] = plain(frame.get('text', ''))
            else:
                choice = {'date': plain(frame.get('date', '')), 'text': plain(frame.get('text', ''))}
                if choice not in pending['choices']:
                    pending['choices'].append(choice)
            self.flush(key)
            # Incomplete snapshots are never silently injected as complete events.
            # Bound memory; the browser exposes a diagnostic when frames are incomplete.
            if len(self.pending) > 256:
                self.pending.popitem(last=False)
                logging.warning('Oldest incomplete snapshot evicted; check game export diagnostics')

    def flush(self, key):
        pending = self.pending[key]
        header = pending.get('header')
        if header and all(i in pending['options'] for i in range(pending.get('expected', 257))):
            event = {k: plain(header.get(k, '')) for k in ('date', 'player', 'title', 'body', 'opening', 'signature')}
            event['options'] = [pending['options'][i] for i in range(pending['expected'])]
            fingerprint = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()
            now = time.monotonic()
            if self.recent.get(fingerprint, 0) + 2 < now:
                self.emit('event', **event)
                self.recent[fingerprint] = now
            self.emitted[key] = now
            pending.pop('header', None)
        if key in self.emitted and pending['choices']:
            for choice in pending['choices']:
                fingerprint = key + json.dumps(choice, sort_keys=True)
                now = time.monotonic()
                if self.recent.get(fingerprint, 0) + 2 < now:
                    self.emit('choice', **choice)
                    self.recent[fingerprint] = now
            self.pending.pop(key, None)
        now = time.monotonic()
        self.recent = {k: v for k, v in self.recent.items() if now-v < 10}
        self.emitted = {k: v for k, v in self.emitted.items() if now-v < 86400}


class Tail:
    def __init__(self, path):
        self.path, self.identity, self.offset, self.partial = path, None, 0, b''
        self.parser = FrameParser()
        # Start at EOF on initial startup; historical logs are not a new session.
        if path.exists():
            stat = path.stat()
            self.identity, self.offset = (stat.st_dev, stat.st_ino), stat.st_size

    def read(self):
        try:
            stat = self.path.stat()
            identity = stat.st_dev, stat.st_ino
            if identity != self.identity or stat.st_size < self.offset:
                self.identity, self.offset, self.partial = identity, 0, b''
                self.parser = FrameParser()
            with self.path.open('rb') as stream:
                stream.seek(self.offset)
                data = stream.read(1024 * 1024)
                self.offset = stream.tell()
            lines = (self.partial + data).split(b'\n')
            self.partial = lines.pop()
            if len(self.partial) > MAX_FRAME:
                self.partial = b''
            return [frame for line in lines if (frame := self.parser.feed(line.decode('utf-8', errors='replace')))]
        except FileNotFoundError:
            return []


def serve(args):
    store = Store(args.state_dir)
    tails = [Tail(args.logs / name) for name in ('game.log', 'debug.log', 'error.log')]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Do not put tokens or game text in the access log.

        def allowed(self):
            origin = self.headers.get('Origin')
            host = self.headers.get('Host', '')
            return (not origin or origin in args.origin) and host in (
                f'127.0.0.1:{args.port}', f'localhost:{args.port}')

        def respond(self, code, value):
            raw = json.dumps(value).encode()
            self.send_response(code)
            if self.headers.get('Origin') in args.origin:
                self.send_header('Access-Control-Allow-Origin', self.headers['Origin'])
            self.send_header('Vary', 'Origin')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def auth(self):
            if not self.allowed():
                self.respond(403, {'error': 'Origin or Host not allowed'})
                return False
            if not hmac.compare_digest(self.headers.get('X-CK3-Token', ''), store.token):
                self.respond(401, {'error': 'Pairing token required'})
                return False
            return True

        def do_OPTIONS(self):
            if not self.allowed():
                return self.respond(403, {'error': 'Origin or Host not allowed'})
            self.send_response(204)
            self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin', ''))
            self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'X-CK3-Token, Content-Type')
            self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Vary', 'Origin')
            self.end_headers()

        def do_GET(self):
            if not self.auth():
                return
            url = urllib.parse.urlsplit(self.path)
            if url.path == '/health':
                return self.respond(200, store.status())
            if url.path == '/events':
                try:
                    params = urllib.parse.parse_qs(url.query)
                    after = int(params.get('after', ['0'])[0])
                    if after < 0:
                        raise ValueError()
                except ValueError:
                    return self.respond(400, {'error': 'Invalid cursor'})
                return self.respond(200, {'feedId': store.get('feed_id'), 'events': store.events(after, 50)})
            self.respond(404, {'error': 'Unknown path'})

        def do_POST(self):
            if not self.auth():
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 <= size <= 4096:
                    raise ValueError('Request too large')
                data = json.loads(self.rfile.read(size))
                if self.path == '/lifecycle':
                    store.lifecycle(data['action'])
                elif self.path == '/test':
                    store.emit('event', date='1081.3.19', player='Bridge test character',
                               title='A message from the court', body='Your messenger has arrived safely.',
                               options=['Welcome them.', 'Let them wait.'])
                elif self.path == '/shutdown':
                    store.stopping = True
                else:
                    return self.respond(404, {'error': 'Unknown path'})
                self.respond(200, {'ok': True})
            except (ValueError, KeyError, TypeError) as error:
                self.respond(400, {'error': str(error)})

    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError:
        logging.error('Port %s is busy; no second companion started', args.port)
        return 1
    server.daemon_threads = True
    server.timeout = 0.1
    logging.info('Listening on 127.0.0.1:%s; watching %s', args.port, args.logs)
    while not store.stopping:
        for tail in tails:
            for frame in tail.read():
                store.accept(frame)
        server.handle_request()
        if store.deadline and time.monotonic() > store.deadline:
            break
    server.server_close()
    store.db.close()
    return 0


def request(args, path, data=None):
    token = (args.state_dir / 'token').read_text().strip()
    req = urllib.request.Request(f'http://127.0.0.1:{args.port}{path}',
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers={'X-CK3-Token': token, 'Content-Type': 'application/json'})
    # Local traffic must not go through a user's HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=3) as response:
        return json.load(response)


def ensure(args):
    try:
        request(args, '/health')
        return
    except (OSError, urllib.error.URLError):
        pass
    Store(args.state_dir).db.close()
    with (args.state_dir / 'companion.log').open('a') as output:
        command = [sys.executable, str(Path(__file__).resolve()), '--state-dir', str(args.state_dir),
                   '--logs', str(args.logs), '--port', str(args.port)]
        for origin in args.origin:
            command += ['--origin', origin]
        subprocess.Popen(command + ['serve'], stdin=subprocess.DEVNULL, stdout=output,
                         stderr=output, start_new_session=True, close_fds=True)
    for _ in range(50):
        try:
            request(args, '/health')
            return
        except (OSError, urllib.error.URLError):
            time.sleep(0.1)
    raise RuntimeError('Companion did not start. Inspect .runtime/bridge/companion.log')


def game_group_alive(pgid):
    """A launcher may exit before the game; track surviving CK3 group members."""
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / 'stat').read_text()
            name = stat[stat.index('(')+1:stat.rindex(')')]
            fields = stat[stat.rindex(')')+2:].split()
            if name.lower() in ('ck3', 'ck3.exe') and fields[0] != 'Z' and int(fields[2]) == pgid:
                return True
        except (OSError, ValueError, IndexError):
            pass
    return False


def run_game(args):
    if not args.command:
        raise ValueError('Pass the game command after run')
    bridge_active = False
    try:
        ensure(args)
        request(args, '/lifecycle', {'action': 'opened'})
        bridge_active = True
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f'CK3 Bridge unavailable; launching game normally: {error}', file=sys.stderr)
    child = None
    try:
        child = subprocess.Popen(args.command, start_new_session=True)
        def forward(sig, _):
            try:
                os.killpg(child.pid, sig)
            except ProcessLookupError:
                pass
        signal.signal(signal.SIGINT, forward)
        signal.signal(signal.SIGTERM, forward)
        code = child.wait()
        while game_group_alive(child.pid):
            time.sleep(0.5)
        return code if code >= 0 else 128-code
    finally:
        if bridge_active:
            try:
                request(args, '/lifecycle', {'action': 'closed'})
            except (OSError, urllib.error.URLError) as error:
                print(f'CK3 Bridge could not report game closure: {error}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    parser.add_argument('--logs', type=Path, default=DEFAULT_LOGS)
    parser.add_argument('--port', type=int, default=17653)
    parser.add_argument('--origin', action='append')
    subs = parser.add_subparsers(dest='action', required=True)
    for action in ('serve', 'ensure', 'token', 'status', 'test', 'stop'):
        subs.add_parser(action)
    run = subs.add_parser('run')
    run.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    args.origin = args.origin or [DEFAULT_ORIGIN]
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    if args.action == 'serve':
        return serve(args)
    if args.action == 'token':
        store = Store(args.state_dir)
        print(store.token)
        store.db.close()
    elif args.action == 'ensure':
        ensure(args)
    elif args.action == 'run':
        return run_game(args)
    else:
        path = {'status': '/health', 'test': '/test', 'stop': '/shutdown'}[args.action]
        print(json.dumps(request(args, path, None if args.action == 'status' else {}), indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as error:
        print(f'CK3 Bridge: {error}', file=sys.stderr)
        sys.exit(1)
