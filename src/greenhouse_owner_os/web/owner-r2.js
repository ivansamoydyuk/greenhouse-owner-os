import {escape as e,money,displayDate} from './core.js';
import {lineAmount} from './harvest.js';
import {categoryNames,taskWhenLabel} from './tasks.js';

export function receiptTotal(lines){const cents=lines.reduce((sum,l)=>sum+BigInt(lineAmount(l.net_weight_kg,l.unit_price).replace('.','')),0n);return `${cents/100n}.${String(cents%100n).padStart(2,'0')}`;}

const titles={PAYABLE:'Долг',PAYMENT:'Оплата',DIRECT_EXPENSE:'Расход',STOCK_RECEIPT:'Товар привезли',STOCK_APPLICATION:'Фактическое внесение',DEPOT_RECEIPT:'Чек депо',PERSONAL_TASK:'Моя задача',WORKER_TASK:'Поручение работнику',INVOICE_MATCH:'Счёт к ранее принятому товару',UNRESOLVED:'Нужно уточнить событие'};
const statuses={PREPARING:'Готовим',NEEDS_INPUT:'Уточните',READY:'Готово к подтверждению',DEFERRED:'Отложено',POSTED:'Подтверждено',CONFIRMED_NONPOSTING:'Подтверждено · не отправлено',REJECTED:'Отклонено',STALE:'Данные изменились',FAILED:'Нужно повторить подготовку'};
const dbName='owner-source-pending-v1';
export async function pendingStore(){
  return new Promise((resolve,reject)=>{const request=indexedDB.open(dbName,1);request.onupgradeneeded=()=>request.result.createObjectStore('sources',{keyPath:'key'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(Error('Не удалось сохранить на телефоне. Оставьте окно открытым и повторите.'));});
}
export async function pendingWrite(value){const db=await pendingStore();return new Promise((resolve,reject)=>{const tx=db.transaction('sources','readwrite');tx.objectStore('sources').put(value);tx.oncomplete=()=>{db.close();resolve();};tx.onerror=tx.onabort=()=>{db.close();reject(Error('Память телефона недоступна или заполнена. Источник ещё не сохранён.'));};});}
async function pendingList(scope){const db=await pendingStore();return new Promise((resolve,reject)=>{const tx=db.transaction('sources');const r=tx.objectStore('sources').getAll();r.onsuccess=()=>resolve(r.result.filter(x=>x.scope===scope).sort((a,b)=>(a.body.captured_at||'').localeCompare(b.body.captured_at||'')||a.key.localeCompare(b.key)));r.onerror=()=>reject(Error('Очередь телефона недоступна'));tx.oncomplete=()=>db.close();});}
async function pendingDelete(key,isCurrent=()=>true){const db=await pendingStore();if(!isCurrent()){db.close();return;}return new Promise((resolve,reject)=>{const tx=db.transaction('sources','readwrite');tx.objectStore('sources').delete(key);tx.oncomplete=()=>{db.close();resolve();};tx.onerror=()=>reject(Error('Не удалось обновить очередь телефона'));});}
// The upload can outlive a logout or an explicit local deletion. Update only a
// still-present original, in the same transaction that reads it; never recreate.
async function pendingFailure(record,upload_error,isCurrent){
  const db=await pendingStore();if(!isCurrent()){db.close();return false;}
  return new Promise((resolve,reject)=>{
    const tx=db.transaction('sources','readwrite'),store=tx.objectStore('sources'),request=store.getAll();let written=false;
    request.onsuccess=()=>{const current=request.result.find(r=>r.key===record.key);if(isCurrent()&&current&&JSON.stringify(current.body)===JSON.stringify(record.body)){store.put({...current,upload_error});written=true;}};
    tx.oncomplete=()=>{db.close();resolve(written);};tx.onerror=tx.onabort=()=>{db.close();reject(Error('Не удалось сохранить причину ошибки. Оригинал остаётся в локальной очереди.'));};
  });
}
// Only source-local validation is safe to bypass. Unknown failures pause the
// pass: a lost response may already have durably saved this exact UUID/body.
export function sourceUploadError(error){
  const status=Number.isInteger(error.status)?error.status:null;
  const kind=[400,413,415,422].includes(status)?'validation':status===401?'auth':status===403?'access':'retry';
  return {kind,status,message:error.message||'Не удалось отправить источник.'};
}
export function sourceUploadMessage(error){
  const reason=`${error.status?`HTTP ${error.status} · `:''}${error.message}`;
  if(error.kind==='validation')return `Источник не принят: ${reason} Оригинал сохранён на телефоне; откройте «Копии на телефоне» для экспорта или явного повтора. Другие источники отправляются отдельно.`;
  if(error.kind==='auth')return `Отправка приостановлена: ${reason} Оригинал сохранён на телефоне; войдите снова.`;
  if(error.kind==='access')return `Отправка приостановлена: ${reason} Проверьте доступ. Оригинал сохранён на телефоне.`;
  return `Отправка приостановлена: ${reason} Оригинал сохранён на телефоне. Повтор сохранит тот же источник.`;
}

function fileData(file){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]);r.onerror=()=>reject(Error('Не удалось прочитать файл'));r.readAsDataURL(file);});}

