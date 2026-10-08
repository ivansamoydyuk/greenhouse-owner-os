import {escape as e,money,errorMessage} from './core.js';
import {lineAmount} from './harvest.js';

export function receiptTotal(lines){const cents=lines.reduce((sum,l)=>sum+BigInt(lineAmount(l.net_weight_kg,l.unit_price).replace('.','')),0n);return `${cents/100n}.${String(cents%100n).padStart(2,'0')}`;}

const titles={PAYABLE:'Долг',PAYMENT:'Оплата',DIRECT_EXPENSE:'Расход',STOCK_RECEIPT:'Товар привезли',STOCK_APPLICATION:'Фактическое внесение',DEPOT_RECEIPT:'Чек депо',PERSONAL_TASK:'Моя задача',WORKER_TASK:'Поручение работнику',INVOICE_MATCH:'Счёт к ранее принятому товару',UNRESOLVED:'Нужно уточнить событие'};
const statuses={PREPARING:'Готовим',NEEDS_INPUT:'Уточните',READY:'Готово к подтверждению',DEFERRED:'Отложено',POSTED:'Подтверждено',CONFIRMED_NONPOSTING:'Подтверждено · не отправлено',REJECTED:'Отклонено',STALE:'Данные изменились',FAILED:'Нужно повторить подготовку'};
const dbName='owner-source-pending-v1';
export async function pendingStore(){
  return new Promise((resolve,reject)=>{const request=indexedDB.open(dbName,1);request.onupgradeneeded=()=>request.result.createObjectStore('sources',{keyPath:'key'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(Error('Не удалось сохранить на телефоне. Оставьте окно открытым и повторите.'));});
}
export async function pendingWrite(value){const db=await pendingStore();return new Promise((resolve,reject)=>{const tx=db.transaction('sources','readwrite');tx.objectStore('sources').put(value);tx.oncomplete=()=>{db.close();resolve();};tx.onerror=tx.onabort=()=>{db.close();reject(Error('Память телефона недоступна или заполнена. Источник ещё не сохранён.'));};});}
async function pendingList(scope){const db=await pendingStore();return new Promise((resolve,reject)=>{const tx=db.transaction('sources');const r=tx.objectStore('sources').getAll();r.onsuccess=()=>resolve(r.result.filter(x=>x.scope===scope));r.onerror=()=>reject(Error('Очередь телефона недоступна'));tx.oncomplete=()=>db.close();});}
async function pendingDelete(key){const db=await pendingStore();return new Promise((resolve,reject)=>{const tx=db.transaction('sources','readwrite');tx.objectStore('sources').delete(key);tx.oncomplete=()=>{db.close();resolve();};tx.onerror=()=>reject(Error('Не удалось обновить очередь телефона'));});}
function fileData(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(Error('Не удалось прочитать файл'));r.readAsDataURL(file);});}

