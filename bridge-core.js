export function formatDate(value) {
    const match = /^(\d+)\.(\d{1,2})\.(\d{1,2})$/.exec(String(value || ''));
    return match ? `${match[3].padStart(2, '0')}.${match[2].padStart(2, '0')}.${Number(match[1])}` : String(value || '');
}

export const MESSAGE_TEMPLATES = Object.freeze({
    date: { label: 'Date line', text: '*{{date}}*' },
    options_header: { label: 'Response options heading', text: '*Response options:*' },
    choice: { label: 'Selected response', text: '*{{user}} chose: {{text}}*' },
    game_opened: { label: 'Game opened', text: '*{{user}} opened Crusader Kings III.*' },
    game_closed: { label: 'Game closed', text: '*{{user}} closed Crusader Kings III.*' },
    session_loaded: { label: 'Save loaded', text: '*{{user}} loaded the game as {{player}}.*' },
    character_changed: { label: 'Player character changed', text: '*{{user}} is now playing as {{player}}.*' },
    death: { label: 'Death', text: '*{{subject}} died.*' },
    birth: { label: 'Birth', text: '*{{subject}} was born.*' },
    marriage: { label: 'Marriage', text: '*{{subject}} married {{other}}.*' },
    divorce: { label: 'Divorce', text: '*{{subject}} divorced {{other}}.*' },
    war_started: { label: 'War started', text: '*{{subject}} began a war against {{other}}.*' },
    war_attacker_won: { label: 'Attacker won', text: '*{{subject}} won the war against {{other}}.*' },
    war_defender_won: { label: 'Defender won', text: '*{{subject}} defeated {{other}}.*' },
    war_white_peace: { label: 'White peace', text: '*The war between {{subject}} and {{other}} ended in white peace.*' },
    milestone: { label: 'Older/general milestone messages', text: '*{{text}}*' },
});

// One substitution pass: braces in game text are never interpreted as templates.
export function renderTemplate(template, values) {
    return template.replace(/\{\{(user|date|player|subject|other|text)\}\}/g, (_, key) => String(values[key] ?? '')).trim();
}

export function formatEvent(event, user, templates = {}) {
    const values = { ...event, user, date: formatDate(event.date) };
    const render = key => renderTemplate(typeof templates[key] === 'string' ? templates[key] : MESSAGE_TEMPLATES[key].text, values);
    const date = event.date ? render('date') : '';
    switch (event.kind) {
        case 'event': {
            const body = [event.opening, event.body, event.signature].filter(Boolean).join('\n\n');
            const options = event.options?.length ? [render('options_header'), ...event.options].filter(Boolean).join('\n') : '';
            return [event.title, date, body, options].filter(Boolean).join('\n\n');
        }
        case 'choice':
            if (event.optionCount === 1) return '';
            // Fall through to the configurable narration shared with lifecycle messages.
        case 'session_loaded':
        case 'character_changed':
        case 'game_opened':
        case 'game_closed': {
            const text = render(event.kind);
            return text ? [text, date].filter(Boolean).join('\n') : '';
        }
        case 'milestone': {
            const known = ['birth', 'death', 'marriage', 'divorce', 'war_started', 'war_attacker_won', 'war_defender_won', 'war_white_peace'];
            const type = known.includes(event.milestoneType) && event.subject ? event.milestoneType : 'milestone';
            const text = render(type);
            return text ? [date, text].filter(Boolean).join('\n') : '';
        }
        default: return '';
    }
}

export function chatTarget(context) {
    if (!context.chatId || (context.characterId === undefined && !context.groupId)) return null;
    const identity = context.groupId || context.characters?.[context.characterId]?.avatar || context.characterId;
    return JSON.stringify([context.groupId ? 'group' : 'character', String(identity), String(context.chatId)]);
}

export function alreadyImported(chat, feedId, seq) {
    return chat.some(message => message.extra?.ck3Bridge?.feedId === feedId && message.extra.ck3Bridge.seq === seq);
}

/** Native saveChatConditional swallows failures. Save a pinned snapshot and check HTTP success. */
export async function persistSnapshot(context, fetchImpl = fetch) {
    const chat = [{ chat_metadata: { ...context.chatMetadata }, user_name: 'unused', character_name: 'unused' }, ...context.chat];
    let path;
    let payload;
    if (context.groupId) {
        path = '/api/chats/group/save';
        payload = { id: context.chatId, chat, force: false };
    } else {
        const character = context.characters[context.characterId];
        if (!character || !context.chatId) throw new Error('No saved character chat is open');
        path = '/api/chats/save';
        payload = { ch_name: character.name, file_name: context.chatId, chat, avatar_url: character.avatar, force: false };
    }
    const response = await fetchImpl(path, {
        method: 'POST', cache: 'no-store', headers: context.getRequestHeaders(), body: JSON.stringify(payload),
    });
    if (!response.ok) throw new Error(`Chat save failed (HTTP ${response.status}); delivery is paused and will retry`);
}

/** Add durable identity before saving, so replay can recover after a tab closes. */
export async function appendEvent(context, event, feedId, expectedTarget, currentContext = () => context, templates = {}) {
    if (!expectedTarget || chatTarget(context) !== expectedTarget) throw new Error('The bound chat is not open');
    if (alreadyImported(context.chat, feedId, event.seq)) return false;
    const text = formatEvent(event, context.name1 || 'User', templates);
    if (!text) return false;
    const message = {
        name: context.name1 || 'User', is_user: true, is_system: false,
        send_date: new Date().toISOString(), mes: text,
        extra: { ck3Bridge: { feedId, seq: event.seq, kind: event.kind,
            ...(event.eventKey ? { eventKey: event.eventKey, eventId: event.eventId, category: event.category } : {}) } },
    };
    // No slash-command evaluation, template evaluation, attachment consumption, or draft edits.
    if (context.chatMetadata) context.chatMetadata.tainted = true;
    context.chat.push(message);
    try {
        await context.saveChat();
    } catch (error) {
        const index = context.chat.indexOf(message);
        if (index !== -1) context.chat.splice(index, 1);
        throw error;
    }
    if (chatTarget(currentContext()) !== expectedTarget) return true;
    context.addOneMessage(message);
    await context.eventSource.emit(context.eventTypes.MESSAGE_SENT, context.chat.length - 1);
    await context.eventSource.emit(context.eventTypes.USER_MESSAGE_RENDERED, context.chat.length - 1);
    return true;
}

export function loopbackUrl(value) {
    const url = new URL(value);
    if (!['http:', 'https:'].includes(url.protocol) || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
        || url.username || url.password || url.search || url.hash || !['', '/'].includes(url.pathname)) {
        throw new Error('Use a local companion address, for example http://127.0.0.1:17653');
    }
    return url.origin;
}
