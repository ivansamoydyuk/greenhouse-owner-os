import assert from 'node:assert/strict';

// Deterministic source selection includes unresolved task originals. Never hide
// an origin or successor to make source counts or sibling preservation pass.
export function taskFacts(proposal) {
  return proposal.action_type === 'UNRESOLVED' ? proposal.revision.payload.task_candidates : proposal.revision.payload;
}
export function sourceTasks(proposals, sourceId) {
  return proposals.filter(p => p.inbox_id === sourceId &&
    (['PERSONAL_TASK', 'WORKER_TASK'].includes(p.action_type) || p.action_type === 'UNRESOLVED' && p.revision.payload.task_candidates));
}
function exactInstruction(proposal, instruction) {
  return taskFacts(proposal)?.original_instruction?.replace(/[.]$/, '') === instruction.replace(/[.]$/, '');
}
export function intendedTask(proposals, sourceId, instruction) {
  return sourceTasks(proposals, sourceId)
    .filter(p => p.action_type === 'PERSONAL_TASK')
    .filter(p => exactInstruction(p, instruction))
    .sort((a, b) => a.id.localeCompare(b.id))[0];
}
export function intendedTaskCandidate(proposals, sourceId, instruction) {
  return sourceTasks(proposals, sourceId)
    .filter(p => p.action_type === 'UNRESOLVED' && !p.recovery)
    .filter(p => exactInstruction(p, instruction))
    .sort((a, b) => a.id.localeCompare(b.id))[0];
}
export function taskSnapshots(proposals, sourceId) {
  return proposals.filter(p => p.inbox_id === sourceId).map(p => ({id: p.id, action_type: p.action_type,
    state: p.state, version: p.version, payload: structuredClone(p.revision.payload)}))
    .sort((a, b) => a.id.localeCompare(b.id));
}
// The actionable queue excludes reserved origins; source accounting must not.
export async function readSourceProposals(api, owner, sourceId) {
  const source = await api(owner + `/captures/${sourceId}`);
  return Promise.all(source.proposals.map(slot => api(owner + `/confirmations/${slot.id}/preview`)));
}
function audienceCommand(proposal, audience) {
  assert.equal(proposal.action_type, 'UNRESOLVED', 'Bare task remains unresolved before explicit audience choice');
  assert.ok(taskFacts(proposal)?.original_instruction);
  assert.ok(proposal.revision.missing.includes('Для себя или работнику?'));
  assert.ok(['personal', 'worker'].includes(audience), 'Fixture audience must be explicit');
  return {origin_proposal_id: proposal.id, expected_version: proposal.version, audience};
}
async function assertRecovery(api, owner, before, origin, target, audience) {
  assert.notEqual(target.id, origin.id, 'Audience recovery creates a linked successor, never overwrites the origin');
  assert.equal(target.inbox_id, origin.inbox_id);
  assert.equal(target.action_type, audience === 'personal' ? 'PERSONAL_TASK' : 'WORKER_TASK');
  assert.ok(['READY', 'NEEDS_INPUT'].includes(target.state), 'Audience choice prepares only; confirmation remains separate');
  assert.equal(target.revision.payload.original_instruction, taskFacts(origin).original_instruction);
  assert.equal(target.recovery.origin_proposal_id, origin.id);
  assert.equal(target.recovery.origin_version, origin.version);
  assert.equal(target.recovery.recovery_proposal_id, target.id);
  const after = await api(owner + `/captures/${origin.inbox_id}`);
  assert.equal(after.text, before.text, 'Audience choice preserves exact captured text');
  assert.deepEqual(after.proposals.map(p => p.id).sort(), [...before.proposals.map(p => p.id), target.id].sort(),
    'Source includes every original slot plus exactly one successor');
  const unchanged = await api(owner + `/confirmations/${origin.id}/preview`);
  assert.deepEqual(unchanged.revision, origin.revision, 'Preparing a successor preserves the original revision');
  assert.equal(unchanged.version, origin.version);
  assert.equal(unchanged.state, origin.state, 'Origin retirement waits for target confirmation');
  return target;
}
// Use only where the existing suite already seeds fixtures through the API.
export async function recoverTaskFixture(api, owner, origin, audience) {
  const body = audienceCommand(origin, audience);
  const before = await api(owner + `/captures/${origin.inbox_id}`);
  const target = await api(owner + `/captures/${origin.inbox_id}/task-recovery`, body);
  return assertRecovery(api, owner, before, origin, target, audience);
}
// The caller opens the real R2 original. This helper performs only the existing
// native select and trusted submit; it does not confirm, assign or dispatch.
export async function chooseTaskAudience(page, api, owner, origin, audience, measured = null) {
  const body = audienceCommand(origin, audience);
  const before = await api(owner + `/captures/${origin.inbox_id}`);
  const form = page.locator('#sheet #r2-task-audience-form');
  await form.waitFor();
  const select = form.locator('[name=audience]');
  assert.equal(await select.inputValue(), '', 'Audience has no personal default');
  assert.equal(await page.locator('#sheet #r2-confirm').count(), 0, 'Ambiguous audience cannot be confirmed');
  if (measured) await measured.field(select, audience, {key: 'audience', origin: 'new_owner_fact', evidence: 'Explicit TEST owner choice; the captured instruction does not specify an audience.'});
  else await select.selectOption(audience);
  const response = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === owner + `/captures/${origin.inbox_id}/task-recovery`);
  const button = form.getByRole('button', {name: 'Открыть проверку', exact: true});
  if (measured) await measured.click(button, 'Prepare explicitly selected task audience');
  else await button.click();
  const result = await response;
  assert.equal(result.status(), 200, await result.text());
  const sent = result.request().postDataJSON();
  for (const [key, value] of Object.entries(body)) assert.equal(sent[key], value);
  const target = await assertRecovery(api, owner, before, origin, await result.json(), audience);
  await page.locator('#sheet #r2-confirm').waitFor();
  return target;
}