export function ownerR2({api,openSheet,reload,getData,getEntity,notice,advanced}){
  let syncing=false,sessionGeneration=0,pollTimer;
  const $=s=>document.querySelector(s),root=()=>`/v1/entities/${getEntity()}/owner`,home=()=>getData()?.owner;
  const scope=()=>home()?`${getEntity()}:${home().owner_id}`:null;
  const guard=fn=>async(...args)=>{try{await fn(...args);}catch(err){notice(err.message);}};
  const party=id=>home()?.finance.references.find(r=>r.id===id)?.original_name||'Не выбран';
  const input=(key,label,value='',type='text')=>`<label>${e(label)}<input name="${key}" type="${type}" value="${e(value??'')}"></label>`;
  const options=(list,value,empty='Не выбран')=>`<option value="">${empty}</option>${list.map(x=>`<option value="${e(x.id)}" ${x.id===value?'selected':''}>${e(x.original_name||x.name||x.display_name)}</option>`).join('')}`;
  const select=(key,label,list,value,empty)=>`<label>${e(label)}<select name="${key}">${options(list,value,empty)}</select></label>`;
  function mount(){
    let bar=$('#owner-capture-bar');if(!bar){bar=document.createElement('div');bar.id='owner-capture-bar';bar.className='owner-capture-bar';bar.setAttribute('aria-label','Записать событие');document.body.append(bar);}
    bar.hidden=!home();bar.innerHTML='<button data-r2-mode="voice_transcript">🎙 Голос</button><button data-r2-mode="photo">📷 Фото</button><button data-r2-mode="text">✎ Текст</button>';
    bar.querySelectorAll('button').forEach(b=>b.onclick=()=>capture(b.dataset.r2Mode));
    document.body.classList.toggle('has-owner-capture',!!home());
  }
  function render(){
    const h=home(),b=h.finance.balances.TRY||{},season=h.season.find(s=>s.currency==='TRY');
    return `<div class="topline"><div><p class="eyebrow">${e(h.finance.entity_name)}</p><h1>Моя теплица</h1><small>ТЕСТОВЫЕ ДАННЫЕ · ${e(h.finance.as_of_date)}</small></div></div>
      <div class="owner-home"><section class="hero owner-receivable"><p>Мне должны</p><div class="big">${money(b.receivable||'0','TRY')}</div><small>Депо и клиенты · подтверждённые долги</small></section>
      <section class="card owner-payable"><h2>Я должен</h2><strong>${money(h.finance.business_balances.TRY?.operational_payable||'0','TRY')}</strong></section>
      <button class="card owner-queue-link ${h.confirmations.overdue?'overdue':''}" id="owner-queue"><span>Неподтверждённые</span><strong>${h.confirmations.n}</strong><small>${h.confirmations.overdue?'Есть карточки старше 36 часов':'Каждое действие подтверждается отдельно'}</small></button>
      <section class="card"><h2>Урожай сезона</h2><small>${e(h.season_start)} → ${e(h.season_end)} · конец не включён</small>${season?`<div class="row"><span>Всего</span><strong>${e(season.kg)} кг</strong></div><div class="row"><span>Средняя ветка</span><strong>${season.average_bunch_weight?Number(season.average_bunch_weight).toFixed(2)+' кг':'Нет подтверждённого количества'}</strong></div><small>${season.count_coverage_complete?'Полное покрытие количества':'Количество известно не для всего веса'} · ${e(season.count_covered_kg||'0')} кг / ${e(season.count||'—')} веток</small><div class="row"><span>Средняя цена</span><strong>${Number(season.average_price).toFixed(2)} TRY/кг</strong></div>`:'<p class="empty">Подтверждённых продаж в этом сезоне пока нет.</p>'}${h.season.filter(s=>s.currency!=='TRY').map(s=>`<small>${e(s.currency)}: ${e(s.kg)} кг · учитывается отдельно</small>`).join('')}</section>
      <section class="card"><h2>Мои задачи · ${h.task_count}</h2>${h.tasks.map(t=>`<div class="owner-task"><span>${e(t.title)}<small>${e(t.planned_date||'Без даты')}</small></span><button data-owner-done="${e(t.id)}" data-version="${t.version}">Готово</button></div>`).join('')||'<p class="empty">Открытых личных задач нет</p>'}</section>
      <section class="card"><h2>Работники</h2>${h.worker_exceptions.map(t=>`<p class="bad">${e(t.title)} · проблема требует проверки</p>`).join('')||'<p class="empty">Нет известных текущих проблем</p>'}<small>Без каталога работников на главной</small></section>
      <section class="card"><h2>Источники на телефоне</h2><p id="owner-pending-state">Проверяем локальную очередь…</p><button id="owner-sync">Повторить отправку</button><small>iPhone может приостановить приложение. Повторная отправка выполняется при открытом приложении. Память браузера может быть очищена системой.</small></section>
      <button id="owner-advanced" class="secondary">Финансы — вручную</button></div>`;
  }
  function bind(){
    mount();$('#owner-queue')?.addEventListener('click',guard(()=>queue()));$('#owner-advanced')?.addEventListener('click',advanced);$('#owner-sync')?.addEventListener('click',guard(sync));
    document.querySelectorAll('[data-owner-done]').forEach(b=>b.onclick=guard(async()=>{b.disabled=true;try{await api(root()+`/tasks/${b.dataset.ownerDone}/state`,{expected_version:Number(b.dataset.version),state:'DONE'});await reload();}finally{b.disabled=false;}}));
    pendingBadge().catch(()=>{if($('#owner-pending-state'))$('#owner-pending-state').textContent='Локальная очередь недоступна; проверьте сохранение каждого источника.';});
  }
  async function pendingBadge(){const sc=scope();if(!sc)return;const list=await pendingList(sc);if($('#owner-pending-state'))$('#owner-pending-state').textContent=list.length?`На телефоне, ещё не в системе: ${list.length}`:'Неотправленных источников нет';}
  async function sync(){
    const sc=scope(),generation=sessionGeneration;if(!sc||syncing||!navigator.onLine)return;syncing=true;
    try{for(const record of await pendingList(sc)){
      if(scope()!==sc||sessionGeneration!==generation)return;
      const r=await api(root()+'/captures',record.body);
      if(!r.server_saved||r.client_capture_id!==record.body.client_capture_id)throw Error('Система не подтвердила сохранение источника');
      await pendingDelete(record.key);notice('Сохранено в системе. Действия ещё не подтверждены.');
    }await pendingBadge();}finally{syncing=false;}
  }
  function capture(mode='text'){
    if(!scope())return;
    openSheet('Что произошло?',`<p class="note">Сначала сохраним оригинал. Долги, деньги и остатки изменятся только после подтверждения карточки.</p><form id="r2-source-form"><label>Текст / расшифровка<textarea name="text" rows="4" maxlength="10000" placeholder="Заплатил Серкану 5 000"></textarea></label>${mode==='voice_transcript'?'<button type="button" id="r2-dictate">Диктовать</button><p class="note">Сохраняется только текст, не аудиозапись. На iPhone можно использовать микрофон клавиатуры.</p>':''}<label>Фото / файл<input id="r2-file" type="file" accept="image/jpeg,image/png,application/pdf" ${mode==='photo'?'capture="environment"':''}></label><small>JPEG/PNG/PDF до 20 MiB. HEIC сначала сохраните как JPEG. Оригинал не обрезается.</small><p id="r2-source-status" role="status"></p><button class="primary" type="submit">Сохранить источник</button></form>`);
    let recognition,saved=false;
    $('#r2-dictate')?.addEventListener('click',()=>{const Speech=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Speech){$('#r2-source-status').textContent='Диктовка браузера недоступна. Используйте микрофон клавиатуры или текст.';return;}recognition=new Speech();recognition.lang='ru-RU';recognition.onresult=ev=>{$('#r2-source-form textarea').value+=Array.from(ev.results).map(r=>r[0].transcript).join(' ');};recognition.onerror=()=>{$('#r2-source-status').textContent='Нет разрешения или диктовка недоступна. Введите текст.';};recognition.start();});
    $('#r2-source-form').onsubmit=async ev=>{
      ev.preventDefault();const button=ev.target.querySelector('[type=submit]');button.disabled=true;
      try{
        recognition?.stop();const text=ev.target.elements.text.value,file=$('#r2-file').files[0];if(!text.trim()&&!file)throw Error('Добавьте текст или файл');if(file&&file.size>20*1024*1024)throw Error('Файл больше 20 MiB; он ещё не сохранён');
        const body={client_capture_id:crypto.randomUUID(),captured_at:new Date().toISOString(),timezone:'Europe/Istanbul',mode,text:text.trim()?text:null};
        if(file){if(!['image/jpeg','image/png','application/pdf'].includes(file.type))throw Error('Поддерживаются JPEG, PNG и PDF');Object.assign(body,{filename:file.name,mime_type:file.type,file_base64:await fileData(file)});}
        const sc=scope();await pendingWrite({key:sc+':'+body.client_capture_id,scope:sc,body});$('#r2-source-status').textContent='Сохранено на телефоне; в системе ещё не сохранено.';
        saved=true;
        if(!navigator.onLine){$('#r2-source-status').textContent='Сохранено на телефоне. В системе ещё нет; отправим при восстановлении связи.';return;}
        try{await sync();}catch{$('#r2-source-status').textContent='Сохранено на телефоне. Отправим при восстановлении связи и открытом приложении.';return;}
        $('#sheet').close();await reload();pollTimer=setTimeout(()=>reload().catch(()=>{}),1500);
      }catch(err){$('#r2-source-status').textContent=err.message;}finally{button.disabled=saved;}
    };
  }
  function card(p){const f=p.revision.payload;return `<article class="card owner-proposal ${p.reminder_eligible?'overdue':''}" data-proposal="${e(p.id)}"><small>${titles[p.action_type]} · ${statuses[p.state]}</small><h2>${e(f.title||f.description||party(f.counterparty_id||f.depot_id)||'Событие')}</h2>${f.amount?`<strong class="amount">${money(f.amount,f.currency)}</strong>`:''}<p>${e(f.event_date||f.planned_date||'Дата пока не выбрана')}</p>${f.money_account_id?`<small>Счёт: ${e(party(f.money_account_id))}</small>`:''}${p.revision.missing.map(m=>`<p class="bad">${e(m)}</p>`).join('')}<button data-r2-open="${e(p.id)}">Проверить карточку</button></article>`;}
  async function queue(offset=0){const r=await api(root()+`/confirmations?offset=${offset}`);openSheet('Неподтверждённые',`<p class="note">Каждая карточка — отдельное действие. Сохранённый источник ещё не означает оплату, долг или приход.</p>${r.items.map(card).join('')||'<p class="empty">Все карточки разобраны</p>'}${r.next_offset!==null?`<button id="r2-next">Следующие карточки →</button>`:''}`);$('#r2-next')?.addEventListener('click',guard(()=>queue(r.next_offset)));document.querySelectorAll('[data-r2-open]').forEach(b=>b.onclick=guard(()=>review(b.dataset.r2Open)));}
  async function review(id){
    const p=await api(root()+`/confirmations/${id}/preview`),f=p.revision.payload,kind=p.action_type;
    let summary=`<p class="eyebrow">${titles[kind]} · ${statuses[p.state]}</p>${card(p)}`;
    if(kind==='DEPOT_RECEIPT')summary+=`<table><thead><tr><th>Качество оригинала</th><th>кг</th><th>Цена</th><th>Сумма</th></tr></thead><tbody>${f.lines.map(l=>`<tr><td>${e(l.original_quality_name)}</td><td>${e(l.net_weight_kg)}</td><td>${e(l.unit_price)}</td><td>${e(l.stated_amount||'—')}</td></tr>`).join('')}</tbody></table><p>Сумма по строкам: ${receiptTotal(f.lines)} ${f.currency}</p><p>Сумма источника: ${e(f.stated_total||'Не читается')} · Alacak: ${e(f.alacak_value||'—')} — не оплата.</p>`;
    openSheet('Проверить действие',summary+`<details><summary>Оригинальный источник</summary><p class="source-text">${e(p.source.text||p.source.filename||'Фото / файл')}</p>${p.source.has_file?`<a href="${root()}/captures/${p.source.id}/original" target="_blank" rel="noopener">Открыть оригинал</a>`:''}</details><div class="actions"><button id="r2-confirm" class="primary" ${p.revision.missing.length||!navigator.onLine?'disabled':''}>Подтвердить</button><button id="r2-edit">Исправить</button><button id="r2-defer">Позже</button><button id="r2-reject" class="danger">Отклонить</button></div><p id="r2-card-status" role="status"></p>`);
    $('#r2-confirm').onclick=guard(async()=>{const b=$('#r2-confirm');b.disabled=true;try{const r=await api(root()+`/confirmations/${id}/confirm`,{expected_version:p.version,preview_digest:p.revision.preview_digest,confirmed:true});if(r.stale){notice('Данные изменились. Нужна новая проверка.');await review(id);}else{$('#sheet').close();notice(r.state==='CONFIRMED_NONPOSTING'?'Подтверждено. Не отправлено работнику / стоимость не изменена.':'Действие подтверждено');await reload();}}finally{b.disabled=false;}});
    $('#r2-edit').onclick=()=>edit(p);for(const action of ['defer','reject'])$('#r2-'+action).onclick=guard(async()=>{await api(root()+`/confirmations/${id}/${action}`,{expected_version:p.version});await reload();await queue();});
  }
  function edit(p){
    const f=p.revision.payload,kind=p.action_type,refs=home().finance.references,parties=refs.filter(r=>r.kind==='counterparty'),accounts=refs.filter(r=>r.kind==='money_account'&&r.currency===(f.currency||'TRY')),areas=home().finance.work_areas,inventory=getData().inventory;
    let fields='';
    if(['PAYABLE','PAYMENT','DIRECT_EXPENSE'].includes(kind))fields=input('amount','Сумма',f.amount)+input('event_date','Дата',f.event_date,'date')+select('counterparty_id','Контрагент',parties,f.counterparty_id,'Не выбран')+'<button type="button" id="r2-party-new">+ Новый</button>'+input('description','За что',f.description)+(kind!=='PAYABLE'?select('money_account_id','Наличные / Банк',accounts,f.money_account_id):'')+(kind==='PAYMENT'?select('obligation_id','Погасить долг',home().finance.obligations.filter(o=>o.counterparty_id===f.counterparty_id&&o.direction==='payable'&&o.remaining_amount!=='0.00').map(o=>({id:o.obligation_id,name:`${o.description||'Долг'} · ${o.remaining_amount} ${o.currency}`})),f.obligation_id):'');
    else if(['PERSONAL_TASK','WORKER_TASK'].includes(kind))fields=input('title','Задача',f.title)+input('planned_date','Дата',f.planned_date,'date')+select('greenhouse_id','Теплица (необязательно для личной задачи)',areas,f.greenhouse_id)+(kind==='WORKER_TASK'?select('worker_id','Работник',getData().tasks.workers||[],f.worker_id)+input('rendered_text','Проверенный перевод',f.rendered_text):'');
    else if(['STOCK_RECEIPT','STOCK_APPLICATION'].includes(kind)){fields=input('event_date','Дата',f.event_date,'date');fields+=f.lines.map((l,i)=>`<fieldset><legend>${e(l.original_product_name)}</legend>${select(`product_${i}`,'Товар',inventory.products,l.product_id)}${input(`quantity_${i}`,'Количество',l.quantity)}${select(`location_${i}`,'Склад',inventory.locations,l.location_id)}<label>Единица<select name="unit_${i}">${['kg','L','piece','bag','container','package'].map(x=>`<option ${x===l.unit?'selected':''}>${x}</option>`).join('')}</select></label><small>Цена неизвестна — это не 0.</small></fieldset>`).join('');}
    else if(kind==='DEPOT_RECEIPT'){fields=input('event_date','Дата чека',f.event_date,'date')+select('depot_id','Депо',parties,f.depot_id);fields+=f.lines.map((l,i)=>`<fieldset><legend>${e(l.original_quality_name)} · ${e(l.source_line)}</legend>${input(`kg_${i}`,'кг',l.net_weight_kg)}${input(`price_${i}`,'Цена за кг',l.unit_price)}<label>Качество именно этого депо<select name="quality_${i}"><option value="">Уточнить</option>${['HIGHEST','FIRST','SECOND'].map(q=>`<option ${q===f.qualities[i]?'selected':''}>${q}</option>`).join('')}</select></label></fieldset>`).join('');fields+='<label><input name="quality_reviewed" type="checkbox"> Подтверждаю качества строк именно этого депо</label><label><input name="arithmetic_reviewed" type="checkbox"> Проверил строки и сумму; Alacak не означает оплату</label>';}
    else if(kind==='INVOICE_MATCH')fields=select('receipt_id','Уже подтверждённый физический приход',inventory.events?.filter(r=>r.kind==='receipt'&&r.status==='posted').map(r=>({id:r.id,name:r.event_date+' · '+r.payload.lines.map(l=>l.original_product_name).join(', ')}))||[],f.receipt_id);
    else fields='<p>Автоматическая подготовка не определила событие. Оригинал сохранён; выберите «Ввести вручную».</p>';
    openSheet('Исправить карточку',`<form id="r2-edit-form" class="r2-fields">${fields}<button class="primary" type="submit">Сохранить исправления</button></form><button id="r2-advanced">Ввести вручную</button><p id="r2-edit-status" role="status"></p>`);
    $('#r2-advanced').onclick=()=>{$('#sheet').close();advanced();};
    $('#r2-party-new')?.addEventListener('click',()=>{openSheet('Новый контрагент','<form id="r2-new-party"><label>Название<input name="name" required maxlength="250"></label><button class="primary">Готово</button></form>');$('#r2-new-party').onsubmit=guard(async ev=>{ev.preventDefault();const r=await api(root()+'/counterparties',{name:ev.target.elements.name.value,confirmed:true});await reload();p.revision.payload.counterparty_id=r.id;edit(p);});});
    $('#r2-edit-form').onsubmit=async ev=>{ev.preventDefault();const b=ev.target.querySelector('[type=submit]');b.disabled=true;try{const values=Object.fromEntries(new FormData(ev.target)),payload={...f};
      for(const [k,v] of Object.entries(values))if(k in payload&&!['lines','qualities','quality_reviewed','arithmetic_reviewed'].includes(k))payload[k]=v||null;
      if(kind==='PAYMENT'){payload.payment_purpose='debt';payload.obligation_id=values.obligation_id||null;}
      if(kind.startsWith('STOCK_'))payload.lines=f.lines.map((l,i)=>({...l,product_id:values[`product_${i}`]||null,quantity:values[`quantity_${i}`]||null,location_id:values[`location_${i}`]||null,unit:values[`unit_${i}`]}));
      if(kind==='DEPOT_RECEIPT'){payload.lines=f.lines.map((l,i)=>({...l,net_weight_kg:values[`kg_${i}`],unit_price:values[`price_${i}`]}));payload.qualities=f.lines.map((_,i)=>values[`quality_${i}`]||null);payload.quality_reviewed=values.quality_reviewed==='on';payload.arithmetic_reviewed=values.arithmetic_reviewed==='on';}
      await api(root()+`/confirmations/${p.id}/revise`,{expected_version:p.version,payload});await reload();await review(p.id);
    }catch(err){$('#r2-edit-status').textContent=err.message;}finally{b.disabled=false;}};
  }
  window.addEventListener('online',guard(sync));document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')sync().catch(()=>{});});
  function logout(){sessionGeneration++;clearTimeout(pollTimer);if($('#owner-capture-bar'))$('#owner-capture-bar').hidden=true;document.body.classList.remove('has-owner-capture');}
  return {render,bind,mount,capture,queue,sync,logout};
}
