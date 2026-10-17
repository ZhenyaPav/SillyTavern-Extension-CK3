/** Category metadata never becomes message prose. Unknown/legacy records use Other. */
export function createCategoryFilters(definitions) {
    const ids = new Set(definitions.map(category => category.id));
    function categoryFor(event) {
        if (event.kind === 'milestone') {
            if (event.milestoneType?.startsWith('war_')) return 'war';
            if (event.milestoneType === 'death') return 'health';
            if (['birth', 'marriage', 'divorce'].includes(event.milestoneType)) return 'family';
        }
        return ids.has(event.category) ? event.category : 'other';
    }
    return {
        categoryFor,
        preset(name) {
            return Object.fromEntries(definitions.map(category => [category.id,
                name === 'story' ? category.storyFocused : name === 'all']));
        },
        allows(event, settings, decisions = {}) {
            if (['game_opened', 'game_closed', 'session_loaded', 'character_changed'].includes(event.kind)) {
                return settings.lifecycle !== false;
            }
            if (event.kind === 'milestone' && settings.milestones === false) return false;
            if (settings.categories?.[categoryFor(event)] === false) return false;
            // A category enabled after a skipped parent must not admit an orphan choice.
            return event.kind !== 'choice' || !event.eventKey || decisions[event.eventKey] === true;
        },
        remember(decisions, event, allowed) {
            const next = { ...decisions };
            if (event.kind === 'event' && event.eventKey) {
                delete next[event.eventKey];
                next[event.eventKey] = allowed;
                const keys = Object.keys(next);
                for (const key of keys.slice(0, Math.max(0, keys.length - 512))) delete next[key];
            }
            return next;
        },
    };
}
