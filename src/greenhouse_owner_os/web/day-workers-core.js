/** Exact original form evidence. Business validation and totals belong to the server. */
export const dayFieldNames = Object.freeze({
  day_request: ['people','days','work','start_date','note'],
  day_decision: ['decision','people','days','work','start_date','note','reason'],
  day_clarification: ['text'],
  day_actual: ['event_date','people','work','full_day','note','allocations'],
  day_confirmation: ['note'],
  day_accrual: ['counterparty_id','daily_rate','currency','expense_account_id','rate_basis','due_date'],
  day_scope: ['worker_id','greenhouse_id','action','reason'],
});
export function daySource(kind,draft,fields) {
  if(!dayFieldNames[kind]||!draft?.id||!draft?.captured_at)throw Error('Не подтверждена идентичность исходной формы.');
  return {schema_version:1,form_kind:kind,form_id:draft.id,captured_at:draft.captured_at,timezone:'Europe/Istanbul',fields:Object.fromEntries(dayFieldNames[kind].map(key=>[key,String(fields[key]??'')]))};
}
export function dayDraft(id,captured_at,fields={}) { return {id,captured_at,...fields}; }
export function dayStored(result,sourceId) {
  if(!sourceId)return false;
  const source=x=>x?.id===sourceId||x?.source?.form_id===sourceId||x?.source_id===sourceId||x?.original?.form_id===sourceId;
  return source(result)||(result?.requests||[]).some(source)||(result?.actuals||[]).some(source)||(result?.decisions||[]).some(source)||(result?.clarifications||[]).some(source);
}
export const dayLabels = Object.freeze({
  ru: {title:'Дневные рабочие',requests:'Мои заявки',newRequest:'Нужны работники',greenhouse:'Теплица',choose:'Выберите теплицу',people:'Сколько человек, включая старшего',days:'На сколько дней',work:'Какая работа',optional:'Дата и примечание · необязательно',date:'Дата начала · если известна',note:'Примечание',unknownDate:'Дата не указана',send:'Отправить заявку',back:'Назад',saved:'Сохранено на сервере',plan:'План',actual:'Фактически по дням',newActual:'Записать фактическую работу',actualDate:'День работы',actualPeople:'Сколько человек работало, включая старшего',fullDay:'Работали полный день?',yes:'Да, полный день',no:'Нет, неполный день',unknown:'Неизвестно',saveActual:'Отправить факт',answer:'Ответ владельца',clarify:'Ответить на уточнение',clarification:'Уточнение',sendClarification:'Отправить уточнение',empty:'Заявок пока нет',noScope:'Владелец ещё не подтвердил вашу теплицу. Попросите его проверить назначение.',refresh:'Обновить',retry:'Повторить ту же отправку',pending:'Ответ сервера не получен. Проверьте список или повторите ту же отправку. Исходные поля сохранены.',review:'Статические подписи на дари требуют проверки носителем. Текст работника и ответ владельца показаны в оригинале.',noPlan:'Без плановой заявки',personDays:'человеко-дней',sent:'Отправлено',approved:'Одобрено',rejected:'Отклонено',needsClarification:'Нужно уточнение',revised:'Одобрено с изменениями',reportOnly:'Это сообщение о факте. Денежные суммы отдельно проверяет владелец.'},
  fa: {title:'کار روزمزد',requests:'درخواست‌های من',newRequest:'به کارگر نیاز است',greenhouse:'گلخانه',choose:'گلخانه را انتخاب کنید',people:'تعداد نفرها، به شمول سرگروه',days:'برای چند روز',work:'چه کاری',optional:'تاریخ و یادداشت اختیاری',date:'تاریخ آغاز، اگر معلوم است',note:'یادداشت',unknownDate:'تاریخ مشخص نیست',send:'فرستادن درخواست',back:'برگشت',saved:'در سرور ذخیره شد',plan:'برنامه',actual:'کار انجام‌شده در هر روز',newActual:'ثبت کار انجام‌شده',actualDate:'روز کار',actualPeople:'تعداد نفرهایی که کار کردند، به شمول سرگروه',fullDay:'تمام روز کار کردند؟',yes:'بلی، تمام روز',no:'نخیر، بخشی از روز',unknown:'معلوم نیست',saveActual:'فرستادن گزارش کار',answer:'پاسخ مالک',clarify:'پاسخ به پرسش',clarification:'توضیح',sendClarification:'فرستادن توضیح',empty:'هنوز درخواستی نیست',noScope:'مالک هنوز گلخانهٔ شما را تأیید نکرده است. از او بخواهید که تعیین گلخانه را بررسی کند.',refresh:'تازه کردن',retry:'فرستادن دوبارهٔ همان گزارش',pending:'پاسخ سرور دریافت نشد. فهرست را بررسی کنید یا همان گزارش را دوباره بفرستید. معلومات اصلی محفوظ است.',review:'متن‌های ثابت دری باید توسط کسی که زبان مادری‌اش دری است بررسی شوند. متن کارگر و پاسخ مالک به زبان اصلی نشان داده می‌شوند.',noPlan:'بدون درخواست قبلی',personDays:'نفر-روز',sent:'فرستاده شد',approved:'تأیید شد',rejected:'رد شد',needsClarification:'توضیح لازم است',revised:'با تغییر تأیید شد',reportOnly:'این گزارش کار انجام‌شده است. مالک مبلغ پول را جداگانه بررسی می‌کند.'},
});
export const dayText=language=>dayLabels[language]||dayLabels.ru;
export function dayState(state,language='ru') {
  const t=dayText(language);return ({SUBMITTED:t.sent,PENDING:t.sent,APPROVED:t.approved,REVISE_APPROVED:t.revised,REVISED_APPROVED:t.revised,REJECTED:t.rejected,CLARIFICATION_REQUIRED:t.needsClarification,NEEDS_CLARIFICATION:t.needsClarification})[state]||state;
}

