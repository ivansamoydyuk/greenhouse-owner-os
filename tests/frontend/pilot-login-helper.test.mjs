import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const source=readFileSync(new URL('../e2e/helpers/pilot-auth.mjs',import.meta.url),'utf8');
const body=source.slice(source.indexOf('export async function ownerLogin('),source.indexOf('export async function provisionWorker(')).replace('export ','');
for(const pilotMode of [false,true])for(const navigate of [false,true])test(`${pilotMode?'private':'legacy'} owner login ${navigate?'starts login':'retains return route'} and matches exact destination with slash-bearing fragment`,async()=>{
 const calls=[],base='http://127.0.0.1:4173';let predicate,options;
 const page={goto:async url=>calls.push(['goto',url]),locator:selector=>({fill:async value=>calls.push(['fill',selector,value])}),getByRole:()=>({click:async()=>calls.push(['click'])}),waitForURL:async(match,waitOptions)=>{predicate=match;options=waitOptions;calls.push(['destination']);}};
 const owner={fixture:'synthetic owner'},login=new Function('pilotMode','pilotCredentials','fillPilotLogin','process',body+';return ownerLogin;')(pilotMode,async()=>({owner}),async(p,credentials)=>{assert.equal(p,page);assert.equal(credentials,owner);calls.push(['pilot login']);},{env:{PREVIEW_SECRET:'synthetic test input'}});
 await login(page,base,{navigate});
 assert.deepEqual(calls.filter(([kind])=>kind==='goto'),navigate?[['goto',base+(pilotMode?'/pilot/':'/r3/')]]:[]);
 assert.equal(calls.at(-1)[0],'destination');assert.equal(options.waitUntil,'domcontentloaded');assert.equal(typeof predicate,'function');
 for(const suffix of ['', '#home','#capture/text','#capture/expense','#harvest/part/2026/kg/TRY'])assert.equal(predicate(new URL(base+'/r3/connected.html'+suffix)),true,suffix);
 for(const destination of ['https://other.test/r3/connected.html#capture/text',base+'/pilot/?return=%23capture%2Ftext',base+'/worker',base+'/other/connected.html',base+'/r3/connected.html-extra',base+'/r3/connected.html/nested'])assert.equal(predicate(new URL(destination)),false,destination);
});
