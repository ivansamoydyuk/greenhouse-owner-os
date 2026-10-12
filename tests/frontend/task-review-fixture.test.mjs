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

// These are mocked helper checks, not browser acceptance. Real E2E suites keep
// all capture phrases and assert their authoritative API/SQL lifecycle effects.
const helpers = await import('../e2e/task-review-fixture.mjs');
function candidate(id = 'origin', instruction = 'Сегодня полить.') {
  return {id, inbox_id: 'source', action_type: 'UNRESOLVED', state: 'NEEDS_INPUT', version: 1,
    revision: {payload: {note: instruction, task_candidates: {original_instruction: instruction, greenhouse_id: 'greenhouse', planned_date: '2026-10-12'}},
      missing: ['Для себя или работнику?'], provenance: {task_candidates: {category: 'INFERENCE'}}}};
}
function recoveryFixture({fault} = {}) {
  const origin = candidate(), sibling = task('sibling', 'Сверить строки');
  const target = {...task('target', 'Сегодня полить.'), recovery: {origin_proposal_id: origin.id, origin_version: 1, recovery_proposal_id: 'target'}};
  const calls = []; let recovered = false;
  const source = () => ({id: 'source', text: 'Сегодня полить.\nСверить строки', proposals: recovered ? [origin, sibling, target] : [origin, sibling]});
  const api = async (path, body) => {
    calls.push({path, body});
    if (body !== undefined) {
      assert.equal(path, '/owner/captures/source/task-recovery');recovered = true;
      return structuredClone(target);
    }
    if (path === '/owner/captures/source') {
      const result = structuredClone(source());
      if(recovered && fault === 'missing-origin') result.proposals = result.proposals.filter(p => p.id !== origin.id);
      if(recovered && fault === 'extra-successor') result.proposals.push(task('extra', 'Сегодня полить.'));
      if(recovered && fault === 'changed-source') result.text = 'Changed';
      return result;
    }
    const id = path.split('/').at(-2), result = structuredClone([origin,sibling,target].find(p => p.id === id));
    assert.ok(result, path);
    if(recovered && id === origin.id && fault === 'changed-origin') result.revision.payload.note = 'Changed';
    return result;
  };
  return {origin,target,api,calls,commit: () => api('/owner/captures/source/task-recovery',{origin_proposal_id:'origin',expected_version:1,audience:'personal'})};
}

test('source task accounting keeps ambiguous originals, repeated instructions, successor and generic unresolved history', () => {
  const origin=candidate('b'), repeated=candidate('a'), target=task('target','Сегодня полить.'), generic=task('generic','unknown','source','UNRESOLVED');
  origin.recovery={recovery_proposal_id:target.id};
  const list=[origin,repeated,target,generic,task('foreign','Сегодня полить.','foreign')];
  assert.equal(helpers.intendedTaskCandidate(list,'source','Сегодня полить.').id,'a');
  assert.equal(intendedTask(list,'source','Сегодня полить.').id,'target');
  assert.equal(sourceTasks(list,'source').length,3,'Two exact original slots plus successor');
  assert.equal(taskSnapshots(list,'source').length,4,'Generic unresolved history also survives');
  assert.equal(helpers.taskFacts(origin).original_instruction,'Сегодня полить.');
});

test('fixture API recovery submits explicit audience, retains both origins and adds exactly one unconfirmed target', async () => {
  const fixture=recoveryFixture(), original=structuredClone(fixture.origin);
  const target=await helpers.recoverTaskFixture(fixture.api,'/owner',fixture.origin,'personal');
  assert.equal(target.action_type,'PERSONAL_TASK');assert.deepEqual(fixture.origin,original);
  assert.deepEqual(fixture.calls.filter(c=>c.body!==undefined),[{path:'/owner/captures/source/task-recovery',body:{origin_proposal_id:'origin',expected_version:1,audience:'personal'}}]);
  assert.ok(!fixture.calls.some(c=>/\/(?:confirm|assign|dispatch)$/.test(c.path)));
});

test('fixture audience cannot silently default or mutate a non-candidate', async () => {
  for(const audience of ['',undefined,'default']) {
    const fixture=recoveryFixture();await assert.rejects(helpers.recoverTaskFixture(fixture.api,'/owner',fixture.origin,audience));assert.equal(fixture.calls.length,0);
  }
  const fixture=recoveryFixture();await assert.rejects(helpers.recoverTaskFixture(fixture.api,'/owner',fixture.target,'personal'));assert.equal(fixture.calls.length,0);
});

for(const fault of ['missing-origin','extra-successor','changed-source','changed-origin']) test(`fixture recovery rejects ${fault} instead of weakening source accounting`, async () => {
  const fixture=recoveryFixture({fault});await assert.rejects(helpers.recoverTaskFixture(fixture.api,'/owner',fixture.origin,'personal'));
});

test('full-source snapshots read all capture IDs rather than the actionable queue', async () => {
  const fixture=recoveryFixture();await fixture.commit();
  const rows=await helpers.readSourceProposals(fixture.api,'/owner','source');
  assert.deepEqual(rows.map(p=>p.id),['origin','sibling','target']);
  assert.equal(taskSnapshots(rows,'source').length,3);
  assert.ok(!fixture.calls.some(c=>c.path==='/owner/confirmations'));
});

function mockAudiencePage(fixture, measured = false) {
  const events=[];let selected='', responsePredicate;
  const response={status:()=>200,text:async()=>JSON.stringify(fixture.target),json:async()=>structuredClone(fixture.target),request:()=>({postDataJSON:()=>({origin_proposal_id:'origin',expected_version:1,audience:selected,reason:'owner input'}),method:()=> 'POST'}),url:()=> 'http://preview/owner/captures/source/task-recovery'};
  const select={inputValue:async()=>selected,selectOption:async value=>{selected=value;events.push(['select',value]);}};
  const button={click:async()=>{events.push(['click','submit']);await fixture.commit();assert.equal(responsePredicate(response),true);}};
  const form={waitFor:async()=>events.push(['visible','form']),locator:selector=>{assert.equal(selector,'[name=audience]');return select;},getByRole:(role,options)=>{assert.equal(role,'button');assert.equal(options.name,'Открыть проверку');return button;}};
  const page={locator:selector=>selector.endsWith('#r2-task-audience-form')?form:{count:async()=>0,waitFor:async()=>events.push(['visible','ordinary-confirm'])},waitForResponse:predicate=>{responsePredicate=predicate;return Promise.resolve(response);}};
  const metrics=measured?{field:async(locator,value,details)=>{assert.equal(details.origin,'new_owner_fact');assert.equal(details.key,'audience');events.push(['metric','clarification']);await locator.selectOption(value);},click:async(locator,label)=>{events.push(['metric',label]);await locator.click();}}:null;
  return {page,events,metrics};
}
for(const measured of [false,true]) test(`real-control helper preserves explicit selection and only submits audience preparation (measured=${measured})`, async () => {
  const fixture=recoveryFixture(), mock=mockAudiencePage(fixture,measured);
  const target=await helpers.chooseTaskAudience(mock.page,fixture.api,'/owner',fixture.origin,'personal',mock.metrics);
  assert.equal(target.id,'target');
  assert.equal(mock.events.filter(e=>e[0]==='click').length,1);
  assert.ok(mock.events.find(e=>e[0]==='select'&&e[1]==='personal'));
  assert.deepEqual(mock.events.at(-1),['visible','ordinary-confirm']);
  assert.equal(mock.events.filter(e=>e[0]==='metric'&&e[1]==='clarification').length,measured?1:0);
});
