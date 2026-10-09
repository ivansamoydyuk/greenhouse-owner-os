import test from 'node:test';
import assert from 'node:assert/strict';
import {displayDate,displayDateTime} from '../../src/greenhouse_owner_os/web/core.js';
import {tasksUI} from '../../src/greenhouse_owner_os/web/tasks.js';

const decode=value=>String(value).replaceAll('&quot;','"').replaceAll('&#39;',"'").replaceAll('&lt;','<').replaceAll('&gt;','>').replaceAll('&amp;','&');
const task=()=>({id:'task-1',title:'Проверить полив',original_instruction:'Проверить полив в теплице.',description:'Проверить полив в теплице.',owner_language:'ru',status:'draft',planned_date:'2026-10-08',due_at:'2026-10-08T22:30:00Z',assignments:[],reports:[],actions:[],links:[],interpretation:{},version:1});
const worker=(id='dari',language='fa',active=true)=>({id,display_name:`TEST ${id}`,language,active});
function harness({workers=[worker()],record=task(),handler}={}){
 const originalDocument=globalThis.document,nodes=new Map(),requests=[];let revision=0,html='',reloads=0;
 const data={owner:{owner_id:'owner'},tasks:{tasks:[record],workers,areas:[{id:'g1',kind:'greenhouse',name:'Теплица 1'},{id:'g2',kind:'greenhouse',name:'Теплица 2'},{id:'p1',parent_id:'g1',name:'Часть 1'},{id:'p2',parent_id:'g2',name:'Часть 2'}],rules:[],attention:[]}};
 function node(id='',tag='div',attrs=''){
  return {id,tag,value:decode(attrs.match(/\bvalue="([^"]*)"/)?.[1]||''),disabled:/\bdisabled\b/.test(attrs),open:false,textContent:'',type:attrs.match(/\btype="([^"]*)"/)?.[1]||'text',_html:'',_children:[],
   get innerHTML(){return this._html;},set innerHTML(value){for(const id of this._children)nodes.delete('#'+id);this._html=value;this._children=parse(value);if(this.tag==='select')this.value=selectedValue(value);},
   querySelector(selector){return selector==='button.primary'?this.primary:nodes.get(selector)||null;},
   querySelectorAll(){return [...nodes.values()].filter(n=>['input','textarea','select','button'].includes(n.tag)).concat(this.primary?[this.primary]:[]);},
   close(){if(this.open){this.open=false;revision++;}},focus(){this.focused=true;},
  };
 }
 function selectedValue(body){const options=[...body.matchAll(/<option\b([^>]*)>/g)];return decode((options.find(m=>/\bselected\b/.test(m[1]))||options[0])?.[1].match(/\bvalue="([^"]*)"/)?.[1]||'');}
 function parse(body){
  const ids=[];
  for(const match of body.matchAll(/<(\w+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)){
   const [,tag,attrs,id]=match,n=node(id,tag,attrs),tail=body.slice(match.index+match[0].length),end=tail.indexOf(`</${tag}>`),inside=end<0?'':tail.slice(0,end);
   n.textContent=decode(inside.replace(/<[^>]*>/g,''));if(tag==='select')n.value=selectedValue(inside);if(tag==='textarea')n.value=decode(inside);
   if(tag==='form')n.primary=node('','button',inside.match(/<button[^>]*class="primary"[^>]*>/)?.[0]||'');
   nodes.set('#'+id,n);ids.push(id);
  }
  return ids;
 }
 const sheet=node('sheet'),content=node('sheet-content');nodes.set('#sheet',sheet);nodes.set('#sheet-content',content);
 function openSheet(title,body){revision++;html=title+'\n'+body;for(const key of nodes.keys())if(!['#sheet','#sheet-content'].includes(key))nodes.delete(key);parse(body);content.firstElementChild={};sheet.open=true;}
 globalThis.document={querySelector:s=>nodes.get(s)||null,querySelectorAll:()=>[]};
 const api=async(path,body)=>{requests.push({path,body});if(handler)return handler(path,body,record);return body?{id:record.id}:structuredClone(record);};
 const ui=tasksUI({api,openSheet,getSheetRevision:()=>revision,reload:async()=>{reloads++;},getData:()=>data,getEntity:()=>'entity',notice:message=>assert.fail(message)});
 const submit=selector=>{const form=nodes.get(selector);return form.onsubmit({preventDefault(){},target:form});};
 return {ui,nodes,requests,data,sheet,openSheet,submit,navigate:()=>{revision++;sheet.close();},html:()=>html+'\n'+(nodes.get('#assign-preview')?.innerHTML||''),reloads:()=>reloads,cleanup:()=>{globalThis.document=originalDocument;}};
}
async function assignment(h){await h.ui.read('task-1');h.nodes.get('#task-assign').onclick();}

test('Russian dates are fixed to the operating timezone and date-only facts never shift',()=>{
 const previous=process.env.TZ;
 try{for(const zone of ['America/Los_Angeles','Asia/Tokyo','UTC']){process.env.TZ=zone;assert.match(displayDate('2026-10-08'),/^8 окт\. 2026/);assert.match(displayDate('2026-10-08T22:30:00Z'),/^9 окт\. 2026/);assert.match(displayDateTime('2026-10-08T22:30:00Z'),/9 окт\. 2026.*01:30/);assert.match(displayDateTime('2026-10-08T09:45'),/8 окт\. 2026.*09:45/);assert.equal(displayDateTime('2026-10-08'),displayDate('2026-10-08'));}}
 finally{if(previous===undefined)delete process.env.TZ;else process.env.TZ=previous;}
 for(const value of [null,undefined,'','not a date','2026-02-30']){assert.equal(displayDate(value),'Не указана');assert.equal(displayDateTime(value,'—'),'—');}
});

test('task common path is intent and verified greenhouse, with every optional backend field retained',()=>{
 const h=harness();try{
  h.ui.edit({description:'Проверить <полив>',greenhouse_id:'g1',work_area_id:'p1',category:'irrigation',planned_date:'2026-10-08',due_at:'2026-10-08T22:30:00Z',priority:'high'});
  const html=h.html(),advanced=html.indexOf('<details class="module-disclosure task-advanced">');
  assert.ok(html.indexOf('id="task-description"')<advanced);assert.ok(html.indexOf('id="task-greenhouse"')<advanced);assert.match(html,/Проверить &lt;полив&gt;/);
  for(const id of ['task-title','task-category','task-area','task-planned','task-due','task-priority'])assert.ok(html.indexOf(`id="${id}"`)>advanced);
  assert.match(html,/<summary class="date-chip" id="task-planned-summary">Плановая дата · 8 окт\. 2026/);
  assert.equal(h.nodes.get('#task-due').value,'2026-10-09T01:30');assert.match(html,/Часть 1/);assert.doesNotMatch(html,/Часть 2/);
  assert.match(h.ui.render(),/8 окт\. 2026/);assert.doesNotMatch(h.ui.render(),/>2026-10-08</);
 }finally{h.cleanup();}
});

test('common task submit derives only its title and preserves instruction and optional defaults',async()=>{
 const h=harness();try{h.ui.edit();const instruction='  Проверить полив\nНе менять настройки.  ';h.nodes.get('#task-description').value=instruction;await h.submit('#task-form');
  const body=h.requests.find(r=>r.body)?.body;assert.equal(body.title,'Проверить полив');assert.equal(body.description,instruction);assert.equal(body.original_instruction,instruction);
  assert.equal(body.greenhouse_id,null);assert.equal(body.work_area_id,null);assert.equal(body.planned_date,null);assert.equal(body.due_at,null);assert.equal(body.category,'general');assert.equal(body.priority,'normal');assert.equal(body.source,'test:owner-tasks');
 }finally{h.cleanup();}
});

test('task optional fields retain date semantics and reject a part from another greenhouse',async()=>{
 const h=harness();try{h.ui.edit({description:'Полив',title:'Мой заголовок',greenhouse_id:'g1',work_area_id:'p1',planned_date:'2026-10-08',due_at:'2026-10-08T12:00:00Z',priority:'urgent',category:'irrigation'});h.nodes.get('#task-area').value='p2';await h.submit('#task-form');assert.equal(h.requests.length,0);assert.match(h.nodes.get('#task-error').textContent,/Часть должна/);h.nodes.get('#task-area').value='p1';await h.submit('#task-form');const body=h.requests.find(r=>r.body).body;assert.equal(body.title,'Мой заголовок');assert.equal(body.planned_date,'2026-10-08');assert.equal(body.due_at,'2026-10-08T15:00:00+03:00');assert.equal(body.priority,'urgent');assert.equal(body.category,'irrigation');assert.equal(body.work_area_id,'p1');}finally{h.cleanup();}
});

test('exact confirmed prepared assignment is shown beside original and one primary action records explicit review',async()=>{
 const record=task();record.prepared_assignment={worker_id:'dari',language:'fa',rendered_text:'آبیاری گلخانه را بررسی کنید. <TEST>'};const h=harness({record,workers:[worker('russian','ru'),worker()]});
 try{await assignment(h);assert.equal(h.nodes.get('#assign-worker').value,'dari');assert.equal(h.nodes.get('#task-assign-form').primary.disabled,false);assert.match(h.html(),/آبیاری گلخانه را بررسی کنید\. &lt;TEST&gt;/);assert.match(h.html(),/Оригинал · Русский/);assert.match(h.html(),/Нажимая «Назначить», вы подтверждаете/);assert.doesNotMatch(h.html(),/assign-reviewed|type="checkbox"/);
  await h.submit('#task-assign-form');assert.deepEqual(h.requests.find(r=>r.path.endsWith('/assignments')).body,{worker_id:'dari',language:'fa',rendered_text:record.prepared_assignment.rendered_text,translation_source:'owner_reviewed_translation',translation_version:'owner-reviewed-r1',rendering_reviewed:true});
 }finally{h.cleanup();}
});

test('missing, mismatched or Russian original text never masquerades as a Dari translation',async()=>{
 for(const prepared of [undefined,{worker_id:'other',language:'fa',rendered_text:'ترجمه'},{worker_id:'dari',language:'tr',rendered_text:'Türkçe metin'},{worker_id:'dari',language:'fa',rendered_text:task().original_instruction},{worker_id:'dari',language:'fa',rendered_text:task().original_instruction+' '},{worker_id:'dari',language:'fa',rendered_text:'  '+task().original_instruction.toUpperCase().replaceAll(' ','\n ')+'  '},{worker_id:'dari',language:'fa',rendered_text:' '}]){
  const record=task();record.prepared_assignment=prepared;const h=harness({record});try{await assignment(h);assert.equal(h.nodes.get('#task-assign-form').primary.disabled,true);assert.equal(h.nodes.get('#assign-text').value,'');assert.match(h.html(),/нет подготовленного перевода/);assert.match(h.html(),/Автоматический перевод не подключён/);assert.doesNotMatch(h.html(),/id="assign-prepared-text"/);await h.submit('#task-assign-form');assert.equal(h.requests.filter(r=>r.body).length,0);}finally{h.cleanup();}
 }
});

test('manual translation stays optional, explicit and bound to the selected worker language',async()=>{
 const h=harness();try{await assignment(h);const text=h.nodes.get('#assign-text');text.value=task().original_instruction;text.oninput({target:text});assert.equal(h.nodes.get('#task-assign-form').primary.disabled,true);text.value='متن بررسی شده';text.oninput({target:text});assert.equal(h.nodes.get('#task-assign-form').primary.disabled,false);await h.submit('#task-assign-form');const body=h.requests.find(r=>r.body).body;assert.equal(body.rendered_text,'متن بررسی شده');assert.equal(body.language,'fa');assert.equal(body.translation_source,'owner_reviewed_translation');assert.equal(body.rendering_reviewed,true);}finally{h.cleanup();}
});

test('explicit original fallback is labelled and submitted as Russian, never as worker language',async()=>{
 const h=harness();try{await assignment(h);h.nodes.get('#assign-use-original').onclick();assert.equal(h.nodes.get('#task-assign-form').primary.focused,true,'Removed fallback button hands focus to the unchanged assignment action');assert.match(h.html(),/Будет назначен оригинал · Русский/);assert.match(h.html(),/Это не перевод на язык работника/);await h.submit('#task-assign-form');const body=h.requests.find(r=>r.body).body;assert.equal(body.language,'ru');assert.equal(body.rendered_text,task().original_instruction);assert.equal(body.translation_source,'original');}finally{h.cleanup();}
});

test('worker changes invalidate prepared and manual text, and inactive workers never auto-select',async()=>{
 const record=task();record.prepared_assignment={worker_id:'dari',language:'fa',rendered_text:'ترجمه'};const h=harness({record,workers:[worker(),worker('turkish','tr'),worker('inactive','fa',false)]});
 try{await assignment(h);const select=h.nodes.get('#assign-worker');select.value='turkish';select.onchange();assert.equal(h.nodes.get('#task-assign-form').primary.disabled,true);assert.doesNotMatch(h.nodes.get('#assign-preview').innerHTML,/ترجمه/);assert.equal(h.nodes.get('#assign-text').value,'');select.value='dari';select.onchange();assert.equal(h.nodes.get('#task-assign-form').primary.disabled,false);assert.doesNotMatch(h.html(),/value="inactive"/);}finally{h.cleanup();}
 const absent=harness({record,workers:[worker('dari','fa',false),worker('one','tr'),worker('two','uz')]});try{await assignment(absent);assert.equal(absent.nodes.get('#assign-worker').value,'');assert.equal(absent.nodes.get('#task-assign-form').primary.disabled,true);}finally{absent.cleanup();}
});

test('Russian worker common path uses exact original without redundant translation fields',async()=>{
 const h=harness({workers:[worker('russian','ru')]});try{await assignment(h);assert.equal(h.nodes.get('#assign-worker').value,'russian');assert.doesNotMatch(h.html(),/id="assign-text"|Автоматический перевод/);await h.submit('#task-assign-form');const body=h.requests.find(r=>r.body).body;assert.equal(body.language,'ru');assert.equal(body.translation_source,'original');assert.equal(body.rendered_text,task().original_instruction);}finally{h.cleanup();}
});

test('late task reads cannot replace newer sheets, dismissed sheets or a changed owner',async()=>{
 for(const interrupt of [h=>h.ui.edit(),h=>h.navigate(),h=>h.openSheet('Другое окно',''),h=>{h.data.owner.owner_id='other';}]){
  let resolve;const h=harness({handler:()=>new Promise(r=>{resolve=r;})});try{const pending=h.ui.read('task-1');interrupt(h);const after=h.html();resolve(task());await pending;assert.equal(h.html(),after);}finally{h.cleanup();}
 }
});

test('submitting freezes worker choice, rejects repeated submits and ignores late completion after dismissal',async()=>{
 let resolve;const record=task();record.prepared_assignment={worker_id:'dari',language:'fa',rendered_text:'ترجمه'};const h=harness({record,handler:(_path,body)=>body?new Promise(r=>{resolve=r;}):structuredClone(record)});
 try{await assignment(h);const pending=h.submit('#task-assign-form');assert.equal(h.nodes.get('#assign-worker').disabled,true);await h.submit('#task-assign-form');assert.equal(h.requests.filter(r=>r.body).length,1);h.sheet.close();resolve({id:'assigned'});await pending;assert.equal(h.sheet.open,false);assert.equal(h.reloads(),0);assert.equal(h.requests.filter(r=>!r.body).length,1);}finally{h.cleanup();}
});

test('late assignment errors cannot leak into a different form',async()=>{
 let reject;const h=harness({workers:[worker('russian','ru')],handler:(_path,body,record)=>body?new Promise((_r,j)=>{reject=j;}):structuredClone(record)});
 try{await assignment(h);const pending=h.submit('#task-assign-form');h.ui.edit();reject(Error('OLD PRIVATE ERROR'));await pending;assert.equal(h.nodes.get('#task-error').textContent,'');assert.doesNotMatch(h.html(),/OLD PRIVATE ERROR/);}finally{h.cleanup();}
});


test('editing prepared text updates the visible preview and provenance through collapse and submission',async()=>{
 const record=task();record.prepared_assignment={worker_id:'dari',language:'fa',rendered_text:'PREPARED T1'};const h=harness({record});
 try{await assignment(h);const text=h.nodes.get('#assign-text');text.value='REVIEWED T2 <exact>';text.oninput({target:text});h.nodes.get('#assign-manual').open=false;
  assert.equal(h.nodes.get('#assign-text'),text,'Editing does not replace the focused textarea');assert.equal(h.nodes.get('#assign-prepared-text').textContent,text.value);assert.match(h.nodes.get('#assign-text-provenance').textContent,/Изменён вручную/);assert.doesNotMatch(h.nodes.get('#assign-rendering-preview').innerHTML,/PREPARED T1|Из подтверждённого/);
  await h.submit('#task-assign-form');assert.equal(h.requests.find(r=>r.body).body.rendered_text,'REVIEWED T2 <exact>');
 }finally{h.cleanup();}
});

test('clearing a prepared translation removes its visible preview and disables assignment',async()=>{
 const record=task();record.prepared_assignment={worker_id:'dari',language:'fa',rendered_text:'PREPARED T1'};const h=harness({record});
 try{await assignment(h);const text=h.nodes.get('#assign-text');text.value='';text.oninput({target:text});assert.equal(h.nodes.get('#task-assign-form').primary.disabled,true);assert.doesNotMatch(h.nodes.get('#assign-rendering-preview').innerHTML,/PREPARED T1|Из подтверждённого/);await h.submit('#task-assign-form');assert.equal(h.requests.filter(r=>r.body).length,0);}finally{h.cleanup();}
});


test('native close while already closed is inert, navigation revision still fences a delayed read',async()=>{
 let resolve;const h=harness({handler:()=>new Promise(r=>{resolve=r;})});
 try{assert.equal(h.sheet.open,false);const pending=h.ui.read('task-1');h.sheet.close();h.navigate();resolve(task());await pending;assert.equal(h.sheet.open,false);assert.equal(h.html().trim(),'');}finally{h.cleanup();}
});


test('manual whitespace or case edits of Russian original cannot be labelled Dari',async()=>{
 for(const original of [task().original_instruction+' ',task().original_instruction.toUpperCase().replaceAll(' ','\n')]){
  const h=harness();try{await assignment(h);const field=h.nodes.get('#assign-text');field.value=original;field.oninput({target:field});assert.equal(h.nodes.get('#task-assign-form').primary.disabled,true);assert.match(h.nodes.get('#assign-rendering-preview').innerHTML,/Это оригинал/);await h.submit('#task-assign-form');assert.equal(h.requests.filter(r=>r.body).length,0);}finally{h.cleanup();}
 }
});

test('ordinary task detail prioritizes instruction, place, time and assignment over technical controls',async()=>{
 const record={...task(),category:'irrigation',greenhouse_id:'g1',planned_date:null,due_at:null,relative_time_context:'следующая неделя',original_instruction:'Нужно переделать полив на Озвади на следующей неделе',description:'Нужно переделать полив на Озвади на следующей неделе'};
 const h=harness({record});try{await h.ui.read(record.id);const html=h.html(),extra=html.indexOf('id="task-details-extra"'),primary=html.slice(0,extra);
  assert.ok(extra>0,'Task detail has an accessible additional section');assert.match(html,/<details class="module-disclosure task-advanced" id="task-details-extra"><summary>Дополнительно<\/summary>/);
  for(const text of ['Что сделать','Где','Когда','Кому','Полив','Теплица 1','следующая неделя','Работник не назначен','id="task-assign"'])assert.ok(primary.includes(text),text);
  for(const text of ['id="task-repeat"','id="task-link"','<h3>История</h3>','Отчётов нет'])assert.ok(html.indexOf(text)>extra,text);
  assert.doesNotMatch(primary,/Связать фактическое событие|цикл|Задача сама не проводит событие/);
  assert.equal(h.nodes.get('#task-link').onclick instanceof Function,true);assert.equal(h.nodes.get('#task-repeat').onclick instanceof Function,true);
 }finally{h.cleanup();}
});

test('reported task keeps actionable report and completion guidance outside additional details',async()=>{
 const record={...task(),status:'reported',can_complete:false,reports:[{id:'report',original_text:'Не удалось закончить',original_language:'ru',state:'submitted',normalized:{},attachments:[]} ]};
 const h=harness({record});try{await h.ui.read(record.id);const primary=h.html().split('id="task-details-extra"')[0];assert.match(primary,/data-review-report="report"/);assert.match(primary,/Для завершения нужен текущий отчёт/);}finally{h.cleanup();}
});

test('assignment review retains place and source timing next to original before worker and translation',async()=>{
 const record={...task(),greenhouse_id:'g1',relative_time_context:'следующая неделя'};record.prepared_assignment={worker_id:'dari',language:'fa',rendered_text:'متن بررسی شده'};
 const h=harness({record});try{await assignment(h);const html=h.html();assert.match(html,/Теплица 1/);assert.match(html,/8 окт\. 2026/);assert.match(html,/В источнике: следующая неделя/);assert.ok(html.indexOf('Теплица 1')<html.indexOf('id="assign-worker"'));assert.ok(html.indexOf('id="assign-worker"')<html.indexOf('id="assign-preview"'));}finally{h.cleanup();}
});

test('confirmed preparation category stays labelled as a proposal without rewriting the R1 category',async()=>{
 const record={...task(),category:'general',prepared_category:'irrigation',relative_time_context:'следующая неделя'};const before=JSON.stringify(record);
 const h=harness({record});try{await h.ui.read(record.id);const primary=h.html().split('id="task-details-extra"')[0];assert.match(primary,/<dt>Категория · предложено<\/dt><dd><strong>Полив<\/strong>/);assert.equal(JSON.stringify(record),before);assert.equal(record.category,'general');}finally{h.cleanup();}
});
