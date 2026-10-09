// Deterministic selection for a review-rendering fixture, never queue order.
export function sourceTasks(proposals, sourceId) {
  return proposals.filter(p => p.inbox_id === sourceId && p.action_type === 'PERSONAL_TASK');
}

export function intendedTask(proposals, sourceId, instruction) {
  return sourceTasks(proposals, sourceId)
    .filter(p => p.revision.payload.original_instruction?.replace(/[.]$/, '') === instruction.replace(/[.]$/, ''))
    .sort((a, b) => a.id.localeCompare(b.id))[0];
}

export function taskSnapshots(proposals, sourceId) {
  return sourceTasks(proposals, sourceId).map(p => ({id: p.id, state: p.state, version: p.version, payload: p.revision.payload}))
    .sort((a, b) => a.id.localeCompare(b.id));
}