export function receiptEffectTotal(lines){const cents=lines.reduce((sum,l)=>{const [whole,fraction='']=String(l.confirmed_amount??lineAmount(l.net_weight_kg,l.unit_price)).split('.');return sum+BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0'));},0n);return `${cents/100n}.${String(cents%100n).padStart(2,'0')}`;}

// Formatting only: preserve decimal strings and never round through binary floats.
export function displayQuantity(value){
  if(value==null||!/^[-]?\d+(?:\.\d+)?$/.test(String(value)))return 'UNKNOWN';
  const [whole,fraction='']=String(value).split('.'),tail=fraction.replace(/0+$/,'');
  return whole.replace(/\B(?=(\d{3})+(?!\d))/g,' ')+(tail?','+tail:'');
}
export function displayMetric(value){
  if(value==null||!/^\d+(?:\.\d+)?$/.test(String(value)))return 'UNKNOWN';
  const [whole,fraction='']=String(value).split('.');
  const cents=BigInt(whole)*100n+BigInt(fraction.padEnd(2,'0').slice(0,2))+((fraction[2]||'0')>='5'?1n:0n);
  return displayQuantity(cents/100n)+','+String(cents%100n).padStart(2,'0');
}
const captureModes=[['photo','Фото'],['voice_transcript','Голос'],['text','Текст']];
const captureIcon=mode=>`<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${({photo:'<path d="M8 5l1.5-2h5L16 5h4a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z"/><circle cx="12" cy="12" r="4"/>',voice_transcript:'<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8"/>',text:'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6m-6 4h6m-6 4h4"/>'})[mode]}</svg>`;

const qualityNames={HIGHEST:'Высшее',FIRST:'Первое',SECOND:'Второе'};
const languageNames={ru:'Русский',tr:'Турецкий',fa:'Дари',uz:'Узбекский'};
export function workerProposal(payload,workers){return {...payload,language:workers.find(w=>w.id===payload.worker_id)?.language||null};}
// Presentation-only cleanup. The captured source and proposal payload are never
// rewritten to make a scan-friendly heading; the original remains in details.
export function conciseProposalText(value,limit=112){
  let text=String(value||'').replace(/\s+/g,' ').trim();
  const sentences=text.match(/.*?[.!?](?=\s|$)|.+$/g)||[];
  if(sentences.length>1){const unique=[];for(const sentence of sentences){const trimmed=sentence.trim();if(trimmed!==unique.at(-1))unique.push(trimmed);}text=unique.join(' ');}
  const words=text.split(' ');
  for(let n=3;n<=words.length/2;n++)if(words.length%n===0&&words.every((word,i)=>word===words[i%n])){text=words.slice(0,n).join(' ');break;}
  if(text.length>limit){const cut=text.slice(0,limit-1),space=cut.lastIndexOf(' ');text=cut.slice(0,space>limit/2?space:cut.length).trimEnd()+'…';}
  return text;
}
const translationSameAsOriginal=(text,original)=>!!text?.trim()&&text.trim().replace(/\s+/g,' ').toLocaleLowerCase()===String(original||'').trim().replace(/\s+/g,' ').toLocaleLowerCase();
export function workerTranslationIssue(payload){
  if(!payload.language||payload.language==='ru')return '';
  if(!payload.rendered_text?.trim())return 'Нет проверенного перевода. Добавьте перевод на язык работника.';
  if(translationSameAsOriginal(payload.rendered_text,payload.original_instruction))return 'Текст совпадает с оригиналом. Проверенный перевод ещё не добавлен.';
  return '';
}
const effects={
  PAYABLE:'Создаст долг поставщику. Деньги со счёта не списываются.',
  PAYMENT:'Спишет деньги с выбранного счёта и погасит только выбранный долг. Новый расход или аванс не создаётся.',
  DIRECT_EXPENSE:'Запишет оплаченный расход и спишет деньги с выбранного счёта. Долг и аванс не создаются.',
  STOCK_RECEIPT:'Увеличит физический остаток указанного товара на выбранном складе. Оплату или долг не создаёт.',
  STOCK_APPLICATION:'Запишет фактическое внесение и уменьшит остаток указанного товара на складе. Поручение работнику не отправляется.',
  DEPOT_RECEIPT:'Запишет продажу и долг депо одной операцией. Alacak не означает получение денег. Отчёт работника останется отдельным доказательством.',
  PERSONAL_TASK:'Создаст личную задачу. Деньги и склад не изменятся.',
  WORKER_TASK:'Создаст черновик задачи R1. Работнику не отправлено; назначение и отправка выполняются отдельно в разделе задач.',
  INVOICE_MATCH:'Свяжет счёт с существующим физическим приходом. Повторного прихода не будет; стоимость не изменится.',
  UNRESOLVED:'Действие пока не определено. Оригинал сохранён, бизнес-эффект не создаётся.'
};
const fact=(label,value)=>`<dl class="owner-fact"><dt>${e(label)}</dt><dd><strong>${e(value??'Не указано')}</strong></dd></dl>`;
const exactMoney=(amount,currency)=>amount==null?'Не указана':money(amount,currency||'Валюта не определена');
// A readable subtotal is never evidence of a complete receipt amount.
const completeDepotLines=f=>!!f.lines?.length&&!f.unreadable_lines?.length;
// The reviewed snapshot is preferred over a later Home refresh for effect facts.
export function proposalSummary(p,data={},withOpen=true){
  const f=p.revision.payload,kind=p.action_type,basis=p.revision.basis||{},finance=data.owner?.finance||{},areas=finance.work_areas||[];
  const find=(table,id,list=[])=>basis[`${table}:${id}`]||list.find(x=>x.id===id||x.obligation_id===id);
  const name=(table,id,list=[],empty='Не выбран')=>{const r=find(table,id,list);return r?.original_name||r?.name||r?.display_name||r?.label||(id?`Не найдено · ${id}`:empty);};
  const ref=id=>name('core.counterparties',id,finance.references||[]);
  const area=id=>name('core.work_areas',id,areas,'Не указана');
  let facts='';
  if(['PAYABLE','PAYMENT','DIRECT_EXPENSE'].includes(kind)){
    facts+=fact('Контрагент',f.counterparty_id?ref(f.counterparty_id):kind==='DIRECT_EXPENSE'?'Не указан · допустимо для прямого расхода':'Не выбран');
    facts+=fact('Валюта',f.currency||'Валюта не определена');
    if(kind!=='PAYABLE')facts+=fact('Счёт',name('finance.money_accounts',f.money_account_id,finance.references||[]));
    if(kind==='PAYMENT'){
      const debt=find('finance.obligations',f.obligation_id,finance.obligations||[]);
      facts+=fact('Погашаемый долг',debt?`${debt.description||'Долг'} · ${ref(debt.counterparty_id)} · ${exactMoney(debt.original_amount,debt.currency)}`:f.obligation_id||'Не выбран');
      const remaining=typeof basis.debt_remaining==='object'?basis.debt_remaining?.remaining_amount:basis.debt_remaining;
      facts+=fact('Остаток долга до оплаты',exactMoney(remaining??debt?.remaining_amount,f.currency));
    }else{
      facts+=(f.attributions||[]).map(a=>fact('Теплица / участок',`${area(a.work_area_id)} · ${exactMoney(a.amount,f.currency)}`)).join('');
      if(!f.attributions?.length)facts+=fact('Распределение','GENERAL · без указанной теплицы');
      else facts+='<small>Указаны только точные суммы. Нераспределённый остаток остаётся GENERAL.</small>';
      if(kind==='DIRECT_EXPENSE')facts+='<small>Распределение прямого расхода по теплицам в этой карточке не поддерживается.</small>';
      if(f.due_date)facts+=fact('Ожидаемая дата оплаты',displayDate(f.due_date));
    }
  }else if(kind.startsWith('STOCK_')){
    if(f.separate_event_reviewed)facts+=fact('Повтор похожего текста','Владелец проверил: это отдельный физический приход');
    facts+=(f.lines||[]).map(l=>`<section class="owner-review-line">${fact('Товар',name('inventory.products',l.product_id,data.inventory?.products||[],l.original_product_name||'Не выбран'))}${fact('Название в оригинале',l.original_product_name)}${fact('Количество',`${l.quantity??'UNKNOWN'} ${l.unit||'единица не указана'}`)}${fact('Склад',name('inventory.locations',l.location_id,data.inventory?.locations||[]))}${kind==='STOCK_APPLICATION'?fact('Теплица внесения',area(l.greenhouse_id))+(l.work_area_id?fact('Участок',area(l.work_area_id)):''):''}${fact('Стоимость за базовую единицу',l.unit_cost==null?'UNKNOWN · неизвестна, не 0':exactMoney(l.unit_cost,l.currency))}</section>`).join('');
  }else if(kind==='DEPOT_RECEIPT'){
    if(f.separate_event_reviewed)facts+=fact('Повтор похожего текста','Владелец проверил: это отдельный чек по текстовому источнику');
    facts+=fact('Депо',ref(f.depot_id))+fact('Валюта',f.currency||'Валюта не определена')+fact('Теплица в оригинале',f.original_greenhouse||'Не указана');
    facts+=(f.lines||[]).map((l,i)=>`<section class="owner-review-line">${fact('Качество в оригинале',l.original_quality_name)}${fact('Качество именно этого депо',`${qualityNames[f.qualities?.[i]]||'Не определено'} · ${f.quality_reviewed?'проверено владельцем':'кандидат, нужна проверка'}`)}${fact('Строка источника',l.source_line)}${fact('Вес',`${l.net_weight_kg} кг`)}${fact('Цена за кг',exactMoney(l.unit_price,f.currency))}${fact('Сумма по расчёту',exactMoney(lineAmount(l.net_weight_kg,l.unit_price),f.currency))}${fact('Сумма строки в оригинале',l.stated_amount==null?'Не читается':exactMoney(l.stated_amount,f.currency))}${l.confirmed_amount!=null?fact('Проверенная сумма строки',exactMoney(l.confirmed_amount,f.currency)):''}${fact('Количество веток',l.count??'UNKNOWN · не указано')}${l.allocations?.length?l.allocations.map(a=>fact('Точное распределение',`${area(a.work_area_id)} · ${a.net_weight_kg} кг · ${exactMoney(a.amount,f.currency)} · ${a.confirmed_count??'UNKNOWN'} веток`)).join(''):'<p>Не распределено по теплицам. Оценочный вес и выручка не создаются.</p>'}</section>`).join('');
    const complete=completeDepotLines(f),hasLines=!!f.lines?.length;
    facts+=fact(complete?'Сумма по строкам':'Прочитанные строки · промежуточная сумма',hasLines?exactMoney(receiptTotal(f.lines),f.currency)+(complete?'':' · частично'):'UNKNOWN · строки чека не прочитаны');
    facts+=fact('Сумма продажи после подтверждения',complete?exactMoney(receiptEffectTotal(f.lines),f.currency):'UNKNOWN · нужно восстановить все строки чека');
    facts+=fact('Сумма источника',f.stated_total==null?'Не читается':exactMoney(f.stated_total,f.currency));
    if(f.unreadable_lines?.length)facts+=fact('Непрочитанные строки',f.unreadable_lines.join(' · '));
    facts+=`<p>Alacak: ${e(f.alacak_value??'—')} ${e(f.currency||'Валюта не определена')} — не оплата.</p>`;
    facts+=fact('Связанные партии',(f.batch_ids||[]).map(id=>name('harvest.batches',id,basis.same_day_batches||[])).join('; ')||'Не связаны');
    if(f.batch_ids?.length)facts+=fact('Основание связи',f.link_evidence||'Не указано');
    facts+=fact('Правило качества',f.remember_quality_mapping?'После подтверждения запомнить только для этого депо':'Только проверка этого чека; новое общее правило не создаётся');
  }else if(kind==='PERSONAL_TASK'||kind==='WORKER_TASK'){
    if(f.category)facts+=fact('Категория · предложено',categoryNames[f.category]||f.category);
    facts+=fact('Теплица',area(f.greenhouse_id));
    if(f.relative_time_context&&f.planned_date)facts+=fact('Срок из источника',f.relative_time_context);
    if(kind==='WORKER_TASK'){const issue=workerTranslationIssue(f);facts+=fact('Работник',name('workers.people',f.worker_id,data.tasks?.workers||[],'Не выбран · не назначен'))+fact('Язык',languageNames[f.language]||f.language||'Не указан')+`<details class="task-original"><summary>Исходное поручение</summary><p class="source-text">${e(f.original_instruction||'Не указано')}</p></details>`+(!f.worker_id||!f.language?'<p class="note">Выберите работника, чтобы проверить язык и текст назначения.</p>':issue?`<p class="bad task-translation-exception">${e(issue)}</p>`:fact(f.language==='ru'?'Текст для работника':'Проверенный перевод',f.rendered_text||f.original_instruction||'Не указан'));}
    if(f.note)facts+=fact('Примечание',f.note);
  }else if(kind==='INVOICE_MATCH'){
    const receipt=find('inventory.events',f.receipt_id,data.inventory?.events||[]);
    facts+=fact('Существующий приход',receipt?`${displayDate(receipt.event_date)} · ${(receipt.payload?.lines||[]).map(l=>`${l.original_product_name} ${l.quantity} ${l.unit}`).join('; ')}`:f.receipt_id||'Не выбран');
    if(f.finance_obligation_id)facts+=fact('Связанный долг',f.finance_obligation_id);
  }else if(f.note)facts+=fact('Сохранённое событие',f.note);
  return `<article class="card owner-proposal ${p.reminder_eligible?'overdue':''}" data-proposal="${e(p.id)}"><small>${e(titles[kind]||kind)} · ${e(statuses[p.state]||p.state)}</small><h2>${e(conciseProposalText((kind==='WORKER_TASK'?f.original_instruction:null)||f.title||f.description||titles[kind]||'Событие'))}</h2>${f.amount!=null?`<strong class="amount">${e(exactMoney(f.amount,f.currency))}</strong>`:''}<div class="owner-review-facts">${['PERSONAL_TASK','WORKER_TASK'].includes(kind)?fact('Когда',taskWhenLabel(f,'Пока не выбрана')):fact('Дата',displayDate(f.event_date||f.planned_date,'Пока не выбрана'))}${facts}</div><p class="note owner-review-effect" data-effect="${e(kind)}">После подтверждения: ${e(effects[kind]||'Нужна ручная проверка')}</p>${(p.revision.missing||[]).map(m=>`<p class="bad">${e(m)}</p>`).join('')}${withOpen?`<button data-r2-open="${e(p.id)}">Проверить карточку</button>`:''}</article>`;
}


// A scan-friendly queue card never authorizes an effect. Its primary action fetches
// a fresh, complete preview; only the separate review screen can confirm it.
export function proposalCard(p,data={}){
  const f=p.revision.payload,kind=p.action_type,basis=p.revision.basis||{},finance=data.owner?.finance||{};
  const name=(table,id,list=[],fallback='Не указано')=>{const v=basis[`${table}:${id}`]||list.find(x=>x.id===id);return v?.original_name||v?.name||v?.display_name||v?.label||(id?'Не найдено · '+id:fallback);};
  const party=id=>name('core.counterparties',id,finance.references||[]),area=id=>name('core.work_areas',id,finance.work_areas||[]);
  const lines=f.lines||[],context=[];
  let title=conciseProposalText((kind==='WORKER_TASK'?f.original_instruction:null)||f.title||f.description||titles[kind]||'Событие'),value=f.amount!=null?exactMoney(f.amount,f.currency):'';
  if(f.counterparty_id)context.push(party(f.counterparty_id));
  if(kind==='DEPOT_RECEIPT'){
    title=party(f.depot_id);value=completeDepotLines(f)?exactMoney(receiptEffectTotal(lines),f.currency):'UNKNOWN · итог чека';
    if(!completeDepotLines(f)){if(lines.length)context.push(`По прочитанным строкам: ${exactMoney(receiptEffectTotal(lines),f.currency)} · частично`);if(f.stated_total!=null)context.push(`В источнике: ${exactMoney(f.stated_total,f.currency)}`);}
    context.push(...lines.slice(0,2).map(l=>`${l.net_weight_kg??'UNKNOWN'} кг · ${l.count??'UNKNOWN'} веток`));if(f.original_greenhouse)context.push(f.original_greenhouse);
  }
  if(kind.startsWith('STOCK_')){title=lines[0]?name('inventory.products',lines[0].product_id,data.inventory?.products||[],lines[0].original_product_name||'Товар не выбран'):titles[kind];if(title.startsWith('Не найдено')&&lines[0]?.original_product_name)title=lines[0].original_product_name+' · товар не сопоставлен';value=lines.slice(0,2).map(l=>`${l.quantity??'UNKNOWN'} ${l.unit||'единица не указана'}`).join(' · ');context.push(...lines.slice(0,2).map(l=>name('inventory.locations',l.location_id,data.inventory?.locations||[],'Склад не выбран')));}
  if(lines.length>2)context.push(`Всего строк: ${lines.length} · все в проверке`);
  if(f.greenhouse_id)context.push(area(f.greenhouse_id));
  if(f.worker_id)context.push(name('workers.people',f.worker_id,data.tasks?.workers||[]));
  if(kind==='PAYABLE'&&!f.attributions?.length)context.push('GENERAL · без указанной теплицы');
  if(['PERSONAL_TASK','WORKER_TASK'].includes(kind)){if(f.category)context.push(categoryNames[f.category]||f.category);context.push(taskWhenLabel(f,'Дата не выбрана'));if(f.planned_date&&f.relative_time_context)context.push('В источнике: '+f.relative_time_context);}
  else context.push(displayDate(f.event_date||f.planned_date,'Дата не выбрана'));
  const warnings=[...(p.revision.missing||[])];if(kind==='WORKER_TASK'&&workerTranslationIssue(f))warnings.unshift(workerTranslationIssue(f));
  if(kind.startsWith('STOCK_')&&lines.some(l=>l.unit_cost==null))warnings.unshift('Стоимость UNKNOWN · неизвестна, не 0');
  if(kind==='DEPOT_RECEIPT'&&lines.some(l=>l.count==null))warnings.unshift('Количество веток UNKNOWN · не указано');
  if(kind==='DEPOT_RECEIPT'&&!completeDepotLines(f))warnings.unshift(lines.length?'Итог UNKNOWN: есть непрочитанные строки, требуется уточнение':'Сумма UNKNOWN: строки чека не прочитаны');
  const label=p.source?.filename|| (p.source?.text?'Текстовый источник':'Источник сохранён');
  const primaryLabel=kind==='UNRESOLVED'?'Уточнить':'Подтвердить';
  return `<article class="card queue-card ${p.reminder_eligible?'overdue':''}" data-proposal="${e(p.id)}"><div class="queue-card-head"><small>${e(titles[kind]||kind)} · ${e(statuses[p.state]||p.state)}</small>${p.reminder_eligible?'<span class="badge">Старше 36 часов</span>':''}</div><h2>${e(title)}</h2>${value?`<p class="queue-card-value">${e(value)}</p>`:''}<p class="queue-card-context">${[...new Set(context)].map(e).join(' · ')}</p><p class="queue-card-effect" data-effect="${e(kind)}">${e(effects[kind]||'Нужна ручная проверка')}</p>${warnings.length?`<p class="queue-card-warning">${warnings.slice(0,2).map(e).join(' · ')}${warnings.length>2?` · Ещё ${warnings.length-2} уточнения в проверке`:''}</p>`:''}<div class="queue-card-actions"><button type="button" class="primary" data-r2-open="${e(p.id)}" aria-label="${primaryLabel}: проверить ${e(title)}">${primaryLabel}</button><button type="button" class="secondary" data-r2-edit="${e(p.id)}">Исправить</button><button type="button" class="secondary" data-r2-later="${e(p.id)}">Позже</button></div><details><summary>Источник и подробности</summary><p class="note">${e(label)}. Перед подтверждением откроется полная актуальная проверка.</p><button type="button" data-r2-evidence="${e(p.id)}">Оригинал, детали и отклонение</button></details></article>`;
}

export function ownerR2({api,openSheet,reload,getData,getEntity,notice,advanced,navigate,getOwnerId=()=>getData()?.owner?.owner_id}){
  let syncing=null,sessionGeneration=0,viewGeneration=0,pollTimer,queueTimer,disposeCapture=null,onCaptureSynced=null,onCaptureError=null;
  const $=s=>document.querySelector(s),root=()=>`/v1/entities/${getEntity()}/owner`,home=()=>getData()?.owner;
  const scope=()=>getEntity()&&getOwnerId()?`${getEntity()}:${getOwnerId()}`:null;
  const session=()=>({generation:sessionGeneration,scope:scope(),root:root()});
  const sameSession=t=>!!t.scope&&t.generation===sessionGeneration&&t.scope===scope();
  const snapshot=()=>({...session(),view:viewGeneration,anchor:$('#sheet-content')?.firstElementChild,open:!!$('#sheet')?.open});
  const active=t=>sameSession(t)&&t.view===viewGeneration&&t.anchor===$('#sheet-content')?.firstElementChild&&t.open===!!$('#sheet')?.open;
  const invalidate=()=>{viewGeneration++;clearTimeout(queueTimer);disposeCapture?.();disposeCapture=null;};
  const begin=()=>{invalidate();return snapshot();};
  const show=(t,title,html)=>{if(!active(t))return false;openSheet(title,html);t.anchor=$('#sheet-content')?.firstElementChild;t.open=!!$('#sheet')?.open;return true;};
  const guard=fn=>async(...args)=>{let t=snapshot();try{const work=fn(...args);t=snapshot();await work;}catch(err){if(active(t))notice(err.message);}};
  $('#sheet')?.addEventListener('close',invalidate);
  $('#sheet')?.addEventListener('cancel',invalidate);
  window.addEventListener('popstate',invalidate);
  window.addEventListener('hashchange',invalidate);
  document.addEventListener('click',ev=>{if(ev.target.closest?.('#nav button,[data-go]'))invalidate();},true);
  const input=(key,label,value='',type='text')=>`<label>${e(label)}<input name="${key}" type="${type}" value="${e(value??'')}"></label>`;
  const options=(list,value,empty='Не выбран')=>`<option value="">${empty}</option>${list.map(x=>`<option value="${e(x.id)}" ${x.id===value?'selected':''}>${e(x.original_name||x.name||x.display_name)}</option>`).join('')}`;
  const select=(key,label,list,value,empty)=>`<label>${e(label)}<select name="${key}" aria-label="${e(label)}">${options(list,value,empty)}</select></label>`;
  function mount(){
    let bar=$('#owner-capture-bar');
    if(!bar){bar=document.createElement('div');bar.id='owner-capture-bar';($('#owner-dock')||document.body).append(bar);}
    bar.className='owner-capture-bar';bar.setAttribute('aria-label','Записать событие');bar.hidden=!home();
    // A refresh must not replace the focused capture control or its DOM identity.
    if(!bar.querySelector('[data-r2-mode]')){
      bar.innerHTML=captureModes.map(([mode,label])=>`<button type="button" data-r2-mode="${mode}">${captureIcon(mode)}<span>${label}</span></button>`).join('');
      bar.querySelectorAll('button').forEach(b=>b.onclick=()=>capture(b.dataset.r2Mode));
    }
    document.body.classList.toggle('has-owner-capture',!!home());
  }
  function render(){
    const h=home(),b=h.finance.balances.TRY||{},season=h.season.find(s=>s.currency==='TRY'),tasks=h.tasks.slice(0,3),exceptions=h.worker_exceptions;
    return `<div class="topline owner-topline"><div><p class="eyebrow">${e(h.finance.entity_name)}</p><h1>Моя теплица</h1><small>ТЕСТОВЫЕ ДАННЫЕ · ${e(displayDate(h.finance.as_of_date))}</small></div><span class="badge">Обзор дня</span></div>
      <div class="owner-home"><section class="hero owner-receivable owner-kpi"><p>Мне должны <span class="kpi-currency">TRY</span></p><div class="big">${e(money(b.receivable||'0','TRY'))}</div><small>Депо и клиенты · подтверждённые долги</small></section>
      <section class="card owner-payable owner-kpi"><p>Я должен <span class="kpi-currency">TRY</span></p><strong>${e(money(h.finance.business_balances.TRY?.operational_payable||'0','TRY'))}</strong><small>Операционные долги</small></section>
      <button class="card owner-queue-link ${h.confirmations.overdue?'overdue':''}" id="owner-queue"><span>На проверку</span><strong>${e(h.confirmations.n)}</strong><small>${h.confirmations.overdue?`${e(h.confirmations.overdue)} старше 36 часов · требуют внимания`:h.confirmations.n?'Открыть проверку действий':'Все действия разобраны'}</small></button>
      <section class="card owner-season"><div class="section-head"><h2>Урожай сезона</h2><button class="text-button" data-go="harvest">Подробнее</button></div><small>${e(displayDate(h.season_start))} → ${e(displayDate(h.season_end))} · конец не включён</small>${season?`<p class="owner-season-total"><strong>${e(displayQuantity(season.kg))}</strong> <span>кг</span></p><small>Подтверждённые продажи · TRY</small><div class="owner-season-metrics"><div><span>Средняя ветка</span><strong>${season.average_bunch_weight!=null?`${e(displayMetric(season.average_bunch_weight))} кг`:'Неизвестно'}</strong></div><div><span>Средняя цена</span><strong>${season.average_price!=null?`${e(displayMetric(season.average_price))} TRY/кг`:'Неизвестно'}</strong></div></div><div class="owner-coverage ${season.count_coverage_complete?'complete':'incomplete'}"><span>${season.count_coverage_complete?'Полное покрытие количества':'Количество известно не для всего веса'}</span><small>${season.count!=null?`${e(displayQuantity(season.count_covered_kg))} кг / ${e(displayQuantity(season.count))} веток`:'Нет подтверждённого количества'} · строки чеков депо</small></div><details><summary>Как считаются средние</summary><p class="note">Средняя ветка: вес только строк с подтверждённым количеством / их количество. Средняя цена: сумма продаж / соответствующий вес, независимо от оплаты.</p></details>`:'<p class="empty">Подтверждённых продаж в TRY в этом сезоне пока нет.</p>'}${h.season.filter(s=>s.currency!=='TRY').map(s=>`<div class="owner-other-currency"><strong>${e(s.currency)} · ${e(displayQuantity(s.kg))} кг</strong><small>Отдельно от TRY · средняя цена ${s.average_price!=null?`${e(displayMetric(s.average_price))} ${e(s.currency)}/кг`:'неизвестна'}</small></div>`).join('')}</section>
      <section class="card owner-tasks"><div class="section-head"><h2>Мои задачи</h2><span class="badge">${e(h.task_count)}</span></div>${tasks.map(t=>`<div class="owner-task"><span>${e(t.title)}<small class="${t.planned_date&&t.planned_date<h.finance.as_of_date?'bad':''}">${t.planned_date&&t.planned_date<h.finance.as_of_date?'Просрочено · ':''}${e(taskWhenLabel(t,'Без даты'))}</small>${t.category||t.greenhouse_id?`<small>${[t.category?categoryNames[t.category]||t.category:null,h.finance.work_areas?.find(a=>a.id===t.greenhouse_id)?.name].filter(Boolean).map(e).join(' · ')}</small>`:''}${t.planned_date&&t.relative_time_context?`<small>В источнике: ${e(t.relative_time_context)}</small>`:''}${t.note?`<small>${e(t.note)}</small>`:''}</span><button class="secondary" data-owner-done="${e(t.id)}" data-version="${e(t.version)}" aria-label="Готово: ${e(t.title)}">Готово</button></div>`).join('')||'<p class="empty">Открытых личных задач нет</p>'}${h.task_count>3?'<small>Ближайшие 3 задачи</small>':''}</section>
      <section class="card owner-workers"><div class="section-head"><h2>Работники</h2><button class="text-button" data-go="tasks">Задачи</button></div>${exceptions.map(t=>`<p class="worker-exception"><strong>${e(t.title)}</strong><small class="bad">Проблема требует проверки</small></p>`).join('')||'<p class="owner-clear-state">Нет известных текущих проблем</p>'}<small>Текущие сообщения, требующие внимания</small></section>
      <section class="card owner-source-tools"><div class="section-head"><h2>Сохранение источников</h2><span class="badge">На телефоне</span></div><p id="owner-pending-state" role="status">Проверяем локальную очередь…</p><div class="actions"><button class="secondary" id="owner-sync">Повторить отправку</button><button id="owner-local">Копии на телефоне</button><button id="owner-sources">Сохранённые источники</button></div><details><summary>Как работает отправка</summary><p class="note">iPhone может приостановить приложение. Повторная отправка выполняется при открытом приложении. Память браузера может быть очищена системой.</p></details></section>
      ${getData().preview?.r2_fixtures?'<a class="owner-fixture-link" href="/preview/fixtures/gorgun.png" target="_blank" rel="noopener">Скачать ТЕСТОВЫЙ чек Görgün для фото-проверки</a>':''}<button id="owner-advanced" class="secondary owner-manual">Финансы — вручную</button></div>`;
  }
  function bind(){
    mount();$('#owner-queue')?.addEventListener('click',guard(()=>navigate?navigate('review'):queue()));$('#owner-advanced')?.addEventListener('click',()=>advanced());$('#owner-sync')?.addEventListener('click',guard(sync));$('#owner-sources')?.addEventListener('click',guard(sources));
    document.querySelectorAll('[data-owner-done]').forEach(b=>b.onclick=guard(async()=>{const t=session();if(!t.scope)return;b.disabled=true;try{await api(t.root+`/tasks/${b.dataset.ownerDone}/state`,{expected_version:Number(b.dataset.version),state:'DONE'});if(sameSession(t))await reload();}finally{b.disabled=false;}}));
    const badgeSession=session(),badgeNode=$('#owner-pending-state');pendingBadge().catch(()=>{if(sameSession(badgeSession)&&badgeNode&&badgeNode===$('#owner-pending-state'))badgeNode.textContent='Локальная очередь недоступна; проверьте сохранение каждого источника.';});
    sync().catch(()=>{});
    $('#owner-local')?.addEventListener('click',guard(localSources));
  }
  async function pendingBadge(){const t=session(),node=$('#owner-pending-state');if(!t.scope)return;const list=await pendingList(t.scope);if(sameSession(t)&&node&&node===$('#owner-pending-state'))node.textContent=list.length?`На телефоне, ещё не в системе: ${list.length}${list.some(r=>r.upload_error)?' · есть ошибки отправки, откройте копии на телефоне':''}`:'Неотправленных источников нет';}
  async function localSources(){
    const t=begin();if(!t.scope)return;const records=await pendingList(t.scope);
    if(!show(t,'Ещё не в системе',`<p class="note">Эти оригиналы сохранены только в этом браузере. Экспортируйте копию перед очисткой памяти. Удаление не создаёт бизнес-действие.</p>${records.map((r,i)=>`<article class="card"><h3>${e(r.body.text||r.body.filename||'Источник')}</h3><small>${e(displayDate(r.body.captured_at))}</small>${r.upload_error?`<p class="bad" role="status">${e(sourceUploadMessage(r.upload_error))}</p>`:''}<div class="actions">${r.upload_error?.kind==='validation'?`<button data-local-retry="${i}">Повторить этот источник</button>`:''}<button data-local-export="${i}">Экспорт копии</button><button class="danger" data-local-delete="${i}">Удалить с телефона</button></div></article>`).join('')||'<p>Неотправленных источников нет</p>'}`))return;
    document.querySelectorAll('[data-local-export]').forEach(b=>b.onclick=()=>{if(!active(t))return;const record=records[Number(b.dataset.localExport)],url=URL.createObjectURL(new Blob([JSON.stringify(record.body,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download=`capture-${record.body.client_capture_id}.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
    document.querySelectorAll('[data-local-retry]').forEach(b=>b.onclick=guard(async()=>{if(!active(t))return;b.disabled=true;try{await sync({retryKey:records[Number(b.dataset.localRetry)].key});}finally{if(active(t))await localSources();}}));
    document.querySelectorAll('[data-local-delete]').forEach(b=>b.onclick=guard(async()=>{if(!active(t)||!window.confirm('Удалить оригинал только с телефона? В системе его может ещё не быть. Сначала сохраните экспорт копии.'))return;await pendingDelete(records[Number(b.dataset.localDelete)].key,()=>active(t));if(!active(t))return;await pendingBadge();if(active(t))await localSources();}));
  }
  async function sources(offset=0){
    const t=begin();if(!t.scope)return;const r=await api(t.root+`/captures?offset=${offset}`);
    if(!show(t,'Сохранённые источники',r.items.map(s=>`<article class="card"><h3>${e(s.text||s.original_name||'Фото / файл')}</h3><p>Сохранено в системе · ${e(displayDate(s.captured_at))}</p><p>${e(({SERVER_SAVED:'Ожидает подготовки',PROCESSING:'Готовим',FAILED:'Подготовка не удалась; оригинал сохранён',NEEDS_REVIEW:'Нужна проверка',READY:'Готово к проверке',CONFIRMED:'Подтверждено',REJECTED:'Отклонено'})[s.state]||s.state)}</p><a href="${t.root}/captures/${e(s.id)}/original" target="_blank" rel="noopener">Открыть оригинал</a>${(s.state==='FAILED'||s.state==='SERVER_SAVED')&&s.retryable!==false?`<button data-source-retry="${e(s.id)}">Повторить подготовку</button>`:s.state==='FAILED'&&s.retryable===false?'<p class="bad">Автоматическая подготовка исчерпала попытки. Оригинал сохранён; проверьте его и восстановите действие вручную.</p><button data-source-manual>Ввести вручную по оригиналу</button>':''}${s.proposals.map(p=>`<button data-source-proposal="${e(p.id)}">Открыть действие · ${e(statuses[p.state])}</button>`).join('')}</article>`).join('')+(r.next_offset!=null?'<button id="r2-sources-next">Следующие источники →</button>':'')||'<p>Источников пока нет</p>'))return;
    $('#r2-sources-next')?.addEventListener('click',guard(()=>{if(active(t))return sources(r.next_offset);}));
    document.querySelectorAll('[data-source-manual]').forEach(b=>b.onclick=()=>{if(active(t)){$('#sheet').close();invalidate();advanced('UNRESOLVED');}});
    document.querySelectorAll('[data-source-proposal]').forEach(b=>b.onclick=guard(()=>{if(active(t))return review(b.dataset.sourceProposal);}));
    document.querySelectorAll('[data-source-retry]').forEach(b=>b.onclick=guard(async()=>{if(!active(t))return;b.disabled=true;try{await api(t.root+`/captures/${b.dataset.sourceRetry}/retry-preparation`,{});if(!active(t))return;notice('Оригинал сохранён. Запрошена отдельная подготовка.');await sources(offset);}finally{if(active(t))b.disabled=false;}}));
  }
  function sync({retryKey=null}={}){
    const t=session();if(!t.scope||!navigator.onLine)return Promise.resolve();
    // Concurrent callers share a pass. An explicit per-source retry waits for
    // that pass before starting its own; it never changes original identity.
    if(syncing?.generation===t.generation&&syncing.scope===t.scope){
      const retry=()=>{if(sameSession(t))return sync({retryKey});};
      return retryKey?syncing.promise.then(retry,retry):syncing.promise;
    }
    const job={...t};syncing=job;
    job.promise=(async()=>{try{for(const record of await pendingList(t.scope)){
      if(!sameSession(t))return;
      if(retryKey&&record.key!==retryKey)continue;
      if(record.upload_error?.kind==='validation'&&record.key!==retryKey)continue;
      try{
        const r=await api(t.root+'/captures',record.body,{isCurrent:()=>sameSession(t)});
        if(!sameSession(t))return;
        if(r?.server_saved!==true||r.client_capture_id!==record.body.client_capture_id)throw Error('Система не подтвердила сохранение источника');
      }catch(error){
        if(!sameSession(t))return;
        const upload_error=sourceUploadError(error);
        const kept=await pendingFailure(record,upload_error,()=>sameSession(t));if(!sameSession(t))return;
        if(kept)onCaptureError?.(record.body.client_capture_id,upload_error);
        if(upload_error.kind==='validation')continue;
        throw error;
      }
      await pendingDelete(record.key,()=>sameSession(t));if(!sameSession(t))return;onCaptureSynced?.(record.body.client_capture_id);notice('Сохранено в системе. Действия ещё не подтверждены.');
    }}finally{if(syncing===job)syncing=null;if(sameSession(t))await pendingBadge();}})();
    return job.promise;
  }
  function capture(mode='text'){
    const t=begin();if(!t.scope)return;
    if(!captureModes.some(([value])=>value===mode))mode='text';
    if(!show(t,'Что произошло?',`<form id="r2-source-form" class="capture-flow" data-capture-active="${mode}"><div class="capture-modes" role="group" aria-label="Способ записи">${captureModes.map(([value,label])=>`<button type="button" class="capture-mode ${mode===value?'active':''}" data-capture-mode="${value}" aria-pressed="${mode===value}">${captureIcon(value)}<span>${label}</span></button>`).join('')}</div><div id="r2-voice-tools" class="capture-voice-panel" ${mode==='voice_transcript'?'':'hidden'}><p id="r2-voice-state" class="capture-voice-state" role="status" aria-live="polite" data-state="ready">Готово к диктовке</p><div class="capture-voice-actions"><button type="button" id="r2-voice-stop">Стоп</button><button type="button" id="r2-voice-done">Готово</button><button type="button" id="r2-dictate" hidden>Повторить диктовку</button></div><small>Сохраняется только текст, не аудиозапись.</small></div><details id="r2-file-section" class="capture-attachments capture-photo-panel" ${mode==='photo'?'open':''}><summary id="r2-file-summary" ${mode==='photo'?'hidden':''}>Прикрепить фото / файл</summary><section class="capture-evidence" aria-label="Выбранный источник"><div id="r2-file-preview"><p class="capture-source-empty">Сфотографируйте документ или выберите файл.</p></div><label>Фото / файл<input id="r2-file" type="file" accept="image/jpeg,image/png,application/pdf" ${mode==='photo'?'capture="environment"':''}></label><small>JPEG/PNG/PDF до 20 MiB. HEIC сохраните как JPEG. Оригинал не обрезается.</small></section></details><details id="r2-text-details" class="capture-text-panel" ${mode==='photo'?'':'open'}><summary id="r2-text-summary" ${mode==='photo'?'':'hidden'}>Добавить подпись</summary><label><span id="r2-text-label">${mode==='voice_transcript'?'Расшифровка':mode==='photo'?'Подпись к источнику':'Что произошло?'}</span><textarea name="text" rows="5" maxlength="10000" placeholder="Заплатил Серкану 5 000" aria-describedby="r2-input-help"></textarea></label><small id="r2-input-help" class="capture-guidance">${mode==='voice_transcript'?'Говорите и проверьте текст перед сохранением.':'Запишите своими словами.'}</small></details><p id="r2-source-status" class="capture-status" role="status" aria-live="polite" data-state="empty">Источник ещё не сохранён.</p><button class="primary" type="submit">Сохранить источник</button></form>`))return;
    let recognition,reader,saved=false,saving=false,frozenText=null,finishing=false,focusWhenEnded=false,readingVersion=0,speechGeneration=0;
    const status=$('#r2-source-status'),form=$('#r2-source-form'),fileInput=$('#r2-file'),preview=$('#r2-file-preview'),dictate=$('#r2-dictate'),textInput=form.elements.text;
    const statusText=(text,state)=>{if(active(t)){status.textContent=text;status.dataset.state=state;}};
    const voiceState=(text,state)=>{if(!active(t))return;const node=$('#r2-voice-state');node.textContent=text;node.dataset.state=state;const running=state==='listening'||state==='starting';dictate.hidden=running||state==='finishing';$('#r2-voice-stop').hidden=!running;$('#r2-voice-done').hidden=!running;};
    // Every run owns its callbacks. Even a queued callback retained by WebKit
    // cannot modify text after abort/end, a restart, navigation or account switch.
    const stopRecognition=()=>{speechGeneration++;const wasFinishing=finishing;finishing=false;const submit=form.querySelector('[type=submit]');if(wasFinishing&&submit&&!saved&&!saving)submit.disabled=false;if(recognition){const old=recognition;recognition=null;old.onresult=null;old.onerror=null;old.onend=null;old.onstart=null;old.onaudiostart=null;try{old.abort();}catch{}}};
    const stopPreview=()=>{readingVersion++;if(reader){reader.onload=null;reader.onerror=null;if(reader.readyState===1)reader.abort();reader=null;}};
    disposeCapture=()=>{stopRecognition();stopPreview();onCaptureSynced=null;onCaptureError=null;};
    const fallback=()=>{stopRecognition();voiceState('Диктовка браузера недоступна','fallback');$('#r2-input-help').textContent='На iPhone нажмите микрофон на клавиатуре или введите текст.';textInput.focus?.();};
    const startRecognition=()=>{
      if(!active(t)||saved||saving||recognition)return;
      const Speech=window.SpeechRecognition||window.webkitSpeechRecognition;
      if(!Speech){fallback();return;}
      stopRecognition();focusWhenEnded=false;const generation=speechGeneration,baseline=textInput.value;
      try{
        const run=new Speech();recognition=run;run.lang='ru-RU';run.continuous=true;run.interimResults=true;
        const current=()=>active(t)&&!saved&&!saving&&recognition===run&&generation===speechGeneration&&mode==='voice_transcript';
        run.onresult=ev=>{
          if(!current())return;
          // results is cumulative for this recognition run, not an append-only
          // delta. Rebuild only its transcript, keeping the pre-run user text.
          const transcript=Array.from(ev.results).map(result=>result[0]?.transcript?.trim()||'').filter(Boolean).join(' ');
          textInput.value=baseline+(baseline&&transcript&&!/\s$/.test(baseline)?' ':'')+transcript;
        };
        run.onerror=()=>{if(current())fallback();};
        run.onstart=run.onaudiostart=()=>{if(current()&&!finishing)voiceState('Слушаю…','listening');};
        run.onend=()=>{if(!current())return;stopRecognition();voiceState('Диктовка завершена. Проверьте текст.','done');if(focusWhenEnded){focusWhenEnded=false;textInput.focus?.();}};
        voiceState('Включаем голосовой ввод…','starting');$('#r2-input-help').textContent='Говорите и проверьте текст перед сохранением.';
        // No await, timer or promise before start: this call remains in the
        // original Voice tap's user activation, including a mode-tab switch.
        run.start();
      }catch{fallback();}
    };
    const selectMode=(next,start=true)=>{
      if(!active(t)||saved||saving)return;if(next!==mode)stopRecognition();mode=next;form.dataset.captureActive=mode;
      document.querySelectorAll('[data-capture-mode]').forEach(button=>{const selected=button.dataset.captureMode===mode;button.classList.toggle('active',selected);button.setAttribute('aria-pressed',String(selected));});
      $('#r2-voice-tools').hidden=mode!=='voice_transcript';$('#r2-file-section').open=mode==='photo';$('#r2-file-summary').hidden=mode==='photo';$('#r2-text-details').open=mode!=='photo';$('#r2-text-summary').hidden=mode!=='photo';
      $('#r2-text-label').textContent=mode==='voice_transcript'?'Расшифровка':mode==='photo'?'Подпись к источнику':'Что произошло?';
      $('#r2-input-help').textContent=mode==='voice_transcript'?'Говорите и проверьте текст перед сохранением.':'Запишите своими словами.';
      if(mode==='photo')fileInput.setAttribute('capture','environment');else fileInput.removeAttribute?.('capture');
      if(mode==='text')textInput.focus?.();else textInput.blur?.();
      if(mode==='voice_transcript'&&start)startRecognition();
    };
    document.querySelectorAll('[data-capture-mode]').forEach(b=>b.onclick=()=>selectMode(b.dataset.captureMode));
    textInput.oninput=()=>{if(saved||saving){if(frozenText!==null)textInput.value=frozenText;return;}if(recognition){stopRecognition();voiceState('Текст изменён. Можно сохранить.','done');}};
    const finishVoice=(focus=false)=>{if(!active(t)||saved||saving||finishing)return;focusWhenEnded=focus;if(!recognition){voiceState('Проверьте текст.','done');if(focus)textInput.focus?.();return;}finishing=true;voiceState('Завершаем диктовку…','finishing');form.querySelector('[type=submit]').disabled=true;try{recognition.stop();}catch{fallback();}};
    $('#r2-voice-stop').onclick=()=>finishVoice();$('#r2-voice-done').onclick=()=>finishVoice(true);
    dictate.addEventListener('click',startRecognition);
    fileInput.onchange=()=>{
      if(!active(t)||saved||saving)return;stopPreview();const file=fileInput.files[0];
      if(!file){preview.innerHTML='<p class="capture-source-empty">Фото или PDF не выбраны.</p>';statusText('Источник ещё не сохранён.','empty');return;}
      if(file.size>20*1024*1024){preview.innerHTML='';statusText('Файл больше 20 MiB; он ещё не сохранён.','failed');return;}
      if(!['image/jpeg','image/png','application/pdf'].includes(file.type)){preview.innerHTML='';statusText('Поддерживаются JPEG, PNG и PDF. Файл ещё не сохранён.','failed');return;}
      const caption=`${file.name} · ${displayQuantity(file.size)} байт`;
      if(file.type==='application/pdf'){
        preview.innerHTML=`<div class="capture-file-tile"><strong>PDF</strong><span>${e(caption)}</span><small>Выбран оригинальный документ. Все страницы сохраняются без изменения.</small></div>`;
        statusText('PDF выбран. Источник ещё не сохранён.','selected');return;
      }
      preview.innerHTML='<p class="capture-source-empty" role="status">Готовим просмотр выбранного фото…</p>';
      const version=readingVersion;reader=new FileReader();
      reader.onload=()=>{if(!active(t)||version!==readingVersion)return;const url=String(reader.result);if(!/^data:image\/(jpeg|png);base64,/.test(url)){if(!saved&&!saving)statusText('Не удалось показать фото. Оригинал ещё не сохранён.','failed');return;}preview.innerHTML=`<img class="capture-preview-image" src="${e(url)}" alt="Выбранное фото: ${e(file.name)}"><p class="capture-preview-caption">${e(caption)}</p>`;if(!saved&&!saving)statusText('Фото выбрано. Источник ещё не сохранён.','selected');};
      reader.onerror=()=>{if(active(t)&&version===readingVersion){preview.innerHTML='<p class="capture-source-empty">Предпросмотр фото недоступен.</p>';if(!saved&&!saving)statusText('Не удалось прочитать фото. Выберите файл ещё раз.','failed');}};
      reader.readAsDataURL(file);
    };
    const freezeCapture=locked=>{for(const control of [textInput,fileInput,dictate,$('#r2-voice-stop'),$('#r2-voice-done'),...document.querySelectorAll('[data-capture-mode]')])control.disabled=locked;form.setAttribute('aria-busy',String(saving));};
    form.onsubmit=async ev=>{
      ev.preventDefault();if(!active(t)||saved||saving||finishing)return;const button=ev.target.querySelector('[type=submit]');if(button.disabled)return;if(recognition){finishVoice(true);return;}button.disabled=true;saving=true;freezeCapture(true);
      try{
        stopRecognition();if(mode==='voice_transcript')voiceState('Текст готов к сохранению','done');const text=form.elements.text.value,file=fileInput.files[0];frozenText=text;if(!text.trim()&&!file)throw Error('Добавьте текст или файл');if(file&&file.size>20*1024*1024)throw Error('Файл больше 20 MiB; он ещё не сохранён');
        statusText('Сохраняем оригинал на телефоне…','saving');
        const body={client_capture_id:crypto.randomUUID(),captured_at:new Date().toISOString(),timezone:'Europe/Istanbul',mode,text:text.trim()?text:null};
        if(file){if(!['image/jpeg','image/png','application/pdf'].includes(file.type))throw Error('Поддерживаются JPEG, PNG и PDF');Object.assign(body,{filename:file.name,mime_type:file.type,file_base64:await fileData(file)});}
        if(!active(t))return;
        await pendingWrite({key:t.scope+':'+body.client_capture_id,scope:t.scope,body});if(!active(t))return;
        saved=true;textInput.value=frozenText;statusText('Сохранено на телефоне.','local');
        form.elements.text.disabled=true;fileInput.disabled=true;dictate.disabled=true;$('#r2-voice-stop').disabled=true;$('#r2-voice-done').disabled=true;document.querySelectorAll('[data-capture-mode]').forEach(b=>b.disabled=true);button.textContent='Сохранено на телефоне';
        onCaptureSynced=id=>{if(id===body.client_capture_id){statusText('Сохранено в системе; действия ещё не подтверждены.','server');button.textContent='Сохранено в системе';}};
        onCaptureError=(id,error)=>{if(id===body.client_capture_id)statusText(sourceUploadMessage(error),error.kind==='validation'?'failed':'pending');};
        if(!navigator.onLine){statusText('Сохранено на телефоне. В системе ещё нет; отправим при восстановлении связи и открытом приложении.','pending');return;}
        statusText('Сохранено на телефоне. Отправляем в систему…','uploading');
        try{
          await sync();if(!active(t))return;
          // The awaited upload may have snapshotted the queue before this
          // capture was committed. Start a fresh pass for its durable record.
          if((await pendingList(t.scope)).some(r=>r.body.client_capture_id===body.client_capture_id&&!r.upload_error)){if(!active(t))return;await sync();}
        }catch(error){statusText(sourceUploadMessage(sourceUploadError(error)),'pending');return;}
        if(!active(t))return;
        // A concurrently running sync may have started before this source was saved.
        const local=(await pendingList(t.scope)).find(r=>r.body.client_capture_id===body.client_capture_id);
        if(local){statusText(local.upload_error?sourceUploadMessage(local.upload_error):'На телефоне. Отправка ожидается; повторите с главного экрана.',local.upload_error?.kind==='validation'?'failed':'pending');return;}
        if(!active(t))return;$('#sheet').close();await reload();if(sameSession(t))pollTimer=setTimeout(()=>{if(sameSession(t))reload().catch(()=>{});},1500);
      }catch(err){statusText(err.message,saved?'pending':'failed');}finally{saving=false;if(active(t))freezeCapture(saved);button.disabled=saved;}
    };
    if(mode==='voice_transcript')startRecognition();else if(mode==='text')textInput.focus?.();
  }
  const card=p=>proposalCard(p,getData());
  async function queue(offset=0,expected=null,remaining=60){
    if(expected&&($('#r2-queue-refresh')!==expected||!$('#sheet').open))return;
    const t=begin();if(!t.scope)return;const r=await api(t.root+`/confirmations?offset=${offset}`);
    if(!active(t))return false;
    if(navigate&&offset===0&&!r.items.length&&!r.preparing){if($('#sheet')?.open)$('#sheet').close();return false;}
    if(!show(t,'Неподтверждённые',`<p class="note">Каждая карточка — отдельное действие. Сохранённый источник ещё не означает оплату, долг или приход.</p>${r.preparing?'<p role="status">Источник сохранён. Готовим карточки; можно записать следующее событие.</p>':''}<div class="owner-queue-list">${r.items.map(card).join('')||(!r.preparing?'<p class="empty">Все карточки разобраны</p>':'')}</div><button id="r2-queue-refresh">Обновить карточки</button>${r.next_offset!==null?`<button id="r2-next">Следующие карточки →</button>`:''}`))return;
    const refresh=$('#r2-queue-refresh');refresh.onclick=guard(()=>{if(active(t))return queue(offset);});
    $('#r2-next')?.addEventListener('click',guard(()=>{if(active(t))return queue(r.next_offset);}));
    document.querySelectorAll('[data-r2-open]').forEach(b=>b.onclick=guard(()=>{if(active(t))return review(b.dataset.r2Open);}));
    document.querySelectorAll('[data-r2-evidence]').forEach(b=>b.onclick=guard(()=>{if(active(t))return review(b.dataset.r2Evidence);}));
    document.querySelectorAll('[data-r2-edit]').forEach(b=>b.onclick=guard(()=>{if(active(t))return review(b.dataset.r2Edit,true);}));
    let deferring=false;
    document.querySelectorAll('[data-r2-later]').forEach(b=>b.onclick=guard(async()=>{
      if(!active(t)||deferring)return;const p=r.items.find(p=>p.id===b.dataset.r2Later);if(!p)return;
      deferring=true;b.disabled=true;
      try{await api(t.root+`/confirmations/${p.id}/defer`,{expected_version:p.version});if(!active(t))return;await reload();if(active(t))await queue(offset);}
      finally{deferring=false;if(active(t))b.disabled=false;}
    }));
    if(r.preparing&&remaining>0)queueTimer=setTimeout(()=>{if(active(t)&&navigator.onLine)queue(offset,refresh,remaining-1).catch(()=>{});},2000);
  }
  async function review(id,editFirst=false){
    const t=begin();if(!t.scope)return;const p=await api(t.root+`/confirmations/${id}/preview`);
    if(!active(t))return;
    if(editFirst){edit(p);return;}
    const cannotConfirm=()=>!!p.revision.missing.length||!navigator.onLine||['POSTED','CONFIRMED_NONPOSTING','REJECTED','FAILED'].includes(p.state)||(p.action_type==='WORKER_TASK'&&!!workerTranslationIssue(p.revision.payload));
    if(!show(t,'Проверить действие',proposalSummary(p,getData(),false)+`<details><summary>Оригинальный источник</summary><p class="source-text">${e(p.source.text||p.source.filename||'Фото / файл')}</p>${p.source.has_file?`<a href="${t.root}/captures/${e(p.source.id)}/original" target="_blank" rel="noopener">Открыть оригинал</a>`:''}</details><div class="actions owner-review-actions"><button id="r2-confirm" class="primary" ${cannotConfirm()?'disabled':''}>Подтвердить</button><button id="r2-edit">Исправить</button><button id="r2-defer">Позже</button><button id="r2-reject" class="danger">Отклонить</button></div>${p.state==='REJECTED'?'<button id="r2-reopen">Восстановить новой проверкой</button>':''}<p id="r2-card-status" role="status"></p>`))return;
    let submitting=false;
    const action=fn=>async()=>{if(!active(t)||submitting)return;submitting=true;document.querySelectorAll('#sheet-content .actions button,#r2-reopen').forEach(b=>b.disabled=true);try{await fn();}catch(err){if(active(t))$('#r2-card-status').textContent=err.message;}finally{submitting=false;if(active(t)){document.querySelectorAll('#sheet-content .actions button,#r2-reopen').forEach(b=>b.disabled=false);$('#r2-confirm').disabled=cannotConfirm();}}};
    $('#r2-confirm').onclick=action(async()=>{if(cannotConfirm())return;const r=await api(t.root+`/confirmations/${id}/confirm`,{expected_version:p.version,preview_digest:p.revision.preview_digest,confirmed:true});if(!active(t))return;if(r.stale){notice('Данные изменились. Нужна новая проверка.');await review(id);}else{$('#sheet').close();notice(r.state==='CONFIRMED_NONPOSTING'?'Подтверждено. Не отправлено работнику / стоимость не изменена.':'Действие подтверждено');await reload();}});
    $('#r2-edit').onclick=()=>{if(active(t)&&!submitting)edit(p);};
    for(const name of ['defer','reject'])$('#r2-'+name).onclick=action(async()=>{await api(t.root+`/confirmations/${id}/${name}`,{expected_version:p.version});if(!active(t))return;await reload();if(active(t))await queue();});
    $('#r2-reopen')?.addEventListener('click',action(async()=>{await api(t.root+`/confirmations/${id}/reopen`,{expected_version:p.version});if(!active(t))return;await reload();if(active(t))await review(id);}));
  }
  function edit(p){
    const t=begin();if(!t.scope)return;
    const f=p.revision.payload,kind=p.action_type,workers=getData().tasks.workers||[],workerLanguage=id=>workers.find(w=>w.id===id)?.language||null,refs=home().finance.references,parties=refs.filter(r=>r.kind==='counterparty'),areas=home().finance.work_areas||[],greenhouses=areas.filter(a=>a.kind==='greenhouse'),inventory=getData().inventory;
    const accounts=currency=>refs.filter(r=>r.kind==='money_account'&&r.currency===currency);
    const debts=(party,currency)=>home().finance.obligations.filter(o=>o.counterparty_id===party&&o.currency===currency&&o.direction==='payable'&&o.remaining_amount!=='0.00').map(o=>({id:o.obligation_id,name:`${o.description||'Долг'} · ${o.remaining_amount} ${o.currency}`}));
    const currencies=()=>select('currency','Валюта',['TRY','USD','EUR','UAH'].map(id=>({id,name:id})),f.currency,'Уточнить');
    let fields='';
    if(['PAYABLE','PAYMENT','DIRECT_EXPENSE'].includes(kind)){
      fields=input('amount','Сумма',f.amount)+currencies()+input('event_date','Дата',f.event_date,'date')+select('counterparty_id','Контрагент',parties,f.counterparty_id,'Не выбран')+'<button type="button" id="r2-party-new">+ Новый</button>'+input('description','За что',f.description)+(kind!=='PAYABLE'?select('money_account_id','Наличные / Банк',accounts(f.currency),f.money_account_id):'')+(kind==='PAYMENT'?select('obligation_id','Погасить долг',debts(f.counterparty_id,f.currency),f.obligation_id):'');
      if(kind==='PAYABLE')fields+=`<fieldset><legend>Точное распределение расхода (необязательно)</legend><small>Пустые суммы оставляют GENERAL. Укажите только известные точные суммы; остаток не распределяется автоматически.</small>${areas.map(a=>input(`attribution_${a.id}`,a.name,(f.attributions||[]).find(x=>x.work_area_id===a.id)?.amount||'')).join('')}</fieldset>`;
      if(kind==='DIRECT_EXPENSE')fields+='<p class="note">Прямой расход остаётся GENERAL. Распределение по теплицам здесь не поддерживается. Для другой структуры используйте существующий ручной режим.</p>';
    }else if(['PERSONAL_TASK','WORKER_TASK'].includes(kind)){
      const isWorker=kind==='WORKER_TASK',chosenWorker=workers.find(w=>w.id===f.worker_id),language=workerLanguage(f.worker_id),translation=language===f.language?f.rendered_text||'':'';
      const lookup=(list,id,empty)=>{const item=list.find(x=>x.id===id);return item?.original_name||item?.name||item?.display_name||(id?'Нужно уточнить':empty);};
      const editableFact=(key,label,value,control,required=false)=>`<details class="task-fact" data-task-fact="${key}" ${required&&(!value||key==='planned_date'&&!f.planned_date)?'open':''}><summary><span>${e(label)}</span><strong data-task-fact-value="${key}">${e(value||'Не выбрано')}</strong></summary>${control}</details>`;
      const intent=isWorker?f.original_instruction||f.title||'':f.title||'';
      const relativeQuestion=isWorker&&!f.planned_date&&f.relative_time_context==='следующая неделя'?'На какой день следующей недели поставить?':'';
      fields=`<section class="task-intent-editor"><label>${isWorker?'Поручение':'Задача'}<textarea name="${isWorker?'original_instruction':'title'}" aria-label="${isWorker?'Поручение':'Задача'}" rows="3" maxlength="${isWorker?'10000':'200'}" required>${e(intent)}</textarea></label>${f.category?`<p class="note task-category">Категория · предложено: <strong>${e(categoryNames[f.category]||f.category)}</strong></p>`:''}${f.relative_time_context?`<p class="task-relative-context">Срок из источника: <strong>${e(f.relative_time_context)}</strong></p>`:''}${relativeQuestion?`<p class="task-date-question" id="r2-task-date-question" role="status">${e(relativeQuestion)}</p>`:''}<div class="task-facts">${editableFact('planned_date','Когда',taskWhenLabel(f,isWorker?'':'Без даты'),input('planned_date','Дата',f.planned_date,'date'),isWorker)}${editableFact('greenhouse_id','Где',lookup(greenhouses,f.greenhouse_id,isWorker?'':'Без теплицы'),select('greenhouse_id',isWorker?'Теплица':'Теплица (необязательно для личной задачи)',greenhouses,f.greenhouse_id),isWorker)}${isWorker?editableFact('worker_id','Кому',chosenWorker?.display_name||chosenWorker?.name||'',select('worker_id','Работник',workers,f.worker_id),true):''}</div></section>`;
      if(isWorker)fields+=`<section class="task-translation"><p id="r2-worker-language" class="task-language">Язык работника: ${e(languageNames[language]||'Не определён')}</p><div id="r2-translation-field" ${language==='ru'?'hidden':''}><label>Проверенный перевод<textarea name="rendered_text" rows="3" maxlength="10000">${e(translation)}</textarea></label><p id="r2-translation-status" class="bad" role="status">${e(workerTranslationIssue({...f,language,rendered_text:translation}))}</p></div><small>Язык из карточки работника. Автоматического перевода нет. Создаётся черновик, поручение не отправляется.</small></section>`;
      fields+=`<details class="task-extra"><summary>Дополнительно</summary>${isWorker?input('title','Короткое название',f.title):''}<label>Примечание<textarea name="note" rows="2" maxlength="2000">${e(f.note||'')}</textarea></label></details><details class="task-original"><summary>Оригинальный источник</summary><p class="source-text">${e(p.source?.text||f.original_instruction||f.title||'Текст не указан')}</p></details>`;
    }else if(['STOCK_RECEIPT','STOCK_APPLICATION'].includes(kind)){
      fields=input('event_date','Дата',f.event_date,'date');
      if(kind==='STOCK_RECEIPT'&&p.revision.basis.duplicate_source_receipts?.length&&!p.source?.has_file)fields+=`<label><input name="separate_event_reviewed" type="checkbox" ${f.separate_event_reviewed?'checked':''}> Это отдельный физический приход, не повтор прежнего</label>`;
      fields+=f.lines.map((l,i)=>`<fieldset><legend>${e(l.original_product_name)}</legend>${select(`product_${i}`,'Товар',inventory.products,l.product_id)}${input(`quantity_${i}`,'Количество',l.quantity)}${select(`location_${i}`,'Склад',inventory.locations,l.location_id)}${select(`unit_${i}`,'Единица',['kg','L','piece','bag','container','package'].map(id=>({id,name:id})),l.unit,'Уточнить')}${kind==='STOCK_APPLICATION'?select(`greenhouse_${i}`,'Теплица внесения',greenhouses,l.greenhouse_id)+select(`work_area_${i}`,'Участок внесения (необязательно)',areas.filter(a=>a.kind!=='greenhouse'),l.work_area_id):''}<small>Неизвестная стоимость остаётся UNKNOWN, не 0.</small></fieldset>`).join('');
    }else if(kind==='DEPOT_RECEIPT'){
      fields=input('event_date','Дата чека',f.event_date,'date')+currencies()+select('depot_id','Депо',parties,f.depot_id);
      if(!p.source?.has_file&&(p.revision.basis.confirmed_source_receipts?.length||p.revision.basis.existing_receipts?.some(r=>r.status==='posted')))fields+=`<label><input name="separate_event_reviewed" type="checkbox" ${f.separate_event_reviewed?'checked':''}> Это отдельный чек по текстовому источнику, не повтор прежнего</label>`;
      const batches=(p.revision.basis.same_day_batches||[]).map(b=>({id:b.id,name:b.label}));
      fields+=`<fieldset><legend>Связь с партиями того же дня</legend><small>Выберите по исходному отчёту. Совпадение даты или веса само по себе не подтверждает связь.</small>${batches.map(b=>`<label><input type="checkbox" name="batch_${e(b.id)}" ${f.batch_ids?.includes(b.id)?'checked':''}> ${e(b.name)}</label>`).join('')||'<p>Партии того же дня не найдены.</p>'}${input('link_evidence','Основание связи с партиями',f.link_evidence)}</fieldset>`;
      fields+=f.lines.map((l,i)=>`<fieldset><legend>${e(l.original_quality_name)} · ${e(l.source_line)}</legend>${input(`kg_${i}`,'кг',l.net_weight_kg)}${input(`price_${i}`,'Цена за кг',l.unit_price)}${select(`quality_${i}`,'Качество именно этого депо',Object.entries(qualityNames).map(([id,name])=>({id,name})),f.qualities[i],'Уточнить')}</fieldset>`).join('');
      fields+='<label><input name="quality_reviewed" type="checkbox"> Подтверждаю качества строк именно этого депо</label><label><input name="remember_quality_mapping" type="checkbox"> Запомнить проверенные названия только для этого депо</label><label><input name="arithmetic_reviewed" type="checkbox"> Проверил строки и сумму; Alacak не означает оплату</label>';
    }else if(kind==='INVOICE_MATCH')fields=select('receipt_id','Уже подтверждённый физический приход',inventory.events?.filter(r=>r.kind==='receipt'&&r.status==='posted').map(r=>({id:r.id,name:displayDate(r.event_date)+' · '+r.payload.lines.map(l=>l.original_product_name).join(', ')}))||[],f.receipt_id);
    else fields='<p>Автоматическая подготовка не определила событие. Оригинал сохранён; выберите «Ввести вручную».</p>';
    if(!show(t,'Исправить карточку',`<form id="r2-edit-form" class="r2-fields">${fields}<button class="primary" type="submit">Сохранить исправления</button></form><button id="r2-advanced">Ввести вручную</button><p id="r2-edit-status" role="status"></p>`))return;
    const form=$('#r2-edit-form'),status=$('#r2-edit-status');
    const payloadFromForm=()=>{
      const values=Object.fromEntries(new FormData(form)),payload={...f};
      for(const [k,v] of Object.entries(values))if(k in payload&&!['lines','qualities','attributions','quality_reviewed','remember_quality_mapping','arithmetic_reviewed','separate_event_reviewed'].includes(k))payload[k]=v||null;
      if(['PERSONAL_TASK','WORKER_TASK'].includes(kind))payload.note=values.note||null;
      if(kind==='WORKER_TASK')Object.assign(payload,workerProposal({...payload,title:values.title||null,original_instruction:values.original_instruction||null,rendered_text:values.rendered_text||null},workers));
      if(kind==='PAYABLE')payload.attributions=areas.filter(a=>values[`attribution_${a.id}`]?.trim()).map(a=>({work_area_id:a.id,amount:values[`attribution_${a.id}`].trim()}));
      if(kind==='PAYMENT'){payload.payment_purpose='debt';payload.obligation_id=values.obligation_id||null;}
      if(['STOCK_RECEIPT','DEPOT_RECEIPT'].includes(kind)&&form.elements.separate_event_reviewed)payload.separate_event_reviewed=values.separate_event_reviewed==='on';
      if(kind.startsWith('STOCK_'))payload.lines=f.lines.map((l,i)=>({...l,product_id:values[`product_${i}`]||null,quantity:values[`quantity_${i}`]||null,location_id:values[`location_${i}`]||null,unit:values[`unit_${i}`]||null,...(kind==='STOCK_APPLICATION'?{greenhouse_id:values[`greenhouse_${i}`]||null,work_area_id:values[`work_area_${i}`]||null}:{})}));
      if(kind==='DEPOT_RECEIPT'){payload.batch_ids=(p.revision.basis.same_day_batches||[]).filter(b=>values[`batch_${b.id}`]==='on').map(b=>b.id);payload.link_evidence=values.link_evidence||null;payload.lines=f.lines.map((l,i)=>({...l,net_weight_kg:values[`kg_${i}`],unit_price:values[`price_${i}`]}));payload.qualities=f.lines.map((_,i)=>values[`quality_${i}`]||null);payload.quality_reviewed=values.quality_reviewed==='on';payload.remember_quality_mapping=values.remember_quality_mapping==='on';payload.arithmetic_reviewed=values.arithmetic_reviewed==='on';}
      return payload;
    };
    const updateMoneyOptions=()=>{if(!active(t))return;const currency=form.elements.currency.value,party=form.elements.counterparty_id.value,account=form.elements.money_account_id,debt=form.elements.obligation_id;if(account){const previous=account.value;account.innerHTML=options(accounts(currency),previous);}if(debt){const previous=debt.value;debt.innerHTML=options(debts(party,currency),previous);}};
    if(['PAYABLE','PAYMENT','DIRECT_EXPENSE'].includes(kind)){form.elements.currency.onchange=updateMoneyOptions;form.elements.counterparty_id.onchange=updateMoneyOptions;}
    if(['PERSONAL_TASK','WORKER_TASK'].includes(kind)){
      const valueNode=key=>document.querySelector(`[data-task-fact-value="${key}"]`);
      const updateFact=(key,value)=>{const node=valueNode(key);if(node)node.textContent=value;};
      for(const key of ['planned_date','greenhouse_id','worker_id']){
        const control=form.elements[key];if(!control)continue;
        control.onchange=()=>{if(!active(t))return;const list=key==='worker_id'?workers:greenhouses,item=list.find(x=>x.id===control.value);updateFact(key,key==='planned_date'?taskWhenLabel({...f,planned_date:control.value},kind==='WORKER_TASK'?'Не выбрано':'Без даты'):item?.display_name||item?.original_name||item?.name||(kind==='PERSONAL_TASK'?'Без теплицы':'Не выбрано'));if(key==='planned_date'&&$('#r2-task-date-question'))$('#r2-task-date-question').hidden=!!control.value;if(kind==='WORKER_TASK')invalidateTranslation();};
      }
      const refreshTranslation=()=>{
        if(kind!=='WORKER_TASK'||!active(t))return;
        const language=workerLanguage(form.elements.worker_id.value),translated=form.elements.rendered_text;
        $('#r2-worker-language').textContent='Язык работника: '+(languageNames[language]||'Не определён');$('#r2-translation-field').hidden=language==='ru';
        $('#r2-translation-status').textContent=workerTranslationIssue({language,original_instruction:form.elements.original_instruction.value,rendered_text:translated.value});
      };
      const invalidateTranslation=()=>{if(kind==='WORKER_TASK'){form.elements.rendered_text.value='';refreshTranslation();}};
      if(kind==='WORKER_TASK'){
        let titleEdited=false;const wasAutomatic=!f.title||f.title===String(f.original_instruction||'').slice(0,200);
        form.elements.title.oninput=()=>{titleEdited=true;};
        form.elements.original_instruction.oninput=()=>{if(!active(t))return;if(wasAutomatic&&!titleEdited)form.elements.title.value=conciseProposalText(form.elements.original_instruction.value,200);invalidateTranslation();};
        form.elements.rendered_text.oninput=refreshTranslation;
      }
    }
    $('#r2-advanced').onclick=()=>{if(active(t)){$('#sheet').close();invalidate();advanced(kind);}};
    $('#r2-party-new')?.addEventListener('click',()=>{
      if(!active(t))return;const draft=payloadFromForm(),partyView=begin();
      show(partyView,'Новый контрагент','<form id="r2-new-party"><label>Название<input name="name" required maxlength="250"></label><button class="primary">Готово</button></form><p id="r2-party-status" role="status"></p>');
      $('#r2-new-party').onsubmit=async ev=>{ev.preventDefault();if(!active(partyView))return;const b=ev.target.querySelector('button');if(b.disabled)return;b.disabled=true;try{const r=await api(partyView.root+'/counterparties',{name:ev.target.elements.name.value,confirmed:true});if(!active(partyView))return;await reload();if(active(partyView))edit({...p,revision:{...p.revision,payload:{...draft,counterparty_id:r.id}}});}catch(err){if(active(partyView))$('#r2-party-status').textContent=err.message;}finally{b.disabled=false;}};
    });
    form.onsubmit=async ev=>{ev.preventDefault();if(!active(t))return;const b=ev.target.querySelector('[type=submit]');if(b.disabled)return;b.disabled=true;try{const payload=payloadFromForm();await api(t.root+`/confirmations/${p.id}/revise`,{expected_version:p.version,payload});if(!active(t))return;await reload();if(active(t))await review(p.id);}catch(err){if(active(t))status.textContent=err.message;}finally{b.disabled=false;}};
  }
  window.addEventListener('online',guard(sync));document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')sync().catch(()=>{});});
  function logout(){sessionGeneration++;invalidate();clearTimeout(pollTimer);clearTimeout(queueTimer);if($('#owner-capture-bar'))$('#owner-capture-bar').hidden=true;document.body.classList.remove('has-owner-capture');}
  return {render,bind,mount,capture,queue,sync,logout,localSources,invalidateView:invalidate};
}
