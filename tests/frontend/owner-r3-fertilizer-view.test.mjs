import test from 'node:test';
import assert from 'node:assert/strict';
import { initialState } from '../../prototype/owner-r3/fixtures.js';
import { ensureDaily, projectDaily } from '../../prototype/owner-r3/daily-demo.js';
import { ensureFertilizer, startFertilizerDraft } from '../../prototype/owner-r3/fertilizer-demo.js';

// A deliberately small DOM/scheduler double exercises presentation ordering.
// It does not replace the real-click Chromium/WebKit journeys or text geometry.
let instance = 0;
async function viewHarness() {
  const globals = ['document', 'window', 'requestAnimationFrame', 'cancelAnimationFrame', 'queueMicrotask'];
  const originals = new Map(globals.map(name => [name, Object.getOwnPropertyDescriptor(globalThis, name)]));
  const listeners = { document: new Map(), window: new Map() }, frames = new Map(), microtasks = [], stored = new Map();
  let root = null, frameId = 0;
  const add = (where, type, callback) => {
    if (!listeners[where].has(type)) listeners[where].set(type, []);
    listeners[where].get(type).push(callback);
  };
  const emit = (where, type, target) => {
    const event = { type, target, isTrusted: true };
    for (const callback of listeners[where].get(type) || []) callback(event);
  };
  const matches = (node, selector) => {
    const attribute = selector.match(/^\[([\w-]+)(\^?=)?["']?([^"'\]]*)["']?\]$/);
    if (!attribute) return false;
    const [, name, operation, value] = attribute;
    if (!operation) return Object.hasOwn(node.attributes, name);
    return operation === '^=' ? (node.attributes[name] || '').startsWith(value) : node.attributes[name] === value;
  };
  const document = {
    activeElement: null,
    addEventListener: (type, callback) => add('document', type, callback),
    querySelector: selector => root && (matches(root, selector) ? root : root.querySelector(selector)),
    querySelectorAll: selector => root?.querySelectorAll(selector) || [],
  };
  const window = {
    scrollY: 0,
    scrollTo: (_x, y) => { window.scrollY = y; },
    addEventListener: (type, callback) => add('window', type, callback),
    sessionStorage: { getItem: key => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) },
  };
  Object.assign(globalThis, {
    document, window,
    requestAnimationFrame: callback => { frames.set(++frameId, callback); return frameId; },
    cancelAnimationFrame: id => frames.delete(id),
    queueMicrotask: callback => microtasks.push(callback),
  });
  const disconnect = node => { if (node) { node.isConnected = false; node.children.forEach(disconnect); } };
  function mount(html) {
    disconnect(root);
    const stack = [], nodes = [];
    for (const match of html.matchAll(/<(\/?)([a-z][\w-]*)\b([^>]*)>/gi)) {
      const [, closing, tag, source] = match;
      if (closing) { while (stack.length && stack.pop().tagName !== tag.toUpperCase()) {} continue; }
      const attributes = Object.fromEntries([...source.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(([, key, value]) => [key, value ?? '']));
      const node = {
        attributes, tagName: tag.toUpperCase(), children: [], parentElement: stack.at(-1) || null,
        dataset: Object.fromEntries(Object.entries(attributes).filter(([key]) => key.startsWith('data-')).map(([key, value]) => [key.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), value])),
        isConnected: true, open: Object.hasOwn(attributes, 'open'), type: attributes.type || '',
        getAttribute: name => attributes[name] ?? null,
        matches(selector) { return matches(this, selector); },
        contains(other) { for (let current = other; current; current = current.parentElement) if (current === this) return true; return false; },
        closest(selector) { for (let current = this; current; current = current.parentElement) if (matches(current, selector)) return current; return null; },
        querySelectorAll(selector) {
          // No text layout is simulated; real browser tests own autogrow.
          if (selector === '.fertilizer-autogrow') return [];
          return nodes.filter(candidate => candidate !== this && this.contains(candidate) && matches(candidate, selector));
        },
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
        getClientRects() {
          for (let current = this.parentElement; current; current = current.parentElement) {
            if (current.tagName === 'DETAILS' && !current.open && !current.children.find(child => child.tagName === 'SUMMARY')?.contains(this)) return [];
          }
          return this.isConnected ? [{}] : [];
        },
        focus() { if (this.getClientRects().length) { document.activeElement = this; emit('document', 'focusin', this); } },
      };
      if (node.parentElement) node.parentElement.children.push(node);
      nodes.push(node);
      if (!['INPUT', 'BR', 'HR', 'IMG', 'META', 'LINK', 'SOURCE'].includes(node.tagName)) stack.push(node);
    }
    root = nodes[0]; document.activeElement = null;
    return root;
  }
  function flushMicrotasks() { while (microtasks.length) microtasks.shift()(); }
  function flushFrame() {
    flushMicrotasks(); const pending = [...frames.values()]; frames.clear();
    pending.forEach(callback => callback()); flushMicrotasks();
  }
  function settle() {
    for (let count = 0; (frames.size || microtasks.length) && count < 8; count++) flushFrame();
    assert.equal(frames.size + microtasks.length, 0, 'Presentation work settles without an observer/frame loop');
  }
  const module = await import(`../../prototype/owner-r3/fertilizer-ui.js?presentation-test=${++instance}`);
  const state = initialState(); ensureDaily(state); projectDaily(state); ensureFertilizer(state);
  const record = startFertilizerDraft(state, 'kno3');
  const render = (stage = 'edit') => mount(module.renderFertilizer(state, record.id, stage));
  return {
    state, record, document, window, render, settle, flushFrame, flushMicrotasks,
    get: id => root.querySelector(`[data-testid="${id}"]`),
    emit: (type, target) => emit('document', type, target),
    open(id) { const details = this.get(id); details.open = true; emit('document', 'toggle', details); },
    cleanup() {
      for (const [name, descriptor] of originals) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor); else delete globalThis[name];
      }
    },
  };
}

