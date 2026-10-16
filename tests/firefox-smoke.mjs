// Real Firefox, isolated temporary profile, mocked ST origin, real local companion.
// Usage: node tests/firefox-smoke.mjs /path/to/SillyTavern
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { mkdtemp, mkdir, readFile, rm, appendFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const st = process.argv[2];
if (!st) throw new Error('Pass the existing SillyTavern source directory (provides the ws library)');
const require = createRequire(join(resolve(st), 'package.json'));
const WebSocket = require('ws');
const directory = await mkdtemp(join(tmpdir(), 'ck3st-firefox-'));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
    const server = net.createServer();
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    await new Promise(resolve => server.close(resolve));
    return port;
}
const port = await freePort();
const browserPort = await freePort();
await mkdir(join(directory, 'logs'));
await mkdir(join(directory, 'profile'));
await appendFile(join(directory, 'logs/game.log'), '');
const companion = spawn('python3', [join(root, 'companion/bridge.py'), '--state-dir', join(directory, 'state'),
    '--logs', join(directory, 'logs'), '--port', String(port), 'serve'], { stdio: 'ignore' });
let firefox;
let socket;
let nextId = 0;
const pending = new Map();
let failSave = false;
let saved = 0;
const origin = 'https://nova-st.pve.realm.bz';
const extensionPath = '/public/scripts/extensions/third-party/SillyTavern-Extension-CK3/';
let token;

async function until(fn, message, timeout = 12000) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        if (await fn()) return;
        await delay(100);
    }
    throw new Error(`Timed out: ${message}`);
}
function command(method, params = {}) {
    const id = ++nextId;
    return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => { pending.delete(id); reject(new Error(`BiDi timeout: ${method}`)); }, 15000);
        pending.set(id, { resolve: value => { clearTimeout(timeout); resolve(value); }, reject });
        socket.send(JSON.stringify({ id, method, params }));
    });
}
let contextId;
async function evaluate(expression) {
    const value = await command('script.evaluate', { expression, target: { context: contextId }, awaitPromise: true });
    if (value.type === 'exception') throw new Error(value.exceptionDetails?.text || 'Browser evaluation failed');
    return value.result?.value;
}
const html = `<!doctype html><div id="extensions_settings2"></div><textarea id="send_textarea"></textarea><input id="file_form_input" type="file">
<script>
window.busy = false; window.generations = 0;
const handlers = {};
window.context = {chatId:'campaign', characterId:0, name1:'Alex', chat:[], chatMetadata:{integrity:'test'},
 characters:[{name:'Court',avatar:'court.png'}], extensionSettings:{}, onlineStatus:'connected',
 getRequestHeaders:()=>({'Content-Type':'application/json'}), saveSettingsDebounced:()=>{},
 addOneMessage:()=>{}, generate:async()=>{window.generations++},
 eventTypes:{CHAT_CHANGED:'chat',MESSAGE_SENT:'sent',USER_MESSAGE_RENDERED:'rendered'},
 eventSource:{on:(e,f)=>(handlers[e]??=[]).push(f),emit:async(e,...a)=>{for(const f of handlers[e]||[]) await f(...a)}}};
window.SillyTavern = {getContext:()=>({...window.context})};
</script><script type="module">import {init} from '${extensionPath}index.js'; init();</script>`;

