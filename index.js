import { isGenerating } from '../../../../script.js';
import { appendEvent, chatTarget, loopbackUrl, persistSnapshot } from './bridge-core.js';

const KEY = 'ck3Bridge';
const LOCAL_KEY = 'CK3BridgeLocalV1';
const TAB = crypto.randomUUID();
const LEASE = 'CK3BridgeLeaderV1';
let initialized = false;
let polling = false;
let timer;
let activeController;
let lastStatus;

function ctx() { return SillyTavern.getContext(); }
function config() {
    const context = ctx();
    return context.extensionSettings[KEY] ??= {
        enabled: false, autoReply: true, lifecycle: true, milestones: true,
    };
}
function local() {
    try { return JSON.parse(localStorage.getItem(LOCAL_KEY) || '{}'); }
    catch { return {}; }
}
function saveLocal(value) { localStorage.setItem(LOCAL_KEY, JSON.stringify(value)); }
function status(text) { document.getElementById('ck3st_status').textContent = text; }
function errorText(error) { return error instanceof Error ? error.message : String(error); }
function hasDraft() {
    return Boolean(document.querySelector('#send_textarea')?.value?.trim()
        || document.querySelector('#file_form_input')?.files?.length
        || [...document.querySelectorAll('.edit_textarea, .mes_edit_textarea')].some(input => input.getClientRects().length));
}

async function api(path, body) {
    const state = local();
    if (!state.token) throw new Error('Enter the local pairing token first');
    const base = loopbackUrl(state.url || 'http://127.0.0.1:17653');
    const controller = new AbortController();
    activeController = controller;
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
        const response = await fetch(base + path, {
            method: body === undefined ? 'GET' : 'POST',
            mode: 'cors', credentials: 'omit', cache: 'no-store', signal: controller.signal,
            headers: { 'X-CK3-Token': state.token, ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
            ...(body === undefined ? {} : { body: JSON.stringify(body) }),
            targetAddressSpace: 'loopback',
        });
        if (!response.ok) throw new Error(response.status === 401 ? 'Pairing token rejected' : `Companion returned HTTP ${response.status}`);
        return await response.json();
    } catch (error) {
        if (error instanceof TypeError) throw new Error('Cannot reach the companion. Check it is running and Firefox allows this site to access localhost.');
        throw error;
    } finally {
        clearTimeout(timeout);
        if (activeController === controller) activeController = null;
    }
}

function leader() {
    const now = Date.now();
    let lease;
    try { lease = JSON.parse(localStorage.getItem(LEASE) || 'null'); } catch { /* expired/invalid */ }
    if (lease && lease.tab !== TAB && now - lease.at < 15000) return false;
    localStorage.setItem(LEASE, JSON.stringify({ tab: TAB, at: now }));
    return JSON.parse(localStorage.getItem(LEASE)).tab === TAB;
}

async function poll() {
    if (polling || !config().enabled) return;
    const state = local();
    if (!state.target) return status('Bind this chat to begin.');
    if (chatTarget(ctx()) !== state.target) return status('Paused: open the bound chat to receive game messages.');
    if (!leader()) return status('Another tab is receiving game messages.');
    polling = true;
    try {
        lastStatus = await api('/health');
        if (lastStatus.feedId !== state.feedId) {
            config().enabled = false;
            ctx().saveSettingsDebounced();
            document.getElementById('ck3st_enabled').checked = false;
            return status('Companion history changed. Bind this chat again.');
        }
        if (isGenerating() || hasDraft()) return status('Queued: waiting for generation or your draft/attachment.');
        if (lastStatus.incompleteEvents) status(`Incomplete event snapshots: ${lastStatus.incompleteEvents}. Check game export.`);
        else if (lastStatus.gameRunning && !lastStatus.framesReceived) status('Game running; waiting for the mod’s first log frame.');
        else status(`Connected · ${lastStatus.framesReceived} game frames · ${lastStatus.context?.date || 'no game date yet'}`);
        const batch = await api(`/events?after=${Number(state.cursor) || 0}`);
        if (batch.feedId !== state.feedId) throw new Error('Companion history changed; bind again');
        let needReply = state.pendingReply === true;
        for (const event of batch.events) {
            if (chatTarget(ctx()) !== state.target || hasDraft() || isGenerating() || !config().enabled) break;
            const skip = (['game_opened', 'game_closed', 'session_loaded', 'character_changed'].includes(event.kind) && !config().lifecycle)
                || (event.kind === 'milestone' && !config().milestones);
            if (!skip) {
                const context = ctx();
                const added = await appendEvent({ ...context, saveChat: () => persistSnapshot(context) }, event, state.feedId, state.target, ctx);
                needReply ||= added;
            }
            // Read latest state to avoid undoing a concurrent Unbind or settings edit.
            const latest = local();
            if (latest.target !== state.target || latest.feedId !== state.feedId) break;
            latest.cursor = event.seq;
            latest.pendingReply = needReply;
            saveLocal(latest);
        }
        if (needReply && config().autoReply && config().enabled && chatTarget(ctx()) === state.target && !hasDraft() && !isGenerating()) {
            if (ctx().onlineStatus === 'no_connection') return status('Messages recorded; AI backend is disconnected.');
            // Clear before dispatch: a reload must not unexpectedly generate a duplicate reply.
            const latest = local();
            if (latest.target !== state.target) return;
            latest.pendingReply = false;
            saveLocal(latest);
            await ctx().generate('normal', { automatic_trigger: true });
        }
    } catch (error) {
        status(errorText(error));
        console.warn('[CK3 Bridge]', error);
    } finally {
        polling = false;
    }
}

