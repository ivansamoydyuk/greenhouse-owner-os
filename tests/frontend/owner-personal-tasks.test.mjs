import test from 'node:test';
import assert from 'node:assert/strict';
import {personalTasksUI,personalTaskRows,personalTaskDetail} from '../../src/greenhouse_owner_os/web/personal-tasks.js';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const decode=value=>String(value).replaceAll('&quot;','"').replaceAll('&#39;',"'").replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
const error=(status,message='TEST unavailable')=>Object.assign(Error(message),{status});
const fixture=(id,index=0)=>({id,entity_id:'entity-a',owner_id:'owner-a',proposal_id:'proposal-'+id,title:'Личная задача '+id,
  greenhouse_id:'greenhouse',planned_date:index>2?null:'2026-10-10',category:'irrigation',relative_time_context:index>2?'следующая неделя':null,
  note:'Сохранённая заметка',original_instruction:'TEST original '+id,state:'TO_DO',version:1,
  actions:[{id:'action-'+id,version:1,state:'TO_DO',actor_id:'owner-a',created_at:'2026-10-10T09:00:00Z'}]});

function harness({count=5,records,handler,storage=new Map()}={}){
  const prior=globalThis.document,nodes=new Map(),requests=[],notices=[],sheets=[],replay=new Map();
  let entity='entity-a',owner='owner-a',auth=0,active=true,revision=0,reloads=0,effects=0,ui;
  const tasks=records||Array.from({length:count},(_,n)=>fixture('task-'+(n+1),n));
  const data=()=>({entity_name:'TEST Farm',owner:{owner_id:owner},work_areas:[{id:'greenhouse',name:'TEST greenhouse'}]});
  function node(tag='div',attrs=''){
    const id=attrs.match(/\bid="([^"]+)"/)?.[1],dataset={};
    for(const match of attrs.matchAll(/data-([a-z-]+)="([^"]*)"/g))dataset[match[1].replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=decode(match[2]);
    const n={id,tag,dataset,disabled:/\bdisabled\b/.test(attrs),open:false,children:[],textContent:'',_html:'',attributes:{},isConnected:true,
      get innerHTML(){return this._html;},set innerHTML(value){
        for(const child of this.children){child.isConnected=false;if(child.id)nodes.delete('#'+child.id);}
        this._html=value;this.children=[];
        for(const match of value.matchAll(/<(\w+)\b([^>]*)>/g)){
          if(!/\bid=|\bdata-personal-open=/.test(match[2]))continue;
          const child=node(match[1],match[2]);this.children.push(child);if(child.id)nodes.set('#'+child.id,child);
        }
      },
      setAttribute(key,value){this.attributes[key]=value;},
      querySelectorAll(selector){return selector==='[data-personal-open]'?this.children.filter(child=>child.dataset.personalOpen):[];},
      focus(){document.activeElement=this;},
      close(){this.open=false;revision++;},
    };return n;
  }
  const sheet=node('dialog','id="sheet"'),content=node('div','id="sheet-content"');nodes.set('#sheet',sheet);nodes.set('#sheet-content',content);
  globalThis.document={querySelector:selector=>nodes.get(selector)||null,activeElement:null};
  const memory={getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)};
  const page=(state='TO_DO',offset=0,limit=20)=>({state,offset,limit,counts:{TO_DO:tasks.filter(t=>t.state==='TO_DO').length,DONE:tasks.filter(t=>t.state==='DONE').length},
    total:tasks.filter(t=>t.state===state).length,items:structuredClone(tasks.filter(t=>t.state===state).slice(offset,offset+limit)),
    next_offset:tasks.filter(t=>t.state===state).length>offset+limit?offset+limit:null});
  async function defaultAPI(path,body){
    const url=new URL(path,'http://test'),id=decodeURIComponent(url.pathname.split('/').at(body?-2:-1));
    if(body){
      const key=path+JSON.stringify(body);if(replay.has(key))return structuredClone(replay.get(key));
      const task=tasks.find(t=>t.id===id);if(task.version!==body.expected_version)throw error(409,'Task changed');
      task.state=body.state;task.version++;task.actions.push({version:task.version,state:task.state,created_at:'2026-10-10T10:00:00Z'});effects++;
      const result=structuredClone(task);replay.set(key,result);return result;
    }
    if(url.search)return page(url.searchParams.get('state'),Number(url.searchParams.get('offset')),Number(url.searchParams.get('limit')));
    return structuredClone(tasks.find(t=>t.id===id));
  }
  const api=async(path,body,options)=>{const request={path,body,options};requests.push(request);return handler?handler(request,defaultAPI):defaultAPI(path,body);};
  function openSheet(title,html){sheets.push({title,html});revision++;sheet.open=true;content.innerHTML=html;}
  function mount(){
    const old=nodes.get('#personal-task-panel');if(old){old.isConnected=false;for(const child of old.children)if(child.id)nodes.delete('#'+child.id);}
    const html=ui.render(),panel=node('section','id="personal-task-panel"');nodes.set('#personal-task-panel',panel);panel.innerHTML=html;return ui.bind();
  }
  ui=personalTasksUI({api,openSheet,getData:data,getEntity:()=>entity,getSheetRevision:()=>revision,getAuthGeneration:()=>auth,
    isPageActive:()=>active,getStorage:()=>memory,notice:message=>notices.push(message),reload:async()=>{reloads++;await mount();}});
  return {ui,nodes,tasks,requests,notices,sheets,sheet,storage,mount,page,defaultAPI,openSheet,
    html:()=>nodes.get('#personal-task-panel')?._html||'',detail:()=>content._html,
    click:id=>{const n=nodes.get(id);assert.ok(n,id);return n.onclick?.();},
    switchOwner:()=>{owner='owner-b';auth++;},switchEntity:()=>{entity='entity-b';},newSession:()=>auth++,
    leave:()=>{active=false;ui.invalidateView();sheet.close();},return:()=>{active=true;return mount();},
    logout:()=>{ui.logout();owner=null;auth++;sheet.close();},reloads:()=>reloads,effects:()=>effects,
    cleanup:()=>{globalThis.document=prior;},
  };
}

