import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {ambiguousWorkerReferences, prepareAmbiguousWorkerReferences} from '../e2e/friction-worker-fixture.mjs';

const config = {environment: 'TEST_PREVIEW', actual_operations: false, entity_id: 'test-entity'};
const worker = (reference, id) => ({...reference, id, entity_id: config.entity_id, language: 'fa',
  active: true, source: 'test:owner-friction-training', contact: null, telegram_ref: null});

test('ambiguous-worker fixture creates two contact-free references before measurement without task effects', async () => {
  const writes = [], setup = [];
  const unrelated = worker({display_name: 'TEST Muhammed', native_name: 'TEST محمد'}, 'unrelated');
  const result = await prepareAmbiguousWorkerReferences(async (path, payload) => {
    writes.push({path, payload}); return worker(payload, `created-${writes.length}`);
  }, config, [unrelated], setup);
  assert.deepEqual(result.map(row => row.display_name), ['Асыф Газипаша', 'Асыф Алания']);
  assert.deepEqual(result.map(row => row.id), ['created-1', 'created-2']);
  assert.equal(writes.length, 2);
  for (const {path, payload} of writes) {
    assert.equal(path, '/v1/entities/test-entity/workers');
    assert.deepEqual(Object.keys(payload).sort(), ['display_name', 'language', 'native_name', 'notes', 'source']);
    assert.equal(payload.source, 'test:owner-friction-training');
    assert.match(payload.notes, /TEST benchmark/);
  }
  assert.deepEqual(setup.map(row => row.id), result.map(row => row.id));
  assert.equal(unrelated.display_name, 'TEST Muhammed');
});

test('existing exact synthetic names reuse their distinct IDs without another write', async () => {
  const rows = ambiguousWorkerReferences.map((reference, index) => worker(reference, `existing-${index}`));
  const setup = [];
  const result = await prepareAmbiguousWorkerReferences(async () => assert.fail('Unexpected write'), config, rows, setup);
  assert.deepEqual(result, rows);
  assert.deepEqual(setup, []);
});

test('reference setup rejects non-TEST environments before any write', async () => {
  for (const unsafe of [{...config, environment: 'production'}, {...config, actual_operations: true}]) {
    await assert.rejects(prepareAmbiguousWorkerReferences(async () => assert.fail('Unexpected write'), unsafe, [], []));
  }
});

test('duplicate, cross-entity, contacted, scoped, inactive or non-fixture refs fail closed', async () => {
  const rows = ambiguousWorkerReferences.map((reference, index) => worker(reference, `existing-${index}`));
  const invalid = [[...rows, {...rows[0], id: 'duplicate'}], rows.map(row => ({...row, id: 'same'}))];
  for (const change of [{entity_id: 'other'}, {contact: 'contact'}, {telegram_ref: 'channel'},
    {active: false}, {source: 'another-source'}, {default_greenhouse_id: 'scope'}]) {
    invalid.push([{...rows[0], ...change}, rows[1]]);
  }
  for (const refs of invalid) {
    await assert.rejects(prepareAmbiguousWorkerReferences(async () => assert.fail('Unexpected write'), config, refs, []));
  }
});

test('friction keeps all ten frozen examples and original blocked-worker assertions after fixture restoration', async () => {
  const harness = await fs.readFile(new URL('../e2e/owner-friction.mjs', import.meta.url), 'utf8');
  const sample = harness.slice(harness.indexOf('const sample = ['), harness.indexOf('].map(([id, family'));
  assert.equal((sample.match(/\['GOLD-/g) || []).length, 10);
  assert.ok(sample.includes("['GOLD-0387', 'worker_ambiguous_identity', 'WORKER_TASK', true]"));
  const setup = harness.indexOf('await prepareAmbiguousWorkerReferences(api, config, workers, fixtureSetup)');
  assert.ok(setup > 0 && setup < harness.indexOf('before = await snapshot();'));
  assert.match(harness, /else assert\.equal\(proposal\.action_type, test\.kind/);
  assert.match(harness, /assert\.equal\(proposal\.revision\.payload\.worker_id, null/);
  assert.match(harness, /assert\.equal\(proposal\.revision\.payload\.language, null/);
  assert.match(harness, /assert\.equal\(proposal\.revision\.payload\.rendered_text, null/);
  assert.match(harness, /assert\.equal\(proposal\.confirmed_effect, null/);
  assert.match(harness, /assert\.equal\(await sheet\(\)\.locator\('#r2-confirm'\)\.isDisabled\(\), true/);
  assert.match(harness, /assert\.equal\(source\.text, test\.text, 'Exact source preservation'\)/);
});
