import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source=fs.readFileSync('prototype/owner-r3/connected.js','utf8');
const toastCode=source.slice(source.indexOf('function toast('),source.indexOf('function shell('));
function harness(){
 let now=0,nextId=0;const timers=new Map(),classes=new Set();
 const element={textContent:'',classList:{add:name=>classes.add(name),remove:name=>classes.delete(name)}};
 const setTimeout=(run,delay)=>{const id=nextId++;timers.set(id,{run,at:now+delay});return id;};
 const clearTimeout=id=>timers.delete(id);
 const toast=new Function('document','setTimeout','clearTimeout',`let toastTimer=null;${toastCode};return toast;`)({querySelector:()=>element},setTimeout,clearTimeout);
 return {toast,element,visible:()=>classes.has('show'),pending:()=>timers.size,tick(time){
  for(;;){const due=[...timers].filter(([,timer])=>timer.at<=time).sort((a,b)=>a[1].at-b[1].at)[0];if(!due)break;now=due[1].at;timers.delete(due[0]);due[1].run();}now=time;
 }};
}

test('a later toast stays visible for its own interval despite the earlier timeout',()=>{
 const h=harness();h.toast('First saved action');h.tick(1000);h.toast('Second saved action');
 h.tick(3500);assert.equal(h.visible(),true,'first timer must not hide the current message');assert.equal(h.element.textContent,'Second saved action');
 h.tick(4499);assert.equal(h.visible(),true);h.tick(4500);assert.equal(h.visible(),false);assert.equal(h.pending(),0);
});

test('rapid task messages cannot leave a stale timeout to hide the next assignment acknowledgement',()=>{
 const h=harness();h.toast('Task complete');h.tick(905);h.toast('Task restored');
 h.tick(3500);assert.equal(h.visible(),true,'only the latest timeout owns visibility');
 h.tick(4146);h.toast('Assignment saved');assert.equal(h.pending(),1);
 h.tick(4405);assert.equal(h.visible(),true,'restore timeout must not shorten assignment visibility');assert.equal(h.element.textContent,'Assignment saved');
 h.tick(7645);assert.equal(h.visible(),true);h.tick(7646);assert.equal(h.visible(),false);
});