test('personal list reaches the fourth and undated tasks without completing earlier tasks',async()=>{
  const h=harness();try{await h.mount();assert.match(h.html(),/task-4/);assert.match(h.html(),/task-5/);assert.match(h.html(),/следующая неделя/);assert.equal(h.effects(),0);await h.ui.read('task-4');assert.match(h.detail(),/TEST original task-4/);assert.match(h.detail(),/Без даты/);assert.equal(h.requests.at(-1).path,'/v1/entities/entity-a/owner/tasks/task-4');}finally{h.cleanup();}
});

test('personal list pagination visits later records and restores keyboard focus',async()=>{
  const h=harness({count:21});try{await h.mount();assert.match(h.html(),/task-20/);assert.doesNotMatch(h.html(),/task-21/);await h.click('#personal-next');assert.match(h.html(),/task-21/);assert.match(h.html(),/Показано 21–21 из 21/);assert.equal(document.activeElement.id,'personal-task-status');await h.click('#personal-prev');assert.match(h.html(),/task-1/);assert.equal(h.effects(),0);}finally{h.cleanup();}
});

test('fourth task DONE, completed detail and explicit restore use the same identity/version',async()=>{
  const h=harness();try{await h.mount();await h.ui.read('task-4');await h.click('#personal-task-change');assert.equal(h.sheet.open,false);assert.equal(h.tasks[3].state,'DONE');assert.deepEqual(h.tasks.slice(0,3).map(t=>t.state),['TO_DO','TO_DO','TO_DO']);assert.doesNotMatch(h.html(),/data-personal-open="task-4"/);await h.click('#personal-completed');await h.ui.read('task-4');assert.match(h.detail(),/Вернуть в активные/);await h.click('#personal-task-change');assert.equal(h.tasks[3].version,3);assert.equal(h.tasks[3].state,'TO_DO');assert.deepEqual(h.tasks[3].actions.map(a=>a.state),['TO_DO','DONE','TO_DO']);assert.equal(h.reloads(),2);assert.equal(h.effects(),2);assert.deepEqual(h.requests.filter(r=>r.body).map(r=>r.body),[{expected_version:1,state:'DONE'},{expected_version:2,state:'TO_DO'}]);}finally{h.cleanup();}
});

test('DONE and restore put keyboard focus on the refreshed list rather than a removed row',async()=>{
  const h=harness();try{await h.mount();await h.ui.read('task-4');await h.click('#personal-task-change');assert.equal(document.activeElement.id,'personal-task-status');await h.click('#personal-completed');await h.ui.read('task-4');await h.click('#personal-task-change');assert.equal(document.activeElement.id,'personal-task-status');assert.equal(document.activeElement.isConnected,true);}finally{h.cleanup();}
});