export function init() {
    if (initialized) return;
    initialized = true;
    const panel = document.createElement('div');
    panel.className = 'extension_container';
    panel.innerHTML = `
      <div class="inline-drawer">
        <div class="inline-drawer-toggle inline-drawer-header"><b>CK3 Bridge</b><div class="inline-drawer-icon fa-solid fa-circle-chevron-down down"></div></div>
        <div class="inline-drawer-content">
          <label class="checkbox_label"><input id="ck3st_enabled" type="checkbox"> Receive game messages</label>
          <label class="checkbox_label"><input id="ck3st_reply" type="checkbox"> Automatically generate an AI reply</label>
          <label class="checkbox_label"><input id="ck3st_lifecycle" type="checkbox"> Game/session messages</label>
          <label class="checkbox_label"><input id="ck3st_milestones" type="checkbox"> Major life and war changes</label>
          <label for="ck3st_url">Local companion</label>
          <input id="ck3st_url" class="text_pole" placeholder="http://127.0.0.1:17653" autocomplete="off">
          <label for="ck3st_token">Pairing token (stored only in this browser)</label>
          <input id="ck3st_token" class="text_pole" type="password" autocomplete="off">
          <div class="flex-container">
            <button id="ck3st_connect" class="menu_button">Test connection</button>
            <button id="ck3st_bind" class="menu_button">Bind this chat</button>
            <button id="ck3st_unbind" class="menu_button">Unbind</button>
            <button id="ck3st_test" class="menu_button">Send sample event</button>
          </div>
          <small>Bind before starting CK3. Switching chats pauses delivery. Pending messages arrive in order; events collected while busy share one AI reply.</small>
          <p id="ck3st_status" role="status">Not connected.</p>
        </div>
      </div>`;
    (document.querySelector('#extensions_settings2') || document.querySelector('#extensions_settings')).append(panel);
    const state = local();
    document.getElementById('ck3st_url').value = state.url || 'http://127.0.0.1:17653';
    document.getElementById('ck3st_token').value = state.token || '';
    for (const [id, key] of [['enabled', 'enabled'], ['reply', 'autoReply'], ['lifecycle', 'lifecycle'], ['milestones', 'milestones']]) {
        const input = document.getElementById(`ck3st_${id}`);
        input.checked = config()[key];
        input.addEventListener('change', () => {
            config()[key] = input.checked;
            ctx().saveSettingsDebounced();
            if (key === 'enabled' && !input.checked) activeController?.abort();
            schedulePoll();
        });
    }
    const storeConnection = () => {
        const current = local();
        current.url = loopbackUrl(document.getElementById('ck3st_url').value.trim());
        current.token = document.getElementById('ck3st_token').value.trim();
        saveLocal(current);
    };
    function action(id, handler) {
        document.getElementById(`ck3st_${id}`).addEventListener('click', async () => {
            try { storeConnection(); await handler(); }
            catch (error) { status(errorText(error)); }
        });
    }
    action('connect', async () => {
        const health = await api('/health');
        status(`Connected. ${health.framesReceived} game frames received.`);
    });
    action('bind', async () => {
        if (polling || isGenerating()) throw new Error('Wait for the current message or generation to finish');
        const target = chatTarget(ctx());
        if (!target) throw new Error('Open a saved character or group chat first');
        const health = await api('/health');
        saveLocal({ ...local(), target, feedId: health.feedId, cursor: health.latest, pendingReply: false });
        status('Bound to this chat. New game messages will arrive here.');
    });
    action('unbind', async () => {
        config().enabled = false;
        ctx().saveSettingsDebounced();
        document.getElementById('ck3st_enabled').checked = false;
        activeController?.abort();
        const current = local();
        delete current.target;
        current.pendingReply = false;
        saveLocal(current);
        status('Unbound.');
    });
    action('test', async () => {
        if (!local().target || chatTarget(ctx()) !== local().target) throw new Error('Bind this chat first');
        await api('/test', {});
        status('Sample event queued. Enable receiving to import it.');
        schedulePoll();
    });
    ctx().eventSource.on(ctx().eventTypes.CHAT_CHANGED, () => { activeController?.abort(); schedulePoll(); });
    // Web Locks serializes tabs, including tabs waking after long browser suspension.
    // The lease also supports older clients and reports which tab is active.
    timer = setInterval(schedulePoll, 1500);
    window.addEventListener('pagehide', () => {
        clearInterval(timer);
        activeController?.abort();
        try {
            if (JSON.parse(localStorage.getItem(LEASE) || 'null')?.tab === TAB) localStorage.removeItem(LEASE);
        } catch { /* storage unavailable */ }
    });
    schedulePoll();
}

function schedulePoll() {
    if (navigator.locks) void navigator.locks.request('CK3BridgeDeliveryV1', { ifAvailable: true }, lock => lock ? poll() : undefined);
    else void poll();
}
