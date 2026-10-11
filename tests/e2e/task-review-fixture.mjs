// Deterministic selection for a review-rendering fixture, never queue order.
export function sourceTasks(proposals, sourceId) {
  return proposals.filter(p => p.inbox_id === sourceId && ['PERSONAL_TASK', 'WORKER_TASK'].includes(p.action_type));
}

export function intendedTask(proposals, sourceId, instruction) {
  return sourceTasks(proposals, sourceId)
    .filter(p => p.action_type === 'PERSONAL_TASK')
    .filter(p => p.revision.payload.original_instruction?.replace(/[.]$/, '') === instruction.replace(/[.]$/, ''))
    .sort((a, b) => a.id.localeCompare(b.id))[0];
}

export function taskSnapshots(proposals, sourceId) {
  return proposals.filter(p => p.inbox_id === sourceId).map(p => ({id: p.id, action_type: p.action_type,
    state: p.state, version: p.version, payload: structuredClone(p.revision.payload)}))
    .sort((a, b) => a.id.localeCompare(b.id));
}