try {
    await until(async () => {
        try { token = await readFile(join(directory, 'state/token'), 'utf8'); return true; } catch { return false; }
    }, 'companion startup');
    firefox = spawn('firefox', ['--headless', '--no-remote', '--profile', join(directory, 'profile'),
        '--remote-debugging-port', String(browserPort), 'about:blank'], { stdio: 'ignore' });
    await until(async () => {
        try {
            const candidate = new WebSocket(`ws://127.0.0.1:${browserPort}/session`);
            return await new Promise(resolve => {
                candidate.once('open', () => { socket = candidate; resolve(true); });
                candidate.once('error', () => resolve(false));
            });
        } catch { return false; }
    }, 'Firefox BiDi');
    socket.on('message', async raw => {
        const message = JSON.parse(raw);
        if (message.id) {
            const waiter = pending.get(message.id);
            if (!waiter) return;
            pending.delete(message.id);
            if (message.type === 'error') waiter.reject(new Error(`${message.error}: ${message.message}`));
            else waiter.resolve(message.result);
        } else if (message.method === 'network.beforeRequestSent' && message.params.isBlocked) {
            const request = message.params.request;
            const path = new URL(request.url).pathname;
            let body;
            let type = 'application/javascript';
            let statusCode = 200;
            if (path === '/__ck3_bridge_smoke__') { body = html; type = 'text/html'; }
            else if (path === '/public/script.js') body = 'export const isGenerating = () => window.busy;';
            else if ([extensionPath+'index.js', extensionPath+'bridge-core.js'].includes(path)) body = await readFile(join(root, path.split('/').pop()), 'utf8');
            else if (path === '/api/chats/save') {
                type = 'application/json'; body = '{}'; statusCode = failSave ? 503 : 200;
                if (!failSave) saved++;
            } else { type = 'text/plain'; body = ''; statusCode = 404; }
            await command('network.provideResponse', { request: request.request, statusCode,
                headers: [{ name: 'Content-Type', value: { type: 'string', value: type } }],
                body: { type: 'base64', value: Buffer.from(body).toString('base64') } });
        }
    });
    await command('session.new', { capabilities: {} });
    await command('session.subscribe', { events: ['network.beforeRequestSent'] });
    await command('network.addIntercept', { phases: ['beforeRequestSent'],
        urlPatterns: [{ type: 'pattern', protocol: 'https', hostname: new URL(origin).hostname }] });
    contextId = (await command('browsingContext.create', { type: 'tab' })).context;
    await command('browsingContext.navigate', { context: contextId, url: origin+'/__ck3_bridge_smoke__', wait: 'complete' });
    await until(() => evaluate('Boolean(document.getElementById("ck3st_connect"))'), 'extension initialization');
    assert.equal(await evaluate('document.getElementById("ck3st_template_game_opened").value'), '*{{user}} opened Crusader Kings III.*');
    await evaluate(`document.getElementById('ck3st_template_options_header').value='*Available choices:*'; document.getElementById('ck3st_template_options_header').dispatchEvent(new Event('input')); true`);
    assert.equal(await evaluate('context.extensionSettings.ck3Bridge.templates.options_header'), '*Available choices:*');
    await evaluate(`document.getElementById('ck3st_url').value='http://127.0.0.1:${port}'; document.getElementById('ck3st_token').value=${JSON.stringify(token)}; document.getElementById('ck3st_connect').click(); true`);
    await until(() => evaluate('document.getElementById("ck3st_status").textContent.includes("Connected")'), 'HTTPS Firefox to loopback CORS');
    await evaluate("document.getElementById('ck3st_bind').click(); true");
    await until(() => evaluate('document.getElementById("ck3st_status").textContent.startsWith("Bound")'), 'chat binding');
    await evaluate("document.getElementById('send_textarea').value='Unsent draft'; document.getElementById('ck3st_enabled').click(); document.getElementById('ck3st_test').click(); true");
    await delay(1800);
    assert.equal(await evaluate('context.chat.length'), 0, 'draft must defer delivery');
    assert.equal(await evaluate('document.getElementById("send_textarea").value'), 'Unsent draft');
    await evaluate("document.getElementById('send_textarea').value=''; true");
    await until(() => evaluate('context.chat.length===1 && window.generations===1'), 'sample delivery and auto reply');
    await delay(1800);
    assert.equal(await evaluate('context.chat.length'), 1, 'poll replay must not duplicate');
    await evaluate("context.chatId='other'; document.getElementById('ck3st_test').disabled=true; true");
    const req = await fetch(`http://127.0.0.1:${port}/test`, { method: 'POST', headers: { 'X-CK3-Token': token }, body: '{}' });
    assert.equal(req.status, 200);
    await delay(1800);
    assert.equal(await evaluate('context.chat.length'), 1, 'different chat must pause delivery');
    failSave = true;
    await evaluate("context.chatId='campaign'; true");
    await until(() => evaluate('document.getElementById("ck3st_status").textContent.includes("HTTP 503")'), 'save failure');
    assert.equal(await evaluate('context.chat.length'), 1, 'failed save must roll back');
    failSave = false;
    await until(() => evaluate('context.chat.length===2 && window.generations===2'), 'save retry');
    const frame = '[13:17:39][I][jomini_effect_impl.cpp:450]: file: common/on_action/ck3st_on_actions.txt line: 34 (ck3st_birth:effect): STCK3_V1_BEGIN milestone\nSTCK3_V1_FIELD date\n1081.3.20\nSTCK3_V1_FIELD text\nA child was born.\nSTCK3_V1_END\n';
    await appendFile(join(directory, 'logs/game.log'), frame);
    await until(() => evaluate('context.chat.length===3'), 'real log-to-browser delivery');
    assert.equal(await evaluate('context.chat[2].mes'), '*20.03.1081*\n*A child was born.*');
    const location = '[13:17:54][I][jomini_effect_impl.cpp:450]: file: common/scripted_guis/ck3st_exports.txt line: 4 (ck3st_export_event:effect): ';
    const packet = (kind, fields) => location + `STCK3_V1_BEGIN ${kind}\n` + Object.entries(fields).map(([key, value]) => `STCK3_V1_FIELD ${key}\n${value}\n`).join('') + 'STCK3_V1_END\n';
    const event = { key: 'ck3st_test.1', title: 'A messenger arrives', date: '1081.3.20' };
    await appendFile(join(directory, 'logs/game.log'), packet('event', { ...event, body: 'A \x15TOOLTIP:CHARACTER,17261 \x15L messenger\x15!\x15! bows.\n\n"My liege."', count: '2' }) + packet('option', { ...event, index: '1', text: 'You may leave now.' }));
    await delay(1800);
    assert.equal(await evaluate('context.chat.length'), 3, 'partial event must wait for every option');
    await appendFile(join(directory, 'logs/game.log'), packet('option', { ...event, index: '0', text: 'Welcome. Let us begin.' }));
    await until(() => evaluate('context.chat.length===4 && window.generations===4'), 'complete game event delivery');
    assert.equal(await evaluate('context.chat[3].mes'), 'A messenger arrives\n\n*20.03.1081*\n\nA messenger bows.\n\n"My liege."\n\n*Available choices:*\nWelcome. Let us begin.\nYou may leave now.');
    const acknowledgement = { key: 'test.2', title: 'News from court', date: '1081.3.21' };
    await appendFile(join(directory, 'logs/game.log'), packet('event', { ...acknowledgement, body: 'Important news.', count: '1' }) + packet('option', { ...acknowledgement, index: '0', text: 'Understood.' }));
    await until(() => evaluate('context.chat.length===5 && window.generations===5'), 'single-option event delivery');
    await appendFile(join(directory, 'logs/game.log'), packet('choice', { ...acknowledgement, text: 'Understood.' }));
    await delay(1800);
    assert.equal(await evaluate('context.chat.length'), 5, 'acknowledgement must not add a chat message');
    assert.equal(await evaluate('window.generations'), 5, 'acknowledgement must not trigger an AI reply');
    await evaluate(`document.getElementById('ck3st_template_marriage').value='*Wedding: {{subject}} + {{other}}*'; document.getElementById('ck3st_template_marriage').dispatchEvent(new Event('input')); true`);
    await appendFile(join(directory, 'logs/game.log'), packet('milestone', { date: '1081.3.21', milestoneType: 'marriage', subject: 'King', other: 'Queen', text: 'Old wording.' }));
    await until(() => evaluate('context.chat.length===6 && window.generations===6'), 'custom milestone narration');
    assert.equal(await evaluate('context.chat[5].mes'), '*21.03.1081*\n*Wedding: King + Queen*');
    await evaluate(`document.getElementById('ck3st_template_game_opened').value=''; document.getElementById('ck3st_template_game_opened').dispatchEvent(new Event('input')); true`);
    const opened = await fetch(`http://127.0.0.1:${port}/lifecycle`, { method: 'POST', headers: { 'X-CK3-Token': token }, body: '{"action":"opened"}' });
    assert.equal(opened.status, 200);
    await delay(1800);
    assert.equal(await evaluate('context.chat.length'), 6, 'empty narration must suppress delivery');
    assert.equal(await evaluate('window.generations'), 6, 'empty narration must not generate');
    await evaluate(`document.getElementById('ck3st_reset_templates').click(); true`);
    assert.equal(await evaluate('document.getElementById("ck3st_template_options_header").value'), '*Response options:*');
    assert.equal(saved, 6);
    console.log('PASS: Firefox pairing/CORS, queueing, retries, editable narration, reset, full event delivery, and single-option choice/reply suppression.');
} finally {
    try { socket?.close(); } catch { /* closed */ }
    firefox?.kill('SIGTERM');
    companion.kill('SIGTERM');
    await delay(300);
    await rm(directory, { recursive: true, force: true });
}
