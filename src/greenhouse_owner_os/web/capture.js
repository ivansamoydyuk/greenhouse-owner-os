import {escape as e, money, exact} from './core.js';

// Browser speech is an optional adapter. Every transcript is editable evidence;
// it goes through the same server extraction/review path as typed text.
export function globalCapture({api, openSheet, ask, reload, detail, getData, getEntity, notice, harvestReview}) {
  let recognition;
  const $=s=>document.querySelector(s);
  const input=(id,label,value='',type='text')=>`<label for="${id}">${label}</label><input id="${id}" name="${id}" type="${type}" value="${e(value)}">`;
  const select=(id,label,rows,value)=>`<label for="${id}">${label}</label><select id="${id}" name="${id}"><option value="">Выберите…</option>${rows.map(([v,n])=>`<option value="${e(v)}" ${v===value?'selected':''}>${e(n)}</option>`).join('')}</select>`;
  function open() {
    openSheet('Записать событие',`<p class="note">Напишите или продиктуйте, что произошло. Сначала проверка, затем черновик. Деньги без подтверждения не меняются.</p><form id="capture-input"><label for="capture-text">Что произошло?</label><textarea id="capture-text" rows="5" maxlength="10000" placeholder="Серкану должен 8000 лир за ремонт насоса в Имамлы"></textarea><div class="actions"><button id="voice-start" type="button" class="secondary">🎙 Диктовать</button><button id="voice-stop" type="button" class="quiet" hidden>Остановить</button></div><p id="voice-status" class="note" role="status"></p><div class="form-error" id="capture-error" role="alert"></div><button class="primary">Разобрать и проверить</button></form>`);
    let source='text';
    const Speech=window.SpeechRecognition||window.webkitSpeechRecognition;
    $('#voice-status').textContent=Speech?'Распознавание выполняет браузер. Для чувствительных данных используйте текст; качество зависит от устройства и сети.':'В этом браузере распознавание недоступно. Можно использовать диктовку клавиатуры телефона или написать текст.';
    $('#voice-start').disabled=!Speech;
    $('#voice-start').onclick=()=>{
      if(recognition)recognition.abort();
      recognition=new Speech();recognition.lang='ru-RU';recognition.interimResults=false;recognition.continuous=false;
      const status=$('#voice-status'),text=$('#capture-text');
      recognition.onresult=ev=>{const transcript=Array.from(ev.results).filter(r=>r.isFinal).map(r=>r[0].transcript).join(' ');if(transcript){text.value+=(text.value?' ':'')+transcript;source='voice_transcript';status.textContent='Расшифровка готова. Проверьте текст, особенно суммы и имена.';}};
      recognition.onerror=ev=>{status.textContent=ev.error==='not-allowed'?'Доступ к микрофону не разрешён. Используйте текст или разрешите микрофон в браузере.':'Не удалось распознать голос. Можно повторить или написать текст.';};
      recognition.onend=()=>{if($('#voice-start')){$('#voice-start').disabled=false;$('#voice-stop').hidden=true;}};
      recognition.start();$('#voice-start').disabled=true;$('#voice-stop').hidden=false;status.textContent='Слушаю…';
    };
    $('#voice-stop').onclick=()=>recognition?.stop();
    $('#sheet').addEventListener('close',()=>recognition?.abort(),{once:true});
    $('#capture-input').onsubmit=async ev=>{
      ev.preventDefault();const text=$('#capture-text').value.trim();if(!text){$('#capture-error').textContent='Напишите событие или продиктуйте его.';return;}
      const button=ev.target.querySelector('button.primary');button.disabled=true;
      try{recognition?.abort();const r=await api(`/v1/entities/${getEntity()}/captures`,{text,source});await reload();review(r);}catch(err){if($('#capture-error'))$('#capture-error').textContent=err.message;}finally{button.disabled=false;}
    };
  }
  async function read(id){review(await api(`/v1/entities/${getEntity()}/captures/${id}`));}
  function review(r) {
    const data=getData(),f=r.extracted,parties=data.references.filter(x=>x.kind==='counterparty'),accounts=data.references.filter(x=>x.kind==='money_account');
    const gaps=r.gaps.map(g=>`<li>${e(g)}</li>`).join('');
    if(r.route==='HARVEST_DEPOT'){harvestReview(r);return;}
    if(r.state==='LINKED'){detail(r.finance_kind,r.finance_id);return;}
    if(r.route!=='FINANCE'){
      openSheet('Требует внимания',`<blockquote>${e(r.original_text)}</blockquote><ul class="gaps">${gaps||'<li>Для этого события ещё нет проверенного маршрута. Оно сохранено для проверки.</li>'}</ul><p class="note">Продажу бананов должен подтвердить расчёт депо: чек → качества / кг / цена → расчёт → Finance. Сообщение не создаёт выручку, долг или деньги.</p>`);return;
    }
    let html=select('kind','Событие',[['obligation','Долг'],['payment','Оплата / аванс']],f.kind)+select('direction','Направление',f.kind==='payment'?[['out','Оплатил'],['in','Получил']]:[['payable','Мы должны'],['receivable','Нам должны (не продажа бананов)']],f.direction);
    html+=input('amount','Сумма',f.amount||'')+select('currency','Валюта',[['TRY','TRY · лиры'],['USD','USD · доллары'],['EUR','EUR · евро']],f.currency)+select('counterparty_id','Контрагент',parties.map(p=>[p.id,p.original_name]),f.counterparty_id);
    html+=input('event_date','Дата события',f.event_date||'','date')+input('due_date','Ожидаемая дата оплаты (если согласована)',f.due_date||'','date');
    if(f.kind==='obligation')html+=select('obligation_class','Тип долга',[['OPERATIONAL','Операционный'],['STRATEGIC','Стратегический · основная сумма']],f.obligation_class)+select('principal_received','Деньги по стратегическому долгу',[['false','Только признаю непогашенный долг'],['true','Деньги получены на указанный счёт']],String(f.principal_received||false));
    if(r.inbox&&f.kind==='obligation')html+=select('offset_ledger_account_id','Экономическое основание',data.references.filter(a=>a.kind==='ledger_account'&&['expense','revenue','financing_principal'].includes(a.account_class)).map(a=>[a.id,a.original_name+' · '+a.currency]),f.offset_ledger_account_id);
    if(f.kind==='payment'||f.obligation_class==='STRATEGIC')html+=select('money_account_id','Счёт / касса',accounts.map(a=>[a.id,a.original_name+' · '+a.currency]),f.money_account_id);
    if(f.kind==='obligation')html+=`<div class="wide"><h3>Место события · явное распределение</h3>${data.work_areas.map(a=>input('area-'+a.id,a.name+' · сумма',(f.attributions||[]).find(x=>x.work_area_id===a.id)?.amount||'')).join('')}<p class="note">Пусто — место неизвестно. Остаток не распределяется автоматически.</p></div>`;
    if(f.kind==='payment')html+=`<div class="wide"><h3>Погасить долги (пусто — аванс)</h3>${data.obligations.filter(o=>['open','partial'].includes(o.settlement_status)).map(o=>input('allocation-'+o.obligation_id,(parties.find(p=>p.id===o.counterparty_id)?.original_name||'Контрагент')+' · '+money(o.remaining_amount,o.currency),(f.allocations||[]).find(a=>a.obligation_id===o.obligation_id)?.amount||'')).join('')}</div>`;
    openSheet('Проверить сообщение',`<blockquote>${e(r.original_text)}</blockquote><p class="note">Исходный ${r.source==='voice_transcript'?'голосовой текст':'текст'} сохранён. Проверьте все поля; подсказки не являются подтверждёнными фактами.</p>${gaps?`<ul class="gaps">${gaps}</ul>`:''}<form id="capture-review"><div class="fields">${html}${input('description','Основание / комментарий',f.description||r.original_text)}</div>${r.gaps.some(g=>g.includes('Похожее'))?'<label><input type="checkbox" id="duplicate-reviewed" required> Проверил: это отдельное событие, не дубль</label>':''}<div class="form-error" id="capture-error" role="alert"></div><p class="note">Будет создан только финансовый черновик. Проведение — отдельное подтверждение.</p><button class="primary">Подтвердить данные и создать черновик</button></form>`);
    $('#kind').disabled=true;$('#direction').disabled=true;
    $('#capture-review').onsubmit=async ev=>{
      ev.preventDefault();const v=Object.fromEntries(new FormData(ev.target));let amount;try{amount=exact(v.amount);}catch(err){$('#capture-error').textContent=err.message;return;}const reviewed={kind:f.kind,direction:f.direction,amount,currency:v.currency,counterparty_id:v.counterparty_id,event_date:v.event_date};
      if(f.kind==='obligation')Object.assign(reviewed,{obligation_class:v.obligation_class,principal_received:v.principal_received==='true',description:v.description,due_date:v.due_date||null,attributions:data.work_areas.map(a=>({work_area_id:a.id,amount:v['area-'+a.id]})).filter(a=>a.amount?.trim())});
      if(v.money_account_id)reviewed.money_account_id=v.money_account_id;
      if(r.inbox&&v.offset_ledger_account_id)reviewed.offset_ledger_account_id=v.offset_ledger_account_id;
      if(f.kind==='payment')reviewed.allocations=data.obligations.map(o=>({obligation_id:o.obligation_id,amount:v['allocation-'+o.obligation_id]})).filter(a=>a.amount?.trim());
      if($('#duplicate-reviewed'))reviewed.duplicate_reviewed=$('#duplicate-reviewed').checked;
      if(!await ask('Создать черновик по проверенным данным?',`<p>${e(v.amount)} ${e(v.currency)}</p><p>Дата события: ${e(v.event_date)}</p><p>Без проведения и движения денег.</p>`))return;
      const button=ev.target.querySelector('button.primary');button.disabled=true;
      try{const result=await api(`/v1/entities/${getEntity()}/captures/${r.id}/confirm`,{expected_version:r.version,reviewed,confirmed:true});await reload();await detail(result.capture.finance_kind,result.finance.id);notice('Черновик создан. Проверьте его перед проведением.');}catch(err){if($('#capture-error'))$('#capture-error').textContent=err.message;}finally{button.disabled=false;}
    };
  }
  return {open,read,review};
}