export function dayAllocations(fields,greenhouseId,greenhouses,workAreas=[]) {
  if(/^0{1,6}$/.test(String(fields.people)))return []; // Explicit no-work day, never a fabricated allocation.
  const integer=value=>{if(!/^\d{1,6}$/.test(String(value))||Number(value)<1)throw Error('Укажите положительное целое число людей.');return Number(value);};
  const part=id=>{const value=fields['part_id_'+id]||null;if(value&&!workAreas.some(p=>p.id===value&&p.parent_id===id))throw Error('Часть не принадлежит выбранной теплице.');return value;};
  if(fields.allocation_mode==='unknown')return [];
  if(fields.allocation_mode==='same'){if(!greenhouses.some(g=>g.id===greenhouseId))throw Error('Не подтверждена выбранная теплица.');return [{greenhouse_id:greenhouseId,work_area_id:part(greenhouseId),people:integer(fields.people)}];}
  if(fields.allocation_mode==='split'){
    const rows=greenhouses.filter(g=>String(fields['allocation_'+g.id]??'').trim()).map(g=>({greenhouse_id:g.id,work_area_id:part(g.id),people:integer(fields['allocation_'+g.id])}));
    if(!rows.length||rows.reduce((n,r)=>n+r.people,0)>integer(fields.people))throw Error('Распределение должно быть положительным и не превышать общее число людей.');
    return rows;
  }
  throw Error('Укажите, где фактически работала бригада, или выберите неизвестное распределение.');
}

/** Reuse short, already visible work descriptions; this is not a new work taxonomy. */
export function dayWorkChoices(requests,greenhouseId){
 const choices=[];
 for(const request of requests||[]){if(request.greenhouse_id!==greenhouseId)continue;const work=request.current_plan?.work??request.original?.fields?.work;if(typeof work!=='string'||!work.trim()||work.length>120||/[\r\n]/.test(work)||choices.includes(work))continue;choices.push(work);if(choices.length===6)break;}
 return choices;
}
