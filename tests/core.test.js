import test from 'node:test';
import assert from 'node:assert/strict';
import { appendEvent, chatTarget, formatEvent, loopbackUrl, MESSAGE_TEMPLATES, persistSnapshot } from '../bridge-core.js';

function context() {
    return {
        chatId: 'campaign', characterId: 0, name1: 'Alex', chat: [],
        saveChat: async () => {}, addOneMessage: () => {},
        eventSource: { emit: async () => {} }, eventTypes: {},
    };
}

test('RP messages contain only prose and requested date/options, never transport data', () => {
    const message = formatEvent({ kind: 'event', seq: 3, key: 'secret.1', date: '873.2.19',
        title: 'A visitor', body: '/send {{literal}}', options: ['Welcome.', 'Leave.'] }, 'Alex');
    assert.equal(message, 'A visitor\n\n*19.02.873*\n\n/send {{literal}}\n\n*Response options:*\nWelcome.\nLeave.');
    assert.equal(formatEvent({ kind: 'choice', date: '1081.3.19', text: 'Welcome.' }, 'Alex'),
        '*Alex chose: Welcome.*\n*19.03.1081*');
});

test('editable narration covers lifecycle, choices, dates, labels, and structured milestones', () => {
    const templates = { game_opened: '{{user}} returns.', date: 'On {{date}}', options_header: 'Choose:',
        session_loaded: '{{user}} plays {{player}}', marriage: '{{subject}} & {{other}} wed.' };
    assert.equal(formatEvent({ kind: 'game_opened' }, 'Alex', templates), 'Alex returns.');
    assert.equal(formatEvent({ kind: 'session_loaded', player: 'King', date: '873.2.19' }, 'Alex', templates), 'Alex plays King\nOn 19.02.873');
    assert.equal(formatEvent({ kind: 'event', body: '{{user}} stands.', options: ['{{text}}'], date: '873.2.19' }, 'Alex', templates),
        'On 19.02.873\n\n{{user}} stands.\n\nChoose:\n{{text}}');
    assert.equal(formatEvent({ kind: 'milestone', milestoneType: 'marriage', subject: '{{user}}', other: 'Queen', text: 'Old wording' }, 'Alex', templates),
        '{{user}} & Queen wed.');
    assert.equal(formatEvent({ kind: 'milestone', text: 'An older notification.' }, 'Alex'), '*An older notification.*');
    for (const [kind, definition] of Object.entries(MESSAGE_TEMPLATES)) assert.ok(definition.text.startsWith('*') && definition.text.endsWith('*'), kind);
});

test('empty narration and single-option acknowledgements do not create a message or save', async () => {
    const c = context();
    let saves = 0;
    c.saveChat = async () => { saves++; };
    assert.equal(formatEvent({ kind: 'game_closed', date: '873.2.19' }, 'Alex', { game_closed: '' }), '');
    assert.equal(formatEvent({ kind: 'event', title: 'Event', options: ['OK'] }, 'Alex', { options_header: '' }), 'Event\n\nOK');
    assert.equal(await appendEvent(c, { kind: 'choice', text: 'OK', optionCount: 1 }, 'feed', chatTarget(c)), false);
    assert.equal(c.chat.length, 0);
    assert.equal(saves, 0);
});

test('durable message metadata prevents duplicate imports after a cursor-save failure', async () => {
    const c = context();
    const target = chatTarget(c);
    const event = { kind: 'milestone', seq: 7, date: '1.1.1', text: 'A child was born.' };
    assert.equal(await appendEvent(c, event, 'feed', target), true);
    assert.equal(await appendEvent(c, event, 'feed', target), false);
    assert.equal(c.chat.length, 1);
    assert.deepEqual(c.chat[0].extra.ck3Bridge, { feedId: 'feed', seq: 7, kind: 'milestone' });
    assert.equal(c.chat[0].is_user, true);
    assert.equal(c.chat[0].mes.includes('feed'), false);
});

test('failed chat save rolls back the append and permits a safe retry', async () => {
    const c = context();
    c.saveChat = async () => { throw new Error('offline'); };
    await assert.rejects(appendEvent(c, { kind: 'game_opened', seq: 1 }, 'feed', chatTarget(c)), /offline/);
    assert.equal(c.chat.length, 0);
});

test('switching chats during a save does not render into the new chat', async () => {
    const c = context();
    let current = c;
    let renders = 0;
    c.saveChat = async () => { current = { ...c, chatId: 'different' }; };
    c.addOneMessage = () => { renders++; };
    await appendEvent(c, { kind: 'game_opened', seq: 1 }, 'feed', chatTarget(c), () => current);
    assert.equal(renders, 0);
    await assert.rejects(appendEvent(current, { kind: 'game_closed', seq: 2 }, 'feed', chatTarget(c)), /bound chat/);
});

test('pairing token cannot be redirected to a remote or credential-bearing URL', () => {
    assert.equal(loopbackUrl('http://127.0.0.1:17653/'), 'http://127.0.0.1:17653');
    for (const url of ['https://example.com', 'http://localhost.evil.test', 'http://user:password@localhost', 'http://localhost/?token=x']) {
        assert.throws(() => loopbackUrl(url));
    }
});

test('save snapshots retain the target identity and reject HTTP failures without forced overwrites', async () => {
    const c = { ...context(), chatMetadata: { integrity: 'original' }, characters: [{ name: 'Court', avatar: 'court.png' }],
        getRequestHeaders: () => ({ 'Content-Type': 'application/json' }) };
    const requests = [];
    const fetchImpl = async (path, request) => { requests.push([path, JSON.parse(request.body)]); return { ok: false, status: 503 }; };
    await assert.rejects(persistSnapshot(c, fetchImpl), /HTTP 503/);
    assert.equal(requests[0][0], '/api/chats/save');
    assert.equal(requests[0][1].file_name, 'campaign');
    assert.equal(requests[0][1].force, false);
    assert.equal(requests[0][1].chat[0].chat_metadata.integrity, 'original');
    await persistSnapshot({ ...c, groupId: 'group' }, async (path, request) => {
        assert.equal(path, '/api/chats/group/save');
        assert.equal(JSON.parse(request.body).id, 'campaign');
        return { ok: true };
    });
});
