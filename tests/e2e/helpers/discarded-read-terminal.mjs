// Only for the deliberately held, expired bootstrap GET. Record the observed
// terminal event; requestfailed alone does not identify its transport reason
// and is never evidence of a successful business call.
export function observeDiscardedReadTerminal(page, request) {
  if (!request || request.method() !== 'GET') throw Error('Expected the exact held bootstrap GET');
  let status = null, terminal = null, settled = false, resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  const dispose = () => {
    page.off('response', onResponse);
    page.off('requestfinished', onFinished);
    page.off('requestfailed', onFailed);
  };
  const settle = () => {
    if (settled || status === null || terminal === null) return;
    settled = true;
    dispose();
    resolve({status, terminal, disposition:terminal === 'requestfailed' ? 'expired-read-failed' : 'expired-read-finished'});
  };
  const onResponse = response => {
    if (response.request() !== request || settled) return;
    status = response.status();
    if (status !== 200) {
      settled = true;
      dispose();
      reject(Error(`Released bootstrap read returned unexpected HTTP ${status}`));
      return;
    }
    settle();
  };
  const onFinished = observed => { if(observed === request) { terminal = 'requestfinished'; settle(); } };
  const onFailed = observed => { if(observed === request) { terminal = 'requestfailed'; settle(); } };
  page.on('response', onResponse);
  page.on('requestfinished', onFinished);
  page.on('requestfailed', onFailed);
  return {promise, dispose};
}