test('completed-only tasks are reachable from an empty active list',async()=>{
  const records=[{...fixture('done'),state:'DONE',version:2}];const h=harness({records});try{await h.mount();assert.match(h.html(),/Активных личных задач нет/);assert.match(h.html(),/Завершённые · 1/);await h.click('#personal-completed');assert.match(h.html(),/data-personal-open="done"/);}finally{h.cleanup();}
});

test('filter survives close/reopen, Back/Forward-style remount and browser session storage restore',async()=>{
  const storage=new Map(),h=harness({storage});try{await h.mount();await h.click('#personal-completed');h.leave();await h.return();assert.equal(h.ui.getFilter(),'DONE');assert.match(h.html(),/Завершённых личных задач пока нет/);}finally{h.cleanup();}
  const restored=harness({storage});try{await restored.mount();assert.equal(restored.ui.getFilter(),'DONE');assert.match(restored.requests[0].path,/state=DONE/);}finally{restored.cleanup();}
});

test('invalid stored preferences fall back safely without blocking the list',async()=>{
  const storage=new Map([['owner-personal-task-view:entity-a:owner-a','{"filter":"__proto__","offset":-1}']]);const h=harness({storage});try{await h.mount();assert.equal(h.ui.getFilter(),'TO_DO');assert.match(h.html(),/task-1/);}finally{h.cleanup();}
});

test('late previous filter response cannot replace the current completed filter',async()=>{
  const slow=deferred();const h=harness({handler:({path,body},next)=>path.includes('state=TO_DO')?slow.promise:next(path,body)});try{const first=h.mount();await tick();await h.click('#personal-completed');slow.resolve(h.page());await first;assert.equal(h.ui.getFilter(),'DONE');assert.doesNotMatch(h.html(),/data-personal-open/);assert.equal(h.requests[0].options.isCurrent(),false);}finally{h.cleanup();}
});

test('list service failure preserves a visible retry and never pretends the list is empty',async()=>{
  let fail=true;const h=harness({handler:({path,body},next)=>{if(fail)throw error(503,'TEST list outage');return next(path,body);}});try{await h.mount();assert.match(h.html(),/Задачи не загружены/);assert.match(h.html(),/TEST list outage/);assert.doesNotMatch(h.html(),/Активных личных задач нет|data-personal-open/);fail=false;await h.click('#personal-list-retry');assert.match(h.html(),/task-4/);}finally{h.cleanup();}
});

test('wrong-owner task response is rejected before private content renders',async()=>{
  const h=harness({handler:async(request,next)=>{const response=await next(request.path,request.body);response.items[0].owner_id='owner-b';response.items[0].title='PRIVATE OTHER OWNER';return response;}});try{await h.mount();assert.match(h.html(),/Не удалось проверить ответ/);assert.doesNotMatch(h.html(),/PRIVATE OTHER OWNER/);}finally{h.cleanup();}
});

test('false empty successful response with a positive total is rejected',async()=>{
  const h=harness({handler:async(request,next)=>({...await next(request.path,request.body),items:[]})});try{await h.mount();assert.match(h.html(),/получен не полностью/);assert.doesNotMatch(h.html(),/Активных личных задач нет/);}finally{h.cleanup();}
});

test('a truncated positive page cannot hide later tasks behind a missing next page',async()=>{
  const h=harness({handler:async(request,next)=>{const result=await next(request.path,request.body);result.items=result.items.slice(0,3);return result;}});try{await h.mount();assert.match(h.html(),/получен не полностью/);assert.doesNotMatch(h.html(),/data-personal-open/);}finally{h.cleanup();}
});

test('an emptied later page resets to the start after another client changes state',async()=>{
  const h=harness({count:21});try{await h.mount();h.tasks[20].state='DONE';await h.click('#personal-next');assert.match(h.html(),/Показано 1–20 из 20/);assert.match(h.requests.at(-1).path,/offset=0/);}finally{h.cleanup();}
});

