import assert from 'node:assert/strict';

// Match the frozen training reference context, not a parser-specific alias.
// Both people remain distinct; the bare name must not select either worker.
export const ambiguousWorkerReferences = Object.freeze([
  Object.freeze({display_name: 'Асыф Газипаша', native_name: 'Asif Gazipasa'}),
  Object.freeze({display_name: 'Асыф Алания', native_name: 'Asif Alanya'}),
]);
const source = 'test:owner-friction-training';

export async function prepareAmbiguousWorkerReferences(api, config, workers, fixtureSetup) {
  assert.equal(config.environment, 'TEST_PREVIEW');
  assert.equal(config.actual_operations, false);
  const endpoint = `/v1/entities/${config.entity_id}/workers`, prepared = [];
  for (const reference of ambiguousWorkerReferences) {
    const matches = workers.filter(worker => worker.display_name === reference.display_name);
    assert.ok(matches.length <= 1, 'The synthetic full worker name must be unique');
    let worker = matches[0];
    if (!worker) {
      worker = await api(endpoint, {...reference, language: 'fa',
        notes: 'Синтетический работник только для закрытого TEST benchmark: неоднозначное имя.', source});
      fixtureSetup.push({kind: 'TEST ambiguous worker reference', id: worker.id, name: worker.display_name});
    }
    assert.equal(worker.entity_id, config.entity_id);
    assert.equal(worker.display_name, reference.display_name);
    assert.equal(worker.native_name, reference.native_name);
    assert.equal(worker.language, 'fa');
    assert.equal(worker.active, true);
    assert.equal(worker.source, source);
    assert.equal(worker.contact, null);
    assert.equal(worker.telegram_ref, null);
    assert.equal(worker.default_greenhouse_id ?? null, null, 'No task assignment or greenhouse scope is inferred');
    assert.ok(worker.id);
    prepared.push(worker);
  }
  assert.equal(new Set(prepared.map(worker => worker.id)).size, 2, 'Ambiguous workers retain distinct server IDs');
  return prepared;
}
