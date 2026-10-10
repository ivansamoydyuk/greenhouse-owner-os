import {escape as e,displayDate,displayDateTime} from './core.js';
import {categoryNames,taskWhenLabel} from './tasks.js';

const pageSize=20;
const states={TO_DO:'Активные',DONE:'Завершённые'};
const actionLabel=task=>task.state==='DONE'?'Вернуть в активные':'Отметить готовой';
const stateLabel=state=>state==='DONE'?'Завершена':'Активна';
const greenhouse=(task,data)=>task.greenhouse_id
  ?data?.work_areas?.find(area=>area.id===task.greenhouse_id)?.name||'Название теплицы недоступно'
  :'Не указана';

export function personalTaskRows(items,data){
  return items.map(task=>`<button type="button" class="item" data-personal-open="${e(task.id)}"><span class="item-head"><strong>${e(task.title)}</strong><span class="badge">${stateLabel(task.state)}</span></span><small>${e(taskWhenLabel(task,'Без даты'))}</small>${task.greenhouse_id?`<small>${e(greenhouse(task,data))}</small>`:''}${task.category?`<small>${e(categoryNames[task.category]||task.category)}</small>`:''}</button>`).join('');
}

export function personalTaskDetail(task,data){
  return `<article id="personal-task-detail" data-task-id="${e(task.id)}"><span class="badge">${stateLabel(task.state)}</span><h3>${e(task.title)}</h3><div class="owner-review-facts"><p><strong>Когда:</strong> ${e(displayDate(task.planned_date,'Без даты'))}</p>${task.relative_time_context?`<p><strong>Срок из источника:</strong> ${e(task.relative_time_context)}</p>`:''}<p><strong>Теплица:</strong> ${e(greenhouse(task,data))}</p>${task.category?`<p><strong>Категория:</strong> ${e(categoryNames[task.category]||task.category)}</p>`:''}</div>${task.note?`<section class="card"><h3>Заметка</h3><p>${e(task.note)}</p></section>`:''}<section class="card"><h3>Исходная формулировка</h3>${task.original_instruction?`<blockquote dir="auto">${e(task.original_instruction)}</blockquote>`:'<p class="note">Исходная формулировка не указана в подтверждённой записи.</p>'}</section><div id="personal-task-error" class="form-error" role="alert"></div><div class="actions"><button type="button" class="primary" id="personal-task-change">${actionLabel(task)}</button><button type="button" id="personal-task-refresh">Обновить задачу</button></div><details class="module-disclosure"><summary>История состояния · ${e(task.actions.length)}</summary>${task.actions.map(action=>`<div class="row"><span>${stateLabel(action.state)}<small>Версия ${e(action.version)}</small></span><small>${e(displayDateTime(action.created_at))}</small></div>`).join('')||'<p class="note">История состояния отсутствует.</p>'}</details></article>`;
}

function validTask(task,scope){
  return task&&typeof task.id==='string'&&task.id&&typeof task.title==='string'
    &&task.entity_id===scope.entity&&task.owner_id===scope.owner
    &&Object.hasOwn(states,task.state)&&Number.isSafeInteger(task.version)&&task.version>0;
}