for(const boundary of ['close','new sheet','logout','entity','same-owner new session','navigation'])test('late personal detail is fenced after '+boundary,async()=>{
  const held=deferred(),h=harness({handler:({path,body},next)=>path.endsWith('/task-4')?held.promise:next(path,body)});try{
    await h.mount();const work=h.ui.read('task-4');await tick();
    if(boundary==='close')h.sheet.close();if(boundary==='new sheet')h.openSheet('Another sheet','CURRENT VIEW');if(boundary==='logout')h.logout();if(boundary==='entity')h.switchEntity();if(boundary==='same-owner new session')h.newSession();if(boundary==='navigation')h.leave();
    held.resolve({...fixture('task-4'),title:'PRIVATE LATE RESPONSE'});await work;assert.doesNotMatch(h.detail(),/PRIVATE LATE RESPONSE/);assert.equal(h.requests.at(-1).options.isCurrent(),false);assert.deepEqual(h.notices,[]);
  }finally{h.cleanup();}
});

for(const boundary of ['logout','entity','same-owner new session','navigation'])test('late personal list is fenced after '+boundary,async()=>{
  const held=deferred(),h=harness({handler:()=>held.promise});try{const work=h.mount();await tick();if(boundary==='logout')h.logout();if(boundary==='entity')h.switchEntity();if(boundary==='same-owner new session')h.newSession();if(boundary==='navigation')h.leave();const response=h.page();response.items[0].title='PRIVATE LATE LIST';held.resolve(response);await work;assert.doesNotMatch(h.html(),/PRIVATE LATE LIST/);assert.equal(h.requests[0].options.isCurrent(),false);assert.deepEqual(h.notices,[]);}finally{h.cleanup();}
});

test('a late error from a prior personal session cannot replace a newer sheet',async()=>{
  const held=deferred(),h=harness({handler:({path,body},next)=>path.endsWith('/task-4')?held.promise:next(path,body)});try{await h.mount();const work=h.ui.read('task-4');h.openSheet('Current','CURRENT');held.reject(error(503,'PRIVATE OLD ERROR'));await work;assert.equal(h.detail(),'CURRENT');assert.deepEqual(h.notices,[]);}finally{h.cleanup();}
});

test('newer detail request wins when two task reads resolve out of order',async()=>{
  const held=deferred(),h=harness({handler:({path,body},next)=>path.endsWith('/task-4')?held.promise:next(path,body)});try{await h.mount();const old=h.ui.read('task-4');await h.ui.read('task-5');held.resolve(fixture('task-4'));await old;assert.match(h.detail(),/TEST original task-5/);assert.doesNotMatch(h.detail(),/TEST original task-4/);}finally{h.cleanup();}
});

test('response-loss retry sends the exact same state command and does not invent another version',async()=>{
  let fail=true;const h=harness({handler:async({path,body},next)=>{const response=await next(path,body);if(body&&fail){fail=false;throw error(0,'TEST response lost');}return response;}});try{await h.mount();await h.ui.read('task-4');await h.click('#personal-task-change');assert.equal(h.effects(),1);assert.match(h.nodes.get('#personal-task-error').textContent,/TEST response lost/);assert.equal(h.sheet.open,true);await h.click('#personal-task-change');const writes=h.requests.filter(r=>r.body);assert.equal(writes.length,2);assert.deepEqual(writes[0].body,writes[1].body);assert.equal(h.effects(),1);assert.equal(h.tasks[3].version,2);}finally{h.cleanup();}
});

test('duplicate state clicks share one pending UI operation',async()=>{
  const held=deferred(),h=harness({handler:({path,body},next)=>body?held.promise:next(path,body)});try{await h.mount();await h.ui.read('task-4');const first=h.click('#personal-task-change');await h.click('#personal-task-change');assert.equal(h.requests.filter(r=>r.body).length,1);h.sheet.close();held.resolve({...fixture('task-4'),state:'DONE',version:2});await first;assert.equal(h.reloads(),0);assert.deepEqual(h.notices,[]);}finally{h.cleanup();}
});

test('stale state update requires a fresh detail before an explicit new action',async()=>{
  const h=harness();try{await h.mount();await h.ui.read('task-4');h.tasks[3].state='DONE';h.tasks[3].version=2;await h.click('#personal-task-change');assert.match(h.nodes.get('#personal-task-error').textContent,/Задача изменилась/);assert.equal(h.nodes.get('#personal-task-change').disabled,true);assert.equal(h.reloads(),0);await h.click('#personal-task-refresh');assert.match(h.detail(),/Вернуть в активные/);await h.click('#personal-task-change');assert.equal(h.requests.filter(r=>r.body).at(-1).body.expected_version,2);assert.equal(h.tasks[3].state,'TO_DO');}finally{h.cleanup();}
});

