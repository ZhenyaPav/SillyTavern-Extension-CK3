export function formatDate(value) {
    const match = /^(\d+)\.(\d{1,2})\.(\d{1,2})$/.exec(String(value || ''));
    return match ? `${match[3].padStart(2, '0')}.${match[2].padStart(2, '0')}.${Number(match[1])}` : String(value || '');
}

export function formatEvent(event, user) {
    const date = formatDate(event.date);
    switch (event.kind) {
        case 'event': {
            const body = [event.opening, event.body, event.signature].filter(Boolean).join('\n\n');
            const options = event.options?.length ? `Response options:\n${event.options.join('\n')}` : '';
            return [event.title, date, body, options].filter(Boolean).join('\n\n');
        }
        case 'choice': return [`${user} chose: ${event.text}`, date].filter(Boolean).join('\n');
        case 'session_loaded': return [`${user} loaded the game as ${event.player || 'their character'}`, date].filter(Boolean).join('\n');
        case 'character_changed': return [`${user} is now playing as ${event.player || 'a new character'}`, date].filter(Boolean).join('\n');
        case 'game_opened': return `${user} opened Crusader Kings III.`;
        case 'game_closed': return [`${user} closed Crusader Kings III.`, date].filter(Boolean).join('\n');
        case 'milestone': return [date, event.text].filter(Boolean).join('\n');
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
export async function appendEvent(context, event, feedId, expectedTarget, currentContext = () => context) {
    if (!expectedTarget || chatTarget(context) !== expectedTarget) throw new Error('The bound chat is not open');
    if (alreadyImported(context.chat, feedId, event.seq)) return false;
    const text = formatEvent(event, context.name1 || 'User');
    if (!text) return false;
    const message = {
        name: context.name1 || 'User', is_user: true, is_system: false,
        send_date: new Date().toISOString(), mes: text,
        extra: { ck3Bridge: { feedId, seq: event.seq, kind: event.kind } },
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
