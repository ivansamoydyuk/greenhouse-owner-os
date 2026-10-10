import test from 'node:test';
import assert from 'node:assert/strict';
import {freshRoute} from '../e2e/helpers/fresh-route.mjs';
test('explicit same-route refresh reloads server data before readiness', async () => {
 const calls=[];const page={url:()=> 'http://test/r3/connected.html#worker-task/1',reload:async()=>calls.push('reload'),goto:async()=>calls.push('goto')};
 await freshRoute(page,'http://test','worker-task/1',async hash=>calls.push(hash));
 assert.deepEqual(calls,['reload','worker-task/1']);
});
test('different route navigates and keeps the exact context', async () => {
 const calls=[];const page={url:()=> 'http://test/r3/connected.html#home',reload:async()=>calls.push('reload'),goto:async url=>calls.push(url)};
 await freshRoute(page,'http://test','money/result/2026-10-10/TRY/all',async hash=>calls.push(hash));
 assert.deepEqual(calls,['http://test/r3/connected.html#money/result/2026-10-10/TRY/all','money/result/2026-10-10/TRY/all']);
});
