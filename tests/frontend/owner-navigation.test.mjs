import test from 'node:test';
import assert from 'node:assert/strict';
import {isVisibleOwnerControl} from '../e2e/visibility.mjs';
import {
  routes, primaryRoutes, financeRoutes, moreRoutes, resolveRoute,
  primaryNavigation, financeNavigation, moreNavigation, icon,
} from '../../src/greenhouse_owner_os/web/navigation.js';

const buttons = html => [...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)]
  .map(([, attributes, content]) => ({attributes, content}));
const destination = (button, attribute) => button.attributes.match(new RegExp(`${attribute}="([^"]+)"`))?.[1];

test('four primary destinations cover every existing owner route without orphan modules', () => {
  assert.deepEqual(primaryRoutes, ['home', 'review', 'overview', 'more']);
  assert.deepEqual(financeRoutes, ['overview', 'debts', 'operations']);
  assert.deepEqual(moreRoutes, ['harvest', 'inventory', 'tasks', 'inbox', 'parties']);
  assert.deepEqual(new Set(Object.keys(routes)), new Set([...primaryRoutes, ...financeRoutes, ...moreRoutes]));
  for (const [key, route] of Object.entries(routes)) {
    assert.ok(primaryRoutes.includes(route.group), key);
    assert.ok(route.label.trim(), key);
    assert.equal(resolveRoute(`#${key}`), key);
    assert.equal(resolveRoute(key), key);
  }
});

test('unknown, malformed, and injected hashes resolve safely to Home', () => {
  assert.equal(resolveRoute(), 'home');
  for (const hash of ['', '#', '#missing', '#/overview', '#overview?x=1', '#OVERVIEW',
    '#%68ome', '#<img src=x onerror=alert(1)>', '#__proto__', '#constructor', '##home']) {
    assert.equal(resolveRoute(hash), 'home', hash);
  }
});

test('primary navigation exposes text names and one semantic active parent for every route', () => {
  for (const [route, metadata] of Object.entries(routes)) {
    const items = buttons(primaryNavigation(route));
    assert.equal(items.length, 4);
    assert.deepEqual(items.map(item => destination(item, 'data-page')), primaryRoutes);
    assert.deepEqual(items.filter(item => /aria-current="page"/.test(item.attributes))
      .map(item => destination(item, 'data-page')), [metadata.group]);
    for (const item of items) {
      const key = destination(item, 'data-page');
      assert.ok(item.content.includes(`<span>${routes[key].label}</span>`));
      assert.match(item.content, /<svg[^>]+aria-hidden="true"/);
    }
  }
  assert.match(buttons(primaryNavigation('missing'))[0].attributes, /aria-current="page"/);
});

test('Review count is separately labelled, capped visually, and does not change its stable selector', () => {
  for (const count of [0, -1]) assert.doesNotMatch(primaryNavigation('review', count), /nav-count/);
  for (const count of [1, 12, 99, 100, 1250]) {
    const review = buttons(primaryNavigation('review', count)).find(item => destination(item, 'data-page') === 'review');
    assert.match(review.content, /<span>Проверка<\/span>/);
    assert.ok(review.content.includes(`aria-label="${count} ждут проверки"`));
    assert.ok(review.content.includes(`>${count > 99 ? '99+' : count}</span>`));
  }
});

test('Finance tabs preserve all destinations and their selected state', () => {
  for (const route of financeRoutes) {
    const html = financeNavigation(route), items = buttons(html);
    assert.match(html, /aria-label="Разделы финансов"/);
    assert.deepEqual(items.map(item => destination(item, 'data-go')), financeRoutes);
    assert.deepEqual(items.filter(item => /aria-pressed="true"/.test(item.attributes))
      .map(item => destination(item, 'data-go')), [route]);
  }
});

test('More contains the five working modules with descriptive text', () => {
  const items = buttons(moreNavigation());
  assert.deepEqual(items.map(item => destination(item, 'data-go')), moreRoutes);
  for (const item of items) {
    const route = routes[destination(item, 'data-go')];
    assert.ok(item.content.includes(`<strong>${route.label}</strong>`));
    assert.ok(item.content.includes(`<small>${route.description}</small>`));
  }
});

test('icons stay decorative and unknown names cannot inject markup', () => {
  const html = icon('<script>alert(1)</script>');
  assert.match(html, /^<svg/);
  assert.match(html, /aria-hidden="true"/);
  assert.doesNotMatch(html, /<script|onclick|onerror/);
  assert.equal(html, icon('grid'));
});


test('closed native disclosures exclude content with stale rectangles but retain their first summary', () => {
  const previous = globalThis.getComputedStyle;
  globalThis.getComputedStyle = node => ({visibility: node.visibility || 'visible'});
  const node = (tagName, children = [], options = {}) => {
    const result = {tagName, children, parentElement: null, open: false, getClientRects: () => [{}], ...options};
    result.contains = target => result === target || children.some(child => child.contains(target));
    children.forEach(child => {child.parentElement = result;});
    return result;
  };
  try {
    const insideSummary = node('BUTTON'), summary = node('SUMMARY', [insideSummary]);
    const hiddenButton = node('BUTTON'), secondSummary = node('SUMMARY');
    const disclosure = node('DETAILS', [summary, hiddenButton, secondSummary]);
    assert.equal(isVisibleOwnerControl(summary), true);
    assert.equal(isVisibleOwnerControl(insideSummary), true);
    assert.equal(isVisibleOwnerControl(hiddenButton), false);
    assert.equal(isVisibleOwnerControl(secondSummary), false);
    disclosure.open = true;
    assert.equal(isVisibleOwnerControl(hiddenButton), true);
    assert.equal(isVisibleOwnerControl(secondSummary), true);
    const outer = node('DETAILS', [node('SUMMARY'), disclosure]);
    assert.equal(isVisibleOwnerControl(summary), false, 'An inner summary is still hidden by its closed outer disclosure');
    outer.open = true;
    assert.equal(isVisibleOwnerControl(hiddenButton), true);
    disclosure.open = false;
    assert.equal(isVisibleOwnerControl(summary), true);
    assert.equal(isVisibleOwnerControl(hiddenButton), false);
    assert.equal(isVisibleOwnerControl(node('BUTTON', [], {visibility: 'hidden'})), false);
    assert.equal(isVisibleOwnerControl(node('BUTTON', [], {getClientRects: () => []})), false);
    assert.equal(isVisibleOwnerControl(node('BUTTON')), true, 'Ordinary visible unnamed controls must still be checked');
  } finally {
    if (previous === undefined) delete globalThis.getComputedStyle;
    else globalThis.getComputedStyle = previous;
  }
});