test('fertilizer disclosures survive two same-view renders before any animation frame', async () => {
  const view = await viewHarness();
  try {
    view.render(); view.settle();
    for (const id of ['fertilizer-library-details', 'fertilizer-source-details', 'fertilizer-part-details']) view.open(id);
    view.get('fertilizer-area-mode').focus(); view.window.scrollY = 480;
    const stateBefore = structuredClone(view.state);
    view.render();
    // A second native select change can arrive before the first frame. Neither
    // replacement may expose a default-closed control or persist that interim DOM.
    for (const id of ['fertilizer-library-details', 'fertilizer-source-details', 'fertilizer-part-details']) assert.equal(view.get(id).open, true, `${id} is open on insertion`);
    view.render(); view.settle();
    for (const id of ['fertilizer-library-details', 'fertilizer-source-details', 'fertilizer-part-details']) assert.equal(view.get(id).open, true, `${id} survives interrupted restoration`);
    assert.equal(view.document.activeElement === view.get('fertilizer-area-mode'), true, 'Latest replacement select retains focus');
    assert.equal(view.window.scrollY, 480);
    assert.deepEqual(view.state, stateBefore, 'Presentation restoration changes no domain state');
  } finally { view.cleanup(); }
});

test('fertilizer same-view focus restores before the next native keyboard task', async () => {
  const view = await viewHarness();
  try {
    view.render(); view.settle(); view.open('fertilizer-library-details');
    view.get('fertilizer-area-mode').focus();
    view.render(); view.flushMicrotasks();
    assert.equal(view.document.activeElement === view.get('fertilizer-area-mode'), true, 'Replacement select is focused without waiting for rAF');
    assert.equal(view.get('fertilizer-area-mode').getClientRects().length, 1, 'Replacement select is not hidden in a closed disclosure');
    view.settle();
  } finally { view.cleanup(); }
});

test('fertilizer delayed route restoration does not override newer user focus or scroll', async () => {
  const view = await viewHarness();
  try {
    view.render('review'); view.settle();
    // Leaving Review through the Edit link gives the host a saved link focus.
    view.get('fertilizer-edit').focus(); view.window.scrollY = 180;
    view.render('edit'); view.settle();
    view.render('review');
    // app.js queues its own route position restoration immediately after
    // installing the returned markup, before the fertilizer microtask runs.
    let hostPosition = 180;
    requestAnimationFrame(() => {
      view.window.scrollTo(0, hostPosition);
      view.get('fertilizer-edit').focus({ preventScroll: true });
    });
    view.flushMicrotasks();
    const newer = view.get('fertilizer-dari');
    view.emit('pointerdown', newer); newer.focus(); view.window.scrollY = 720;
    hostPosition = 720; // The host scroll listener can already know the new position.
    view.settle();
    assert.equal(view.document.activeElement === newer, true, 'Newer user focus wins over saved route focus');
    assert.equal(view.window.scrollY, 720, 'Newer user scroll wins over saved route position');
  } finally { view.cleanup(); }
});

test('fertilizer synchronous disclosure restoration preserves an explicit closed choice', async () => {
  const view = await viewHarness();
  try {
    view.render('review'); view.settle();
    const comparison = view.get('fertilizer-comparison');
    assert.equal(comparison.open, true, 'Numeric comparison is open by default');
    comparison.open = false; view.emit('toggle', comparison);
    view.render('review');
    assert.equal(view.get('fertilizer-comparison').open, false, 'Saved closed choice overrides the open default on insertion');
    view.settle();
    assert.equal(view.get('fertilizer-comparison').open, false, 'Queued restoration does not reopen a closed disclosure');
  } finally { view.cleanup(); }
});
