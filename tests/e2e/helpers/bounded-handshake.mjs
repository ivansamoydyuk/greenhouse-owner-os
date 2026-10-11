const phases = new Set([
  'records-before', 'install-held-route', 'reload-held', 'warmup-entered',
  'recovery-visible', 'recovery-evidence', 'late-response-terminal',
  'remove-held-route', 'late-records', 'manual-reload', 'posted-confirmation',
  'records-after', 'cleanup-route',
]);

// These limits reject a stuck test handshake; they never manufacture success or
// extend the application's independent 15-second cold-bootstrap deadline.
export function boundedHandshake({
  checkpoint,
  timeoutMs = 30000,
  schedule = (callback, ms) => globalThis.setTimeout(callback, ms),
  cancel = handle => globalThis.clearTimeout(handle),
}) {
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0) throw Error('Invalid handshake deadline');
  return async (phase, work) => {
    if (!phases.has(phase)) throw Error('Unknown handshake phase');
    await checkpoint({phase, state:'started'});
    let timer, timedOut = false;
    try {
      const value = await Promise.race([
        Promise.resolve().then(work),
        new Promise((_, reject) => {
          timer = schedule(() => {
            timedOut = true;
            reject(Error(`Bootstrap recovery handshake timed out: ${phase}`));
          }, timeoutMs);
        }),
      ]);
      await checkpoint({phase, state:'complete'});
      return value;
    } catch (error) {
      await checkpoint({phase, state:'failed', timedOut}).catch(() => {});
      throw error;
    } finally {
      cancel(timer);
    }
  };
}
