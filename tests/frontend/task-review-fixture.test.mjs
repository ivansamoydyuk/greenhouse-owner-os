import assert from 'node:assert/strict';
import test from 'node:test';
import {intendedTask, sourceTasks, taskSnapshots} from '../e2e/task-review-fixture.mjs';

const task = (id, instruction, source='source', kind='PERSONAL_TASK') => ({id, inbox_id: source, action_type: kind,
  state: 'READY', version: 1, revision: {payload: {original_instruction: instruction}}});
test('review fixture selects exact source and intended task regardless of queue order', () => {
  const proposals = [task('a', 'Сверить строки'), task('c', 'Сегодня полить.'), task('b', 'Сегодня полить'),
    task('0', 'Сегодня полить', 'foreign'), task('1', 'Сегодня полить', 'source', 'WORKER_TASK')];
  assert.equal(intendedTask(proposals, 'source', 'Сегодня полить.').id, 'b');
  assert.equal(intendedTask([...proposals].reverse(), 'source', 'Сегодня полить.').id, 'b');
  assert.equal(sourceTasks(proposals, 'source').length, 4);
  assert.equal(intendedTask(proposals, 'source', 'Удалить строки'), undefined);
  assert.deepEqual(taskSnapshots(proposals, 'source'), taskSnapshots([...proposals].reverse(), 'source'));
  const before = taskSnapshots(proposals, 'source');
  proposals[0].version = 2;
  assert.notDeepEqual(taskSnapshots(proposals, 'source'), before);
});

test('sibling preservation includes worker and unresolved slots without selecting either as the personal review target', () => {
  const proposals = [task('a', 'Сегодня полить'), task('b', 'TEST · Проверить давление', 'source', 'WORKER_TASK'),
    task('u', 'неясный остаток', 'source', 'UNRESOLVED')];
  assert.equal(sourceTasks(proposals, 'source').length, 2);
  assert.equal(intendedTask(proposals, 'source', 'Сегодня полить.').id, 'a');
  assert.equal(intendedTask(proposals, 'source', 'TEST · Проверить давление'), undefined);
  const before = taskSnapshots(proposals, 'source');
  assert.equal(before.length, 3);
  proposals[1].revision.payload.original_instruction = 'changed';
  assert.notDeepEqual(taskSnapshots(proposals, 'source'), before);
  proposals.pop();
  assert.notDeepEqual(taskSnapshots(proposals, 'source'), before);
});
