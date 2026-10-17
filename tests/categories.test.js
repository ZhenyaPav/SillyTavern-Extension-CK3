import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createCategoryFilters } from '../category-filters.js';

const definitions = JSON.parse(readFileSync(new URL('../event-categories.json', import.meta.url))).categories;
const filters = createCategoryFilters(definitions);

test('15 categories and presets keep the agreed story categories and mute three recurring types', () => {
    assert.equal(definitions.length, 15);
    assert.equal(new Set(definitions.map(c => c.id)).size, 15);
    assert.equal(Object.values(filters.preset('all')).every(Boolean), true);
    assert.equal(Object.values(filters.preset('none')).some(Boolean), false);
    const story = filters.preset('story');
    assert.deepEqual(Object.keys(story).filter(key => !story[key]).sort(), ['reports', 'scheme_progress', 'vassal_routine']);
    assert.equal(story.scheme_finales, true);
    assert.equal(story.other, true);
});

test('skipped parents cannot produce orphan choices when their category is subsequently enabled', () => {
    const parent = { kind: 'event', eventKey: 'parent', category: 'reports' };
    const settings = { categories: filters.preset('story') };
    const allowed = filters.allows(parent, settings);
    assert.equal(allowed, false);
    const decisions = filters.remember({}, parent, allowed);
    const choice = { ...parent, kind: 'choice' };
    settings.categories.reports = true;
    assert.equal(filters.allows(choice, settings, decisions), false);
    assert.equal(filters.allows(choice, settings, {}), false);
    const delivered = filters.remember(decisions, parent, true);
    assert.equal(filters.allows(choice, settings, delivered), true);
    settings.categories.reports = false;
    assert.equal(filters.allows(choice, settings, delivered), false);
});

test('unknown events remain visible, lifecycle stays separate, milestones follow their subject', () => {
    const settings = { categories: filters.preset('story') };
    assert.equal(filters.allows({ kind: 'event', category: 'future-category' }, settings), true);
    assert.equal(filters.allows({ kind: 'event' }, settings), true);
    settings.categories.family = false;
    assert.equal(filters.allows({ kind: 'milestone', milestoneType: 'birth' }, settings), false);
    assert.equal(filters.allows({ kind: 'milestone', milestoneType: 'war_started' }, settings), true);
    assert.equal(filters.allows({ kind: 'game_opened' }, { categories: filters.preset('none') }), true);
    assert.equal(filters.allows({ kind: 'game_opened' }, { lifecycle: false }), false);
});
