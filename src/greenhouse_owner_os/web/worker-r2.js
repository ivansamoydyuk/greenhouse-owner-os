import {escape as e} from './core.js';
export const R2_CONTRACT='R2_RECEIVED_REPORT';
export const responseLabels={ru:{RECEIVED:'Получено',DONE:'Сделано',PROBLEM:'Проблема'},tr:{RECEIVED:'Aldım',DONE:'Tamamlandı',PROBLEM:'Sorun'},fa:{RECEIVED:'دریافت کردم',DONE:'انجام شد',PROBLEM:'مشکل'},uz:{RECEIVED:'Qabul qildim',DONE:'Bajarildi',PROBLEM:'Muammo'}};
export const responseKey=(worker,task,assignment)=>JSON.stringify(['worker-r2',worker.entity_id||task.entity_id,worker.id,task.id,assignment.id,task.cycle]);
export function responseSnapshot(task,assignment,response,language,comment='',id=crypto.randomUUID(),capturedAt=new Date().toISOString()){
 if(task.execution_contract!==R2_CONTRACT||assignment.cycle!==task.cycle)throw Error('Назначение изменилось. Обновите поручение.');
 const button=responseLabels[language]?.[response];if(!button)throw Error('Неизвестное действие или язык.');
 if(response==='DONE'&&!assignment.acknowledged_at)throw Error('Сначала подтвердите получение поручения.');
 return {client_response_id:id,assignment_id:assignment.id,expected_cycle:task.cycle,response,captured_at:capturedAt,button_label:button,original_language:language,comment};
}
export function pendingResponse(storage,key,task,assignment){
 const raw=storage.getItem(key);if(!raw)return null;let body;
 try{body=JSON.parse(raw);}catch{throw Error('Сохранённый ответ повреждён. Обновите поручение и проверьте историю.');}
 if(body.assignment_id!==assignment.id||body.expected_cycle!==task.cycle||!responseLabels[body.original_language]?.[body.response])throw Error('Сохранённый ответ относится к другой версии поручения.');
 return body;
}
export function responseAlreadyStored(task,body){return (task.response_history||[]).some(x=>x.client_response_id===body.client_response_id&&x.assignment_id===body.assignment_id&&x.cycle===body.expected_cycle);}
export function r2Controls(task,assignment,language,pending,comment=''){
 const recovery=language==='fa'?{pending:'پاسخ در انتظار بررسی سرور است',retry:'تلاش دوباره با همین پاسخ',refresh:'بررسی نتیجه',hold:'تا بررسی نتیجه این پاسخ را تغییر ندهید.',first:'ابتدا دریافت را تأیید کنید؛ سپس انجام شد. مشکل را می‌توانید فوراً گزارش دهید.'}:{pending:'Ответ ожидает проверки сервера',retry:'Повторить тот же ответ',refresh:'Проверить результат',hold:'Не меняйте этот ответ до проверки результата.',first:'Сначала «Получено», затем «Сделано». О проблеме можно сообщить сразу.'};
 const labels=responseLabels[language]||responseLabels.ru,live=!['completed','cancelled'].includes(task.status);
 const done=(task.reports||[]).some(r=>r.assignment_id===assignment.id&&r.cycle===task.cycle&&r.state==='submitted'&&r.claim==='done');
 if(pending)return `<section class="card worker-pending"><strong>${e(recovery.pending)}</strong><p>${e(pending.button_label)}</p><p dir="auto">${e(pending.comment)}</p><p>${e(recovery.hold)}</p><button id="worker-r2-retry" class="primary">${e(recovery.retry)}</button><button id="worker-r2-refresh">${e(recovery.refresh)}</button></section>`;
 if(!live)return '<p class="note">Решение владельца сохранено. История ответов остаётся доступна.</p>';
 return `<label for="worker-r2-comment">Комментарий · необязательно / یادداشت<textarea id="worker-r2-comment" dir="auto" rows="3" maxlength="10000">${e(comment)}</textarea></label><div class="actions worker-r2-actions"><button id="worker-r2-received" data-r2-response="RECEIVED" ${assignment.acknowledged_at?'disabled':''}>${e(labels.RECEIVED)}</button><button id="worker-r2-done" data-r2-response="DONE" class="primary" ${!assignment.acknowledged_at||done?'disabled':''}>${e(labels.DONE)}</button><button id="worker-r2-problem" data-r2-response="PROBLEM">${e(labels.PROBLEM)}</button></div><p class="note">${!assignment.acknowledged_at?e(recovery.first):'Получение сохранено. Результат проверит владелец.'}</p><p class="note">Отчёт не списывает склад и не завершает проверку владельца.</p>`;
}
export function r2History(task){
 return `<details class="worker-response-history"><summary>История ответов / گزارش‌ها</summary>${(task.response_history||[]).map(x=>`<article class="card"><strong>${e(x.button_label)}</strong><small>Цикл ${e(x.cycle)} · ${e(x.captured_at)}</small>${x.comment?`<p dir="auto">${e(x.comment)}</p>`:''}<p class="note">Выбор кнопки сохранён как UI_ACTION. ${x.cycle!==task.cycle?'Предыдущий цикл; не подтверждает текущую работу.':''}</p></article>`).join('')||'<p>Ответов пока нет.</p>'}</details>`;
}