export function personalTasksUI({api,openSheet,reload,getData,getEntity,notice,getSheetRevision,
  isPageActive=()=>true,getAuthGeneration=()=>0,getStorage=()=>window.sessionStorage}){
  const $=selector=>document.querySelector(selector);
  let knownScope=null,filter='TO_DO',offset=0,generation=0,viewGeneration=0,detailGeneration=0,focusAfterWrite=null;
  const scope=()=>getEntity()&&getData()?.owner?.owner_id?{entity:String(getEntity()),owner:String(getData().owner.owner_id)}:null;
  const scopeKey=value=>value?`${value.entity}:${value.owner}`:null;
  const storageKey=value=>'owner-personal-task-view:'+scopeKey(value);
  function persist(){try{if(knownScope)getStorage()?.setItem(storageKey(knownScope),JSON.stringify({filter,offset}));}catch{/* View preferences must not block server-backed tasks. */}}
  function prepare(){
    const next=scope();
    if(scopeKey(next)===scopeKey(knownScope))return next;
    knownScope=next;filter='TO_DO';offset=0;invalidateView();
    try{
      const stored=next?JSON.parse(getStorage()?.getItem(storageKey(next))||'null'):null;
      if(stored&&Object.hasOwn(states,stored.filter)&&Number.isSafeInteger(stored.offset)&&stored.offset>=0){filter=stored.filter;offset=stored.offset;}
    }catch{/* Ignore malformed local preferences, never task facts. */}
    return next;
  }
  const sessionCurrent=t=>scopeKey(t.scope)===scopeKey(scope())&&t.auth===getAuthGeneration()&&isPageActive();
  const current=t=>sessionCurrent(t)&&t.generation===generation&&t.view===viewGeneration&&t.panel===$('#personal-task-panel');
  const sheetCurrent=t=>sessionCurrent(t)&&t.view===viewGeneration&&t.detail===detailGeneration
    &&t.revision===getSheetRevision()&&!!$('#sheet')?.open;
  function invalidateView(){generation++;viewGeneration++;detailGeneration++;focusAfterWrite=null;}
  function logout(){invalidateView();knownScope=null;filter='TO_DO';offset=0;}
  function filters(counts){return `<div class="section-tabs" aria-label="Состояние личных задач"><button type="button" id="personal-active" aria-pressed="${filter==='TO_DO'}" class="${filter==='TO_DO'?'active':''}">Активные${counts?` · ${e(counts.TO_DO)}`:''}</button><button type="button" id="personal-completed" aria-pressed="${filter==='DONE'}" class="${filter==='DONE'?'active':''}">Завершённые${counts?` · ${e(counts.DONE)}`:''}</button></div>`;}
  function panel(mode,response,error){
    const tabs=filters(mode==='ready'?response.counts:null);
    if(mode==='loading')return tabs+'<p id="personal-task-status" role="status">Загружаем личные задачи…</p>';
    if(mode==='error')return tabs+`<section class="card"><h2>Задачи не загружены</h2><p class="form-error" role="alert">${e(error.message)}</p><p class="note">Список пока недоступен. Сохранённые задачи остаются в системе.</p><button type="button" id="personal-list-retry">Повторить загрузку</button></section>`;
    return tabs+`<p id="personal-task-status" role="status" tabindex="-1">${response.total?`Показано ${e(offset+1)}–${e(offset+response.items.length)} из ${e(response.total)}`:filter==='DONE'?'Завершённых личных задач пока нет':'Активных личных задач нет'}</p><div class="module-list">${personalTaskRows(response.items,getData())}</div><div class="actions">${offset>0?'<button type="button" id="personal-prev">← Предыдущие</button>':''}${response.next_offset!==null?'<button type="button" id="personal-next">Следующие →</button>':''}</div>`;
  }
  function render(){
    prepare();
    return `<div class="topline"><div><p class="eyebrow">${e(getData()?.entity_name||'Задачи владельца')}</p><h1>Личные задачи</h1><p class="page-description">Подтверждённые дела и история выполнения.</p></div></div><section id="personal-task-panel" aria-label="Личные задачи">${panel('loading')}</section>`;
  }
  function wire(response){
    $('#personal-active').onclick=()=>load('TO_DO',0,'#personal-active');
    $('#personal-completed').onclick=()=>load('DONE',0,'#personal-completed');
    if($('#personal-list-retry'))$('#personal-list-retry').onclick=()=>load(filter,offset,'#personal-task-status');
    if(!response)return;
    $('#personal-task-panel').querySelectorAll('[data-personal-open]').forEach(button=>button.onclick=()=>read(button.dataset.personalOpen));
    if($('#personal-prev'))$('#personal-prev').onclick=()=>load(filter,Math.max(0,offset-pageSize),'#personal-task-status');
    if($('#personal-next'))$('#personal-next').onclick=()=>load(filter,response.next_offset,'#personal-task-status');
  }
  async function load(state=filter,start=offset,focusSelector=null){
    const owner=prepare(),node=$('#personal-task-panel');if(!owner||!node||!isPageActive())return;
    filter=state;offset=start;persist();
    const t={scope:owner,auth:getAuthGeneration(),generation:++generation,view:viewGeneration,panel:node};
    node.innerHTML=panel('loading');node.setAttribute('aria-busy','true');wire();
    try{
      const response=await api(`/v1/entities/${encodeURIComponent(owner.entity)}/owner/tasks?state=${state}&offset=${start}&limit=${pageSize}`,undefined,{isCurrent:()=>current(t)});
      if(!current(t))return;
      if(!response||response.state!==state||response.offset!==start||response.limit!==pageSize
        ||!Array.isArray(response.items)||!Number.isSafeInteger(response.total)||response.total<0
        ||!response.counts||!Number.isSafeInteger(response.counts.TO_DO)||response.counts.TO_DO<0
        ||!Number.isSafeInteger(response.counts.DONE)||response.counts.DONE<0
        ||response.total!==response.counts[state]||response.items.length>pageSize
        ||!response.items.every(task=>validTask(task,owner)&&task.state===state)
        ||new Set(response.items.map(task=>task.id)).size!==response.items.length
        ||!(response.next_offset===null||(Number.isSafeInteger(response.next_offset)&&response.next_offset===start+pageSize&&response.next_offset<response.total)))throw Error('Не удалось проверить ответ со списком задач. Обновите данные.');
      // A mutation in another tab may have emptied the last offset page.
      if(start>0&&response.items.length===0&&response.total>0)return load(state,0,focusSelector);
      if(response.items.length!==Math.min(pageSize,Math.max(0,response.total-start))
        ||response.next_offset!==(start+pageSize<response.total?start+pageSize:null))throw Error('Список задач получен не полностью. Повторите загрузку.');
      node.innerHTML=panel('ready',response);wire(response);
      if(focusSelector)$(focusSelector)?.focus({preventScroll:true});
      return response;
    }catch(error){if(current(t)){node.innerHTML=panel('error',null,error);wire();if(focusSelector)$('#personal-list-retry')?.focus({preventScroll:true});}}
    finally{if(current(t))node.setAttribute('aria-busy','false');}
  }
  function bind(){
    const focus=focusAfterWrite&&sessionCurrent(focusAfterWrite)?'#personal-task-status':null;
    invalidateView();
    // Refreshing the page invalidates any old detail/version still on screen.
    if($('#personal-task-detail')&&$('#sheet')?.open)$('#sheet').close();
    return load(filter,offset,focus);
  }
  async function read(id){
    const owner=prepare();if(!owner||!isPageActive())return;
    const t={scope:owner,auth:getAuthGeneration(),view:viewGeneration,detail:++detailGeneration};
    openSheet('Личная задача','<p role="status">Загружаем задачу…</p>');t.revision=getSheetRevision();
    try{
      const task=await api(`/v1/entities/${encodeURIComponent(owner.entity)}/owner/tasks/${encodeURIComponent(id)}`,undefined,{isCurrent:()=>sheetCurrent(t)});
      if(!sheetCurrent(t))return;
      if(!validTask(task,owner)||task.id!==id||!Array.isArray(task.actions)
        ||!task.actions.every(action=>Object.hasOwn(states,action.state)&&Number.isSafeInteger(action.version)&&action.version>0))throw Error('Не удалось проверить ответ с задачей. Обновите данные.');
      openSheet('Личная задача',personalTaskDetail(task,getData()));t.revision=getSheetRevision();
      $('#personal-task-change').onclick=()=>changeState(task,t);
      $('#personal-task-refresh').onclick=()=>read(id);
    }catch(error){
      if(!sheetCurrent(t))return;
      openSheet('Личная задача',`<p class="form-error" role="alert">${e(error.message)}</p><button type="button" id="personal-detail-retry">Повторить загрузку</button>`);t.revision=getSheetRevision();
      $('#personal-detail-retry').onclick=()=>{if(sheetCurrent(t))return read(id);};
    }
  }
  async function changeState(task,t){
    if(!sheetCurrent(t)||t.writing)return;
    const target=task.state==='DONE'?'TO_DO':'DONE';t.writing=true;
    const change=$('#personal-task-change'),refresh=$('#personal-task-refresh'),errorNode=$('#personal-task-error');
    change.disabled=true;refresh.disabled=true;errorNode.textContent='';
    try{
      const result=await api(`/v1/entities/${encodeURIComponent(t.scope.entity)}/owner/tasks/${encodeURIComponent(task.id)}/state`,{expected_version:task.version,state:target},{isCurrent:()=>sheetCurrent(t)});
      if(!sheetCurrent(t))return;
      if(!validTask(result,t.scope)||result.id!==task.id||result.state!==target||result.version!==task.version+1)throw Error('Ответ о действии не подтверждён. Обновите задачу и проверьте состояние.');
      $('#sheet').close();offset=0;persist();focusAfterWrite={scope:t.scope,auth:t.auth};
      await reload();
      // An idempotent replay acknowledges the historical action; another client
      // may already have changed the task again. The refreshed list is current.
      if(sessionCurrent(t))notice('Действие сохранено в истории.');
    }catch(error){
      if(!sheetCurrent(t))return;
      const stale=error.status===409,denied=[400,403,404,422].includes(error.status);
      errorNode.textContent=stale?'Задача изменилась. Обновите её перед новым действием.':error.message;
      change.disabled=stale||denied;refresh.disabled=false;
      if(!change.disabled)change.textContent='Повторить: '+actionLabel(task).toLocaleLowerCase('ru');
    }finally{t.writing=false;}
  }
  return {render,bind,load,read,invalidateView,logout,getFilter:()=>filter};
}
