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
  assert.equal(sourceTasks(proposals, 'source').length, 3);
  assert.equal(intendedTask(proposals, 'source', 'Удалить строки'), undefined);
  assert.deepEqual(taskSnapshots(proposals, 'source'), taskSnapshots([...proposals].reverse(), 'source'));
  const before = taskSnapshots(proposals, 'source');
  proposals[0].version = 2;
  assert.notDeepEqual(taskSnapshots(proposals, 'source'), before);
});
