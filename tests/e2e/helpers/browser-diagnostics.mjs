// Deliberately metadata only: no headers, bodies, cookies, queries, fragments,
// storage keys/values, error text, or authenticated key material are retained.
const phases=new Set(['config-read','draft-key-read','configure','configured','refresh-data','harvest-navigation-read','finance-navigation-read','generation-mismatch','render','render-returned','error','payment-loss-handler','payment-post-ack','payment-post-aborted','payment-reload-start','payment-reload-return','payment-unroute-return']);
export function safePath(value){try{const path=new URL(value).pathname;return /^\/(?:v1\/|pilot\/(?:session|draft-key|participants|receipts|login|logout)(?:\/|$)|preview\/config$|r3\/[a-z0-9.-]+$|assets\/[a-z0-9.-]+$)/i.test(path)?path.slice(0,240):'/other';}catch{return '/invalid';}}
export function metadataRing(limit=400){
 if(!Number.isInteger(limit)||limit<1||limit>2000)throw Error('Invalid diagnostic capacity');
 const entries=[];let dropped=0;
 return {record(input){
  const row={event:['request','response','finished','failed','checkpoint','storage'].includes(input.event)?input.event:'unknown'};
  for(const key of ['id','atMs','durationMs','status','generation'])if(Number.isFinite(input[key]))row[key]=input[key];
  if(input.url)row.path=safePath(input.url);
  if(input.method)row.method=['GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'].includes(input.method)?input.method:'UNKNOWN';
  if(phases.has(input.phase))row.phase=input.phase;
  if(['open','transaction'].includes(input.operation))row.operation=input.operation;
  if(['start','complete','success','error','abort','blocked'].includes(input.state))row.state=input.state;
  if(['readonly','readwrite','versionchange'].includes(input.mode))row.mode=input.mode;
  entries.push(row);if(entries.length>limit){entries.shift();dropped++;}
 },snapshot(){return {dropped,entries:entries.map(row=>({...row}))};}};
}
export async function observeBrowserDiagnostics(page){
 const started=Date.now(),ring=metadataRing(),requests=new WeakMap();let sequence=0;
 const elapsed=()=>Date.now()-started;
 page.on('request',request=>{const record={id:++sequence,url:request.url(),method:request.method(),atMs:elapsed()};requests.set(request,record);ring.record({event:'request',...record});});
 page.on('response',response=>{const record=requests.get(response.request());if(record)ring.record({event:'response',...record,atMs:elapsed(),durationMs:elapsed()-record.atMs,status:response.status()});});
 for(const [event,state] of [['requestfinished','finished'],['requestfailed','failed']])page.on(event,request=>{const record=requests.get(request);if(record)ring.record({event:state,...record,atMs:elapsed(),durationMs:elapsed()-record.atMs});});
 await page.addInitScript(()=>{
  const events=[];let sequence=0,dropped=0;
  const record=row=>{events.push({...row,atMs:performance.now()});if(events.length>100){events.shift();dropped++;}};
  window.__testStorageDiagnostics={snapshot:()=>({dropped,entries:events})};
  const open=IDBFactory.prototype.open;
  IDBFactory.prototype.open=function(...args){const request=open.apply(this,args);if(args[0]==='r3-connected-test-v1'){const id=++sequence;record({id,operation:'open',state:'start'});for(const state of ['success','error','blocked'])request.addEventListener(state,()=>record({id,operation:'open',state}));}return request;};
  const transaction=IDBDatabase.prototype.transaction;
  IDBDatabase.prototype.transaction=function(...args){const tx=transaction.apply(this,args);if(this.name==='r3-connected-test-v1'){const id=++sequence,mode=tx.mode;record({id,operation:'transaction',mode,state:'start'});for(const state of ['complete','error','abort'])tx.addEventListener(state,()=>record({id,operation:'transaction',mode,state}));}return tx;};
 });
 return {
  checkpoint:phase=>ring.record({event:'checkpoint',phase,atMs:elapsed()}),
  async capture(){
   const state=await page.evaluate(()=>({phase:document.querySelector('#app')?.dataset.bootstrapStage,generation:Number(document.querySelector('#app')?.dataset.bootstrapGeneration),rendered:Boolean(document.querySelector('#main')?.dataset.renderedRoute),storage:window.__testStorageDiagnostics?.snapshot()})).catch(()=>null);
   const storage=metadataRing(100);for(const row of state?.storage?.entries||[])storage.record({event:'storage',...row});
   const bootstrap=metadataRing(1);if(state)bootstrap.record({event:'checkpoint',phase:state.phase,generation:state.generation,atMs:elapsed()});
   return {network:ring.snapshot(),bootstrap:bootstrap.snapshot().entries[0]||null,rendered:state?.rendered??null,storage:{...storage.snapshot(),dropped:(state?.storage?.dropped||0)+storage.snapshot().dropped}};
  },
 };
}
