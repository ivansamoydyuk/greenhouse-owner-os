import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {observeOwnerLogout} from '../e2e/helpers/logout-diagnostics.mjs';
function fixture({closed=false,stalled=false,bodyStalled=false}={}){
 const listeners=new Map(),calls=[];let url='http://preview/r3/connected.html?private=query-secret#private-fragment';
 const frame={url:()=>url};
 const page={mainFrame:()=>frame,url:()=>url,isClosed:()=>closed,on:(event,handler)=>listeners.set(event,handler),off:(event,handler)=>{assert.equal(listeners.get(event),handler);listeners.delete(event);},evaluate:async()=>{calls.push('read-document');if(stalled)return new Promise(()=>{});return{ready_state:'complete',login_dom:true,owner_dom:false,owner_hidden:null};}};
 const context={request:{get:async(path,options)=>{calls.push({path,options});if(stalled)return new Promise(()=>{});return{status:()=>401};}}};
 const probe=observeOwnerLogout(page,context,'http://preview',{timeoutMs:10,maxEvents:8});
 const emit=(event,value)=>listeners.get(event)?.(value);
 emit('response',{url:()=> 'http://preview/pilot/logout?secret=must-not-log',request:()=>({method:()=> 'POST'}),status:()=>200,finished:()=>bodyStalled?new Promise(()=>{}):Promise.resolve(null)});
 return {probe,page,context,calls,listeners,emit,frame,navigate(){url='http://preview/pilot/?return=secret-fragment';emit('framenavigated',frame);emit('domcontentloaded');emit('load');}};
}

test('logout diagnostics contain only path/status/DOM flags, never URL queries or bodies',async()=>{
 const h=fixture();h.navigate();h.emit('requestfailed',{isNavigationRequest:()=>true,frame:()=>h.frame,url:()=> 'http://preview/pilot/?password=secret'});
 const result=await h.probe.capture('failed');assert.equal(result.pathname,'/pilot/');assert.equal(result.query_present,true);assert.equal(result.fragment_present,false);assert.equal(result.events[0].fragment_present,true);assert.equal(result.logout_status,200);assert.equal(result.logout_completion.value,true);assert.equal(result.session.value.status,401);assert.equal(result.document.value.login_dom,true);
 const json=JSON.stringify(result);assert.doesNotMatch(json,/query-secret|private-fragment|must-not-log|secret-fragment|password=|http:\/\/|headers|cookie|draft_key/);
 assert.equal(h.calls.find(x=>typeof x==='object').path,'http://preview/pilot/session');assert.ok(!h.calls.some(x=>x?.options?.method==='POST'));
 h.probe.stop();assert.equal(h.listeners.size,0);
});

test('closed pages skip DOM evaluation while session revocation stays independently readable',async()=>{
 const h=fixture({closed:true});const result=await h.probe.capture('failed');assert.equal(result.page_closed,true);assert.equal(result.document.state,'closed');assert.equal(result.session.value.status,401);assert.ok(!h.calls.includes('read-document'));h.probe.stop();
});

test('stalled document/session/response body probes are bounded and recorded honestly',async()=>{
 const h=fixture({stalled:true,bodyStalled:true});const result=await h.probe.capture('failed');
 assert.equal(result.document.state,'timeout');assert.equal(result.session.state,'timeout');assert.equal(result.logout_completion.state,'timeout');h.probe.stop();
});

test('navigation evidence is capped and ignores child-frame or non-navigation failures',async()=>{
 const h=fixture();h.emit('framenavigated',{url:()=> 'http://preview/child-secret'});h.emit('requestfailed',{isNavigationRequest:()=>false});
 for(let i=0;i<50;i++)h.navigate();const result=await h.probe.capture('failed');assert.equal(result.events.length,8);assert.ok(!JSON.stringify(result).includes('child-secret'));h.probe.stop();
});

test('existing held-read logout writes and assertions remain before diagnostic capture and failure screenshots',()=>{
 const source=readFileSync(new URL('../e2e/private-daily-pilot.mjs',import.meta.url),'utf8');
 assert.ok(source.includes("await click(page,'Выйти');assert.equal((await logoutReply).status(),200);releaseOwnerRead();await page.waitForURL('**/pilot/');assert.equal((await context.request.get(base+'/pilot/session')).status(),401);"));
 assert.match(source,/try\{await saveLogoutDiagnostic\('existing-logout-assertion-failed'\);\}\s*catch\{[^\n]*diagnostic_failed:true/);
 assert.ok(source.indexOf("await saveLogoutDiagnostic('existing-logout-assertion-failed')")<source.indexOf("await active.screenshot"));
 assert.match(source,/redactPilot\(JSON\.stringify\(result\.logout_diagnostics,null,2\)\)/);
});


test('diagnostic-save rejection never replaces the original logout assertion failure',async()=>{
 const source=readFileSync(new URL('../e2e/private-daily-pilot.mjs',import.meta.url),'utf8');
 const marker=source.indexOf("await saveLogoutDiagnostic('existing-logout-assertions-passed')"),start=source.indexOf('}catch(error){',marker),end=source.indexOf('\n  finally{',start);
 const handler=source.slice(start+1,end),result={},original=Error('original logout assertion');
 const run=new Function('saveLogoutDiagnostic','result','engine','original',`return(async()=>{try{throw original;}${handler}})();`);
 await assert.rejects(run(async()=>{throw Error('private diagnostic write failure details');},result,'chromium',original),error=>error===original);
 assert.deepEqual(result.logout_diagnostics,[{engine:'chromium',outcome:'existing-logout-assertion-failed',diagnostic_failed:true}]);
 assert.doesNotMatch(JSON.stringify(result),/private diagnostic write failure details/);
});
