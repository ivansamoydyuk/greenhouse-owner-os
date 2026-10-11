import test from 'node:test';
import assert from 'node:assert/strict';
import {summarizeCase, summarizeFamilies} from '../e2e/friction-metrics.mjs';

test('friction counts actual business edits separately from capture and review acknowledgements', () => {
  const row = summarizeCase({family: 'payment', status: 'completed', operations: [
    {phase: 'capture', kind: 'click'}, {phase: 'capture', kind: 'transcript_entry'}, {phase: 'capture', kind: 'click'},
    {phase: 'review', kind: 'click'},
    {phase: 'review', kind: 'field_edit', fact_origin: 'captured_source', browser_operation: 'fill'},
    {phase: 'review', kind: 'field_edit', fact_origin: 'new_owner_fact', browser_operation: 'selectOption'},
    {phase: 'review', kind: 'review_acknowledgement'}, {phase: 'review', kind: 'click'},
  ]});
  assert.equal(row.MANUAL_OWNER_INPUT_COUNT, 2);
  assert.equal(row.CLARIFICATION_COUNT, 1);
  assert.equal(row.SOURCE_REENTRY_COUNT, 1);
  assert.equal(row.REVIEW_ACKNOWLEDGEMENT_COUNT, 1);
  assert.equal(row.NATIVE_SELECT_OPERATIONS, 1);
  assert.equal(row.TEXT_FILL_OPERATIONS, 1);
  assert.equal(row.TAPS_TO_CONFIRM, 2);
  assert.equal(row.INITIAL_CAPTURE_TAPS, 2);
  assert.equal(row.INITIAL_CAPTURE_ENTRY_COUNT, 1);
});

test('blocked or unresolved cases never imply zero-input successful confirmation', () => {
  const cases = ['blocked', 'safely_unresolved', 'failed'].map(status => summarizeCase({
    family: 'worker', status, operations: [{phase: 'review', kind: 'click'}],
  }));
  for (const row of cases) {
    assert.equal(row.TAPS_TO_CONFIRM, null);
    assert.equal(row.TAPS_TO_REVIEW_OR_BLOCK, 1);
  }
  const [family] = summarizeFamilies(cases);
  assert.equal(family.measured_cases, 3);
  assert.equal(family.completed, 0);
  assert.equal(family.blocked, 1);
  assert.equal(family.safely_unresolved, 1);
  assert.equal(family.failed, 1);
  assert.ok(Object.values(family.averages_completed_only).every(value => value === null));
});

test('manual translation remains a real owner input without pretending it is a new source fact', () => {
  const row = summarizeCase({family: 'worker', status: 'completed', operations: [
    {phase: 'review', kind: 'field_edit', fact_origin: 'manual_translation', browser_operation: 'fill'},
    {phase: 'review', kind: 'click'},
  ]});
  assert.equal(row.MANUAL_OWNER_INPUT_COUNT, 1);
  assert.equal(row.MANUAL_TRANSLATION_INPUT_COUNT, 1);
  assert.equal(row.CLARIFICATION_COUNT, 0);
  assert.equal(row.SOURCE_REENTRY_COUNT, 0);
  assert.equal(row.TEXT_FILL_OPERATIONS, 1);
});

test('family completion averages exclude blocked flows instead of lowering apparent effort', () => {
  const cases = [summarizeCase({family: 'task', status: 'completed', operations: [
    {phase: 'review', kind: 'click'}, {phase: 'review', kind: 'click'},
    {phase: 'review', kind: 'field_edit', fact_origin: 'new_owner_fact', browser_operation: 'fill'},
  ]}), summarizeCase({family: 'task', status: 'blocked', operations: []})];
  const [family] = summarizeFamilies(cases);
  assert.equal(family.measured_cases, 2);
  assert.equal(family.completed, 1);
  assert.equal(family.averages_completed_only.TAPS_TO_CONFIRM, 2);
  assert.equal(family.averages_completed_only.MANUAL_OWNER_INPUT_COUNT, 1);
});
