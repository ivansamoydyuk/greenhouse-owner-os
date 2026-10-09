// Measurements from executed browser operations, never missing-field estimates.
// A click operation is one Playwright click that emitted a trusted DOM click.
// Native selectOption/fill operations are logged separately; this is not an
// estimate of physical-device taps, keyboard strokes, time, or acoustic accuracy.
import assert from 'node:assert/strict';

export const metricDefinitions = Object.freeze({
  MANUAL_OWNER_INPUT_COUNT: 'Actual changed business-field controls after source capture, including newly supplied facts; excludes initial transcript entry, setup, and review-only acknowledgements.',
  CLARIFICATION_COUNT: 'Actual changed controls supplying a fact absent from the source; a subset of MANUAL_OWNER_INPUT_COUNT. Re-entering already stated facts is never clarification.',
  SOURCE_REENTRY_COUNT: 'Actual changed controls restoring/correcting facts explicitly present in the captured source.',
  MANUAL_TRANSLATION_INPUT_COUNT: 'Actual manual entry of an explicitly supplied reviewed rendering; included in MANUAL_OWNER_INPUT_COUNT but not a clarification of missing business facts.',
  REVIEW_ACKNOWLEDGEMENT_COUNT: 'Review-only checkbox actions, reported separately from business-field inputs.',
  TAPS_TO_CONFIRM: 'Executed, trusted browser click operations after the successful source-save response through successful Confirm; null for unconfirmed/blocked cases. Native option selection and text entry are separately logged, not assigned estimated tap costs.',
  INITIAL_CAPTURE_TAPS: 'Executed click operations opening capture and saving the source, before the successful source-save response.',
  INITIAL_CAPTURE_ENTRY_COUNT: 'Initial transcript fill operations, excluded from manual business fields.',
});

export function summarizeCase(record) {
  const edits = record.operations.filter(op => op.phase === 'review' && op.kind === 'field_edit');
  const clicks = phase => record.operations.filter(op => op.phase === phase && op.kind === 'click').length;
  const completed = record.status === 'completed';
  return {...record,
    MANUAL_OWNER_INPUT_COUNT: edits.length,
    CLARIFICATION_COUNT: edits.filter(op => op.fact_origin === 'new_owner_fact').length,
    SOURCE_REENTRY_COUNT: edits.filter(op => op.fact_origin === 'captured_source').length,
    MANUAL_TRANSLATION_INPUT_COUNT: edits.filter(op => op.fact_origin === 'manual_translation').length,
    REVIEW_ACKNOWLEDGEMENT_COUNT: record.operations.filter(op => op.phase === 'review' && op.kind === 'review_acknowledgement').length,
    NATIVE_SELECT_OPERATIONS: edits.filter(op => op.browser_operation === 'selectOption').length,
    TEXT_FILL_OPERATIONS: edits.filter(op => op.browser_operation === 'fill').length,
    TAPS_TO_CONFIRM: completed ? clicks('review') : null,
    TAPS_TO_REVIEW_OR_BLOCK: completed ? null : clicks('review'),
    INITIAL_CAPTURE_TAPS: clicks('capture'),
    INITIAL_CAPTURE_ENTRY_COUNT: record.operations.filter(op => op.phase === 'capture' && op.kind === 'transcript_entry').length,
  };
}

export function summarizeFamilies(cases) {
  return [...new Set(cases.map(c => c.family))].sort().map(family => {
    const rows = cases.filter(c => c.family === family), completed = rows.filter(c => c.status === 'completed');
    const average = key => completed.length ? completed.reduce((sum, r) => sum + r[key], 0) / completed.length : null;
    return {family, measured_cases: rows.length, completed: completed.length,
      safely_unresolved: rows.filter(c => c.status === 'safely_unresolved').length,
      blocked: rows.filter(c => c.status === 'blocked').length,
      failed: rows.filter(c => c.status === 'failed').length,
      averages_completed_only: Object.fromEntries(['MANUAL_OWNER_INPUT_COUNT', 'CLARIFICATION_COUNT', 'SOURCE_REENTRY_COUNT', 'TAPS_TO_CONFIRM'].map(key => [key, average(key)]))};
  });
}

export async function installClickAudit(page) {
  await page.addInitScript(() => {
    window.ownerFrictionClicks = [];
    document.addEventListener('click', event => {
      const target = event.target.closest('button,input,textarea,select,summary,a') || event.target;
      window.ownerFrictionClicks.push({trusted: event.isTrusted, tag: target.tagName,
        id: target.id || null, name: target.getAttribute('name'),
        text: (target.getAttribute('aria-label') || target.textContent || '').trim().slice(0, 100)});
    }, true);
  });
}

export function createMeasuredActions(page, record) {
  let phase = 'capture';
  const add = entry => record.operations.push({phase, ...entry});
  const click = async (locator, label) => {
    const before = await page.evaluate(() => window.ownerFrictionClicks.length);
    await locator.click();
    const events = await page.evaluate(start => window.ownerFrictionClicks.slice(start), before);
    assert.ok(events.some(event => event.trusted), `No actual trusted browser click: ${label}`);
    add({kind: 'click', label, browser_events: events});
  };
  const reveal = async locator => {
    await locator.waitFor({state: 'attached'});
    const ancestors = locator.locator('xpath=ancestor::details');
    for (let i = 0; i < await ancestors.count(); i++) {
      const disclosure = ancestors.nth(i);
      if (!await disclosure.evaluate(node => node.open)) await click(disclosure.locator(':scope > summary'), 'Reveal field disclosure');
    }
    await locator.waitFor({state: 'visible'});
  };
  return {
    setPhase(next) {phase = next;}, click,
    async transcript(locator, value) {
      assert.equal(phase, 'capture');
      await locator.fill(value);
      assert.equal(await locator.inputValue(), value);
      add({kind: 'transcript_entry', characters: value.length});
    },
    async field(locator, value, {key, origin, evidence}) {
      assert.equal(phase, 'review');
      assert.ok(['captured_source', 'new_owner_fact', 'manual_translation'].includes(origin));
      assert.ok(evidence, 'Every field edit must explain the source or explicit TEST clarification');
      await reveal(locator);
      const previous = await locator.inputValue();
      if (previous === String(value)) return false;
      const tag = await locator.evaluate(node => node.tagName);
      // selectOption exercises the real native control but emits input/change,
      // not a click. Report it separately instead of inventing physical taps or
      // leaving an OS-native popup open during the automated interaction.
      if (tag === 'SELECT') await locator.selectOption(String(value));
      else {await click(locator, `Focus business field: ${key}`); await locator.fill(String(value)); await locator.dispatchEvent('change');}
      assert.equal(await locator.inputValue(), String(value));
      add({kind: 'field_edit', key, previous, value: String(value), fact_origin: origin,
        evidence, browser_operation: tag === 'SELECT' ? 'selectOption' : 'fill'});
      return true;
    },
    async acknowledge(locator, {key, newFact = false, evidence}) {
      assert.equal(phase, 'review');
      assert.equal(await locator.isChecked(), false, 'A review must be an actual new owner click');
      await click(locator, `Review acknowledgement: ${key}`);
      assert.equal(await locator.isChecked(), true);
      add(newFact ? {kind: 'field_edit', key, previous: false, value: true,
        fact_origin: 'new_owner_fact', evidence, browser_operation: 'checkbox click'}
        : {kind: 'review_acknowledgement', key, evidence});
    },
  };
}