test('malformed mutation acknowledgement cannot claim success or close the reviewed task',async()=>{
  const h=harness({handler:({path,body},next)=>body?{...fixture('wrong-id'),state:'DONE',version:2}:next(path,body)});try{await h.mount();await h.ui.read('task-4');await h.click('#personal-task-change');assert.equal(h.sheet.open,true);assert.equal(h.reloads(),0);assert.match(h.nodes.get('#personal-task-error').textContent,/не подтверждён/);assert.deepEqual(h.notices,[]);}finally{h.cleanup();}
});

test('all personal task/source/history text is escaped and no fact edit form is offered',()=>{
  const task={...fixture('x'),title:'<img onerror="alert(1)">',note:'<script>private()</script>',original_instruction:'<b>original</b>',category:'<x>',relative_time_context:'<later>'};
  const rows=personalTaskRows([task],{}),detail=personalTaskDetail(task,{});
  for(const html of [rows,detail])assert.doesNotMatch(html,/<img|<script|<x>|<later>/);
  assert.match(detail,/&lt;b&gt;original&lt;\/b&gt;/);assert.match(detail,/Исходная формулировка/);assert.match(detail,/История состояния/);assert.doesNotMatch(detail,/<input|<textarea|<form|Редактировать/);
});

test('an unresolved greenhouse name stays unknown rather than claiming no selected greenhouse',()=>{
  const task=fixture('reference');assert.match(personalTaskRows([task],{}),/Название теплицы недоступно/);assert.match(personalTaskDetail(task,{}),/Название теплицы недоступно/);assert.doesNotMatch(personalTaskDetail(task,{}),/<strong>Теплица:<\/strong> Не указана/);assert.match(personalTaskDetail({...task,greenhouse_id:null},{}),/<strong>Теплица:<\/strong> Не указана/);
});

test('no personal read or write is dispatched from an inactive route',async()=>{
  const h=harness();try{h.leave();await h.ui.load();await h.ui.read('task-4');assert.equal(h.requests.length,0);}finally{h.cleanup();}
});


test('failed filter and repeated retry keep keyboard focus on the current recovery control',async()=>{
 let fail=false;const h=harness({handler:({path,body},next)=>{if(fail)throw error(503);return next(path,body);}});try{await h.mount();fail=true;await h.click('#personal-completed');assert.equal(document.activeElement.id,'personal-list-retry');const first=document.activeElement;await h.click('#personal-list-retry');assert.notEqual(document.activeElement,first);assert.equal(document.activeElement.id,'personal-list-retry');assert.equal(document.activeElement.isConnected,true);fail=false;await h.click('#personal-list-retry');assert.equal(document.activeElement.id,'personal-task-status');}finally{h.cleanup();}
});


for(const initial of ['TO_DO','DONE'])test(`delayed ${initial==='TO_DO'?'DONE':'restore'} replay cannot announce an obsolete current state`,async()=>{
 const task=fixture('delayed-replay');if(initial==='DONE'){task.state='DONE';task.version=2;task.actions.push({version:2,state:'DONE',created_at:'2026-10-10T09:30:00Z'});}
 let lose=true;const h=harness({records:[task],handler:async({path,body},next)=>{const response=await next(path,body);if(body&&lose){lose=false;throw error(0,'TEST lost response');}return response;}});
 try{await h.mount();await h.ui.read(task.id);const originalVersion=task.version;await h.click('#personal-task-change');assert.equal(h.effects(),1);
  // A separate client performs the opposite transition after the first action
  // commits, but before this view retries its exact original command.
  task.state=initial;task.version++;task.actions.push({version:task.version,state:initial,created_at:'2026-10-10T11:00:00Z'});const authoritative=structuredClone(task);
  await h.click('#personal-task-change');assert.deepEqual(task,authoritative);assert.equal(task.version,originalVersion+2);assert.equal(h.effects(),1);
  const writes=h.requests.filter(request=>request.body);assert.equal(writes.length,2);assert.deepEqual(writes[0].body,writes[1].body);assert.deepEqual(h.notices,['Действие сохранено в истории.']);
  await h.ui.read(task.id);assert.match(h.detail(),initial==='TO_DO'?/Отметить готовой/:/Вернуть в активные/);
 }finally{h.cleanup();}
});
