"""Pure production preparation adapter for the existing R2 review DTOs.

Source text is immutable; every output is a proposed, independently reviewed
slot. No posting, record creation, debt selection, translation or stock cost.
"""
import re
from datetime import date
from decimal import Decimal
from .preparation_language import (corrected,date_value,entity_matches,fold,money_amount,
                                  source_currency,value,names,comparison_source,nonfinancial_modal,meta_or_report,NUMBER,CURRENCY)
from .tasks_capture import extract_task,task_audience
from .preparation_scope import (PAID,OWED,PURCHASE,financial_evidence_view,other_financial_actor,
                                split_outside_quotes,reported_speech,future_financial_intent,enumerated_fragments)
from .inventory_capture import extract_inventory

INTERPRETATION_VERSION='owner-language-preparation-r2-v2'

INVOICE=r'\b(?:fatura|invoice|счет\s+поставщика)\b'
UNSUPPORTED=r'\b(?:аванс\w*|предоплат\w*|стратегическ\w*|финансирован\w*|кредит(?:а|у|ом|е|ы|ов|ный|ная|ное)?|заем\w*|займ\w*|обменял|обменяли|перевод\s+между|перевел\s+между|между\s+своими\s+счетами|внес\s+свои|переместил\w*)\b|\b(?:получил|получили)\s+(?:деньги|оплату)|\b(?:должен|должны)\s+мне\b'
UNCERTAIN=r'\b(?:наверное|возможно|просит|попросил|предлагает|может\s+быть|сколько\s+я\s+должен)\b'
NEGATED_MONEY=r'\bне\s+(?:заплатил\w*|оплатил\w*|платил\w*|выдал\w*|выплатил\w*|перевел\w*|отдал\w*|должен|должна|должны|купил\w*|потратил\w*|погасил\w*|закрыл\w*)\b'


def unresolved(raw):
    return ('UNRESOLVED',dict(note=raw[:2000] or 'Текст не распознан. Оригинал сохранён.'),{})


def unresolved_parts(raw):
    # Every unsupported fragment remains reviewable within the existing DTO
    # bound, even when a long source cannot fit into one note.
    return [unresolved(raw[i:i+2000]) for i in range(0,len(raw),2000)] or [unresolved(raw)]


def provenance(payload,source,*,inferred=(),defaults=()):
    return {k:dict(category='UNKNOWN' if v is None else 'INFERENCE' if k in inferred or k in defaults else 'SOURCE FACT',
                   source_text=source,**({'basis':'capture_candidate'} if k in defaults and v is not None else {})) for k,v in payload.items()}


def account_for(raw,accounts,currency):
    t=re.sub(r'\bне\s+(?:наличными|наличка|из\s+кассы|с\s+кассы|с\s+банка|из\s+банка)\b','',fold(raw));kinds=set()
    if re.search(r'\b(?:наличными|наличка|из\s+кассы|с\s+кассы)\b',t):kinds.add('cash')
    if re.search(r'\b(?:с\s+банка|из\s+банка|банковским\s+переводом|онлайн\w*\s+банк\w*|через\s+банк\w*)\b',t):kinds.add('bank')
    if len(kinds)>1:return None
    matched=entity_matches(raw,accounts)
    if len(matched)==1:
        a=matched[0]
        return str(value(a,'id')) if value(a,'currency')==currency and (not kinds or value(a,'account_type') in kinds) else None
    if len(matched)>1 or len(kinds)!=1:return None
    candidates=[a for a in accounts if value(a,'active',True) is not False and value(a,'account_type') in kinds and value(a,'currency')==currency]
    return str(value(candidates[0],'id')) if len(candidates)==1 else None


def _depot_evidence(raw):
    # Quality rows can be tables or explicitly labelled prose. A loose mention
    # of a quality without a weight/row is not a receipt.
    return bool(re.search(r'(?i)\b(?:Grand|Birinci|İkinci|Ikinci|Sera|Yerli)\b[^\n]{0,80}\d',raw) or re.search(r'(?i)\bAlacak\s*(?:[:=]|равен)?\s*\d',raw))


def depot_text(raw,parties):
    if not _depot_evidence(raw):return None
    lines=[];qualities=[];unreadable=[]
    def num(v):return v.replace('.','').replace(',','.') if ',' in v else v
    labels=list(re.finditer(r'(?i)\b(Grand|Birinci|İkinci|Ikinci|Sera|Yerli)\b',raw))
    scalar=r'(\d+(?:[.,]\d+)*)'
    for idx,label in enumerate(labels):
        end=labels[idx+1].start() if idx+1<len(labels) else len(raw)
        # Totals and subsequent payment statements never supply row columns.
        body=re.split(r'(?i)\b(?:Alacak|Toplam)\b|\n',raw[label.end():end])[0]
        source_line=(label.group()+body).strip()[:1000]
        tagged=bool(re.search(r'(?i)\b(?:кг|kg|вес|масса|цена|ставка|килограмм|сумма|строка|начислено|количество)\b',body))
        count=None
        if tagged:
            patterns=[r'(?<!\w)'+scalar+r'\s*(?:кг|kg)(?!\w)',r'\b(?:цена|ставка|по|килограмм)\s*[:=]?\s*'+scalar,r'\b(?:сумма\s+строки|сумма|строка|начислено|всего|итого)\s*[:=]?\s*'+scalar]
            found=[re.findall(p,body,re.I) for p in patterns]
            counts=re.findall(r'(?i)\bколичество\s*[:=]?\s*(\d+)(?!\d|[.,]\d)',body)
            if any(len(v)!=1 for v in found) or len(counts)>1:unreadable.append(source_line);continue
            kg,price,amount=[num(v[0]) for v in found]
            if counts:count=int(counts[0])
        else:
            nums=re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)',body)
            if len(nums) not in (3,4):unreadable.append(source_line);continue
            # Positional interpretation is valid only for a numeric row, not
            # unrecognized prose with unrelated numbers.
            if re.search(r'[^\W\d_]',re.sub(r'(?i)\b(?:TRY|TL|USD|EUR|UAH)\b','',body)):
                unreadable.append(source_line);continue
            kg,price,amount=[num(v) for v in nums[-3:]]
            if len(nums)==4 and nums[0].isdigit():count=int(nums[0])
        try:
            if any(Decimal(v)<=0 for v in (kg,price,amount)) or count is not None and count<=0:unreadable.append(source_line);continue
            ln=dict(original_quality_name=label.group(),net_weight_kg=kg,unit_price=price,stated_amount=amount,source_line=source_line)
            if count is not None:ln['count']=count
            from greenhouse_owner_os.domain.harvest import LineInput
            LineInput.model_validate(ln)
            lines.append(ln);qualities.append({'grand':'HIGHEST','birinci':'FIRST','ikinci':'SECOND'}.get(fold(label.group())))
        except (ValueError,ArithmeticError):unreadable.append(source_line)
    for line in raw.splitlines():
        if re.search(r'(?i)\b(?:Grand|Birinci|İkinci|Ikinci|Sera|Yerli|Alacak|Toplam)\b',line):continue
        without_dates=re.sub(r'(?<!\d)\d{1,2}[./]\d{1,2}[./]20\d{2}(?!\d)','',line)
        if re.search(r'\d',without_dates) and (len(re.findall(r'(?<!\w)\d+(?:[.,]\d+)*(?!\w)',without_dates))>=3 or re.search(r'(?i)(работни|рабочи|веток|срезал)',line)):unreadable.append(line[:1000])
    deps=entity_matches(raw,[p for p in parties if value(p,'counterparty_type','both') in ('depot','customer','both')])
    dates=re.findall(r'(?<!\d)(\d{1,2})[./](\d{1,2})[./](20\d{2})(?!\d)',raw);event=None
    if len(set(dates))==1:
        try:d,m,y=dates[0];event=date(int(y),int(m),int(d)).isoformat()
        except ValueError:pass
    totals=re.findall(r'(?im)\b(?:alacak|toplam)\s*(?:[:=]|равен)?\s*'+scalar,raw)
    alacak=re.findall(r'(?im)\balacak\s*(?:[:=]|равен)?\s*'+scalar,raw)
    if len(deps)!=1 or 'gorgun' not in fold(value(deps[0],'name')):qualities=[None for _ in lines]
    return dict(depot_id=str(value(deps[0],'id')) if len(deps)==1 else None,event_date=event,lines=lines,qualities=qualities,currency=source_currency(raw),stated_total=num(totals[0]) if len(set(totals))==1 else None,alacak_value=num(alacak[0]) if len(alacak)==1 else None,unreadable_lines=unreadable)

def _parts(raw,*,binary=False,refs=None):
    # An explicit standalone payment assertion next to receipt rows is its own
    # semantic slot. Never reinterpret Alacak or numeric table cells as cash.
    if _depot_evidence(raw):
        money=[];spans=[];cursor=0
        for statement in split_outside_quotes(r'\n+|(?<=[.;])\s+',raw):
            start=raw.find(statement,cursor);end=start+len(statement);cursor=end
            if not re.search(PAID+'|'+OWED+'|'+PURCHASE,financial_evidence_view(statement)):continue
            # A mixed row and financial assertion without an independent
            # boundary stays unresolved as a whole below; never drop the row.
            if _depot_evidence(statement):return [raw]
            money.append(statement.strip());spans.append((start,end))
        if money:
            receipt=raw
            for start,end in reversed(spans):receipt=receipt[:start]+receipt[end:]
            return [receipt.strip(),*money] if receipt.strip() else [raw]
    # Receipt table rows stay together; paragraphs are not separate documents.
    if binary or _depot_evidence(raw):return [raw]
    # Do not split decimals, dates or an explicit same-source correction.
    parts=split_outside_quotes(r'(?:(?<!тыс)(?<!млн)[.;](?!\d)\s+|\n+)',raw,flags=re.I)
    # Anaphoric/attribute sentences qualify the same event. They are kept in
    # its original evidence, while a new explicit action remains independent.
    coherent=[];cursor=0;start=0
    attribute_start=r'^(?:сумма\b|этот\s+счет\b|пока\s+вся\s+сумма\b|остаток\s+долга\b|продавца\b|стоимость\b|ни\s+счета\b|товар\s+ранее\b|деньги\s+(?:отправлен\w*|взял)\b|по\s+кассе\b|оплата\s+прошла\b|в\s+часов\b|у\s+нас\s+нет\s+задолженности\b|это\s+только\s+счет\b)'
    for part in parts:
        position=raw.find(part,cursor);end=position+len(part);cursor=end
        t=fold(part).strip()
        if coherent and (re.search(attribute_start,t) or re.match(r'(?i)^\s*исполнитель\s+[^.!?]+$',coherent[-1]) or re.search(r'\bна\s+нее\b',t) and re.search(r'\b(?:подкормк\w*|обработк\w*)\b',fold(coherent[-1]))):coherent[-1]=raw[start:end]
        else:coherent.append(part);start=position
    parts=coherent
    result=[]
    active_paid=r'\b(?:заплатил(?:а|и)?|оплатил(?:а|и)?|отдал(?:а|и)?|передал(?:а|и)?|перевел(?:а|и)?|выплатил(?:а|и)?|выдал(?:а|и)?|отправил(?:а|и)?|перечислил(?:а|и)?)\b'
    event_start=r'(?:я\s+)?(?:'+active_paid+r'|\b(?:должен|должна|должны|нужно\s+(?:заплатить|оплатить)|(?:остался|образовался|подтвержден)\s+долг)\b|'+r'\b(?:купил(?:а|и)?|потратил(?:а|и)?|заправил(?:а|и)?)\b|\b(?:привезли|доставили|внесли|счет\s+потом|счет\s+еще|invoice|fatura)\b)'
    for part in parts:
        if re.search(UNCERTAIN,fold(part)) or reported_speech(part):result.append(part);continue
        clauses=split_outside_quotes(r'(?:,\s*|\s+и\s+)(?='+event_start+r')',part,flags=re.I)
        # Coordinated elliptical verbs keep the first actor. Splitting must
        # not grant owner authority to a third party's next payment/purchase.
        scoped_clauses=[];cursor=0;start=0
        for clause in clauses:
            position=part.find(clause,cursor);end=position+len(clause);cursor=end
            if scoped_clauses and other_financial_actor(scoped_clauses[-1],refs or {}) and not re.match(r'^\s*(?:я|мы)\b',fold(clause)):
                scoped_clauses[-1]=part[start:end]
            else:scoped_clauses.append(clause);start=position
        clauses=scoped_clauses
        # Purchase plus its payment method is one paid expense; an independent
        # payment with its own amount/object keeps a separate slot.
        if len(clauses)==2 and re.search(PURCHASE,fold(clauses[0])) and re.match(r'(?i)^(?:я\s+)?(?:оплатил|заплатил)(?:а|и)?\b',clauses[1]) and money_amount(clauses[1],implicit=True) is None and not re.search(r'\b(?:долг|отдельно|другой|еще)\b',fold(clauses[1])):clauses=[part]
        if len(clauses)>1 and re.search(r'\b(?:взяли|забрали)\s*$',fold(clauses[0])) and re.match(r'(?i)внесли\b',clauses[1]):clauses=[part]
        scoped=[]
        for clause in clauses:
            if re.search(PAID+'|'+OWED+'|'+PURCHASE,fold(clause)):
                scoped.extend(re.split(r'(?:,\s*|\s+)(?=(?:(?:он|она|они)\s+(?:сказал\w*|говорит|просит)|не\s+(?:переводить|платить|списывать|вносить))\b)',clause,flags=re.I))
            else:scoped.append(clause)
        for clause in scoped:
            if re.search(PAID+'|'+OWED,fold(clause)) and not re.search(r'\bне\b',fold(clause)):
                result.extend(re.split(r',\s*а\s+(?=[А-ЯA-ZİÖÜÇŞ])',clause))
            else:result.append(clause)
    return [p.strip() for p in result if p.strip()]


def _prepare_one(raw,semantic,*,today,refs,pages):
    t=financial_evidence_view(semantic)
    if meta_or_report(semantic) or reported_speech(semantic) or other_financial_actor(semantic,refs):return unresolved_parts(raw)
    if _depot_evidence(raw) and re.search(PAID+'|'+OWED+'|'+PURCHASE,t):return unresolved_parts(raw)
    # A reminder to act is not evidence of the intended business effect.
    explicit_reminder=bool(re.search(r'\b(?:напомни|напомнить|позвонить|уточнить|сверить|поставь|запиши|записать)\b',t))
    task=extract_task(semantic,areas=refs.get('areas',()),workers=refs.get('workers',()),today=today)
    # First-person dative marks money received (or an internal transfer), not
    # an outgoing settlement of the owner's payable. No direction is guessed.
    incoming=re.search(r'\b(?:мне|нам|себе)\s+(?:\w+\s+){0,2}(?:'+PAID+r')',t) or re.search(PAID+r'\s+(?:'+NUMBER+r'\s*(?:'+CURRENCY+r')?\s+)?(?:мне|нам|себе)\b',t)
    if incoming and not explicit_reminder:return unresolved_parts(raw)
    if re.search(r'\bдолжен\s+был\b',t):return unresolved_parts(raw)
    unsupported_scope=re.sub(r'\b(?:аванс\w*|предоплат\w*)\s+(?:здесь\s+)?нет\b|\bне\s+(?:аванс\w*|предоплат\w*)\b','',t)
    if re.search(UNSUPPORTED,unsupported_scope) and not explicit_reminder:return unresolved_parts(raw)
    if re.search(r'\b(?:он|она|они)\s+(?:сказал\w*|говорит|просит)\b',t):return unresolved_parts(raw)
    uncertainty_scope=re.split(r',\s*(?:возможно|наверное|может быть)\s+(?:отдам|оплачу|заплачу|переведу)',t)[0]
    if re.search(UNCERTAIN,uncertainty_scope) and not explicit_reminder:return unresolved_parts(raw)
    if '?' in raw and not explicit_reminder and re.search(PAID+'|'+OWED+'|'+PURCHASE,t):return unresolved_parts(raw)
    if re.search(r'\b(?:отменить|отмена|сторнировать|вернуть)\b.*\b(?:оплат|платеж|деньг)',t):return unresolved_parts(raw)
    if re.search(r'\bперевел\b',t) and len(entity_matches(semantic,refs.get('accounts',())))>1 and not entity_matches(semantic,refs.get('counterparties',()),inflect=True):return unresolved_parts(raw)
    paid=bool(re.search(PAID,t));purchase=bool(re.search(PURCHASE,t))
    if future_financial_intent(semantic) and not purchase and (not paid or re.search(NEGATED_MONEY,t) or re.search(r'\bне\s+оплачен\w*\b',t)) and not explicit_reminder:
        if task:explicit_reminder=True
        else:return unresolved_parts(raw)
    if paid and re.search(r'\b(?:если|будет|будут|должен\s+быть)\b',t) and not explicit_reminder:return unresolved_parts(raw)
    if re.search(r'\b(?:передал|выдал|отправил)(?:а|и)?\b',t) and not re.search(r'\b(?:деньги|оплат\w*|зарплат\w*|платеж\w*)\b',t) and not money_amount(semantic,implicit=True):paid=False
    credit_purchase=purchase and bool(re.search(r'\b(?:в\s+долг|с\s+отсрочкой|еще\s+не\s+оплатил|отдам|заплачу|оплачу|переведу|оплатим|заплатим)\b',t))
    strong_obligation=bool(re.search(r'\b(?:должен|должна|должны|нужно\s+(?:будет\s+)?(?:заплатить|оплатить)|осталось\s+выплатить|к\s+оплате|обязан(?:а|ы)?|начислен(?:а|о|ы)?\s+зарплат\w*|неоплаченный\s+(?:согласованный\s+)?счет)\b',t))
    if re.search(r'\bне\s+(?:погасил|закрыл)(?:а|и)?\b',t) and not strong_obligation:return unresolved_parts(raw)
    owed=(strong_obligation or bool(re.search(OWED,t)) and money_amount(semantic,implicit=True) is not None) and not nonfinancial_modal(t) or credit_purchase
    if purchase and not credit_purchase and not money_amount(semantic,implicit=True) and not re.search(r'\b(?:потратил|заправил)\b',t):return unresolved_parts(raw)
    if not explicit_reminder and (re.search(NEGATED_MONEY,t) or re.search(r'\bне\s+оплачен\w*\b|\b(?:оплата|платеж)\b[^.!?;]{0,40}\bне\s+(?:прошла|завершен\w*|состоял\w*)\b',t)):
        # An explicit current payable can coexist with 'ещё не платил'.
        if not owed or re.search(r'\bне\s+долж',t):return unresolved_parts(raw)
        paid=False
    if (paid or owed or purchase) and not explicit_reminder:
        paid_expense=paid and bool(re.search(r'\b(?:за\s+(?:бензин|топливо|заправку)|расход\s+на\s+заправку|бензин\s+куплен)\b',t)) and not re.search(r'\bдолг\w*\b',t)
        kind='PAYABLE' if owed and not paid else 'PAYMENT' if paid and re.search(r'\bдолг\w*\b',t) else 'DIRECT_EXPENSE' if purchase or paid_expense else 'PAYMENT'
        currency=source_currency(semantic);amount=money_amount(semantic,implicit=True)
        if kind=='PAYMENT' and amount is None:
            # An explicitly stated debt total preceding the payment predicate
            # is not the amount paid. No subtraction or fraction is inferred.
            payment=re.search(PAID,t)
            if payment and re.search(r'\bдолг\w*\b',t[:payment.start()]) and money_amount(t[:payment.start()],implicit=True):
                amount=money_amount(t[payment.end():],implicit=True)
        counterparties=entity_matches(semantic,refs.get('counterparties',()),inflect=True)
        party=str(value(counterparties[0],'id')) if len(counterparties)==1 else None
        event,mentioned=date_value(semantic,today)
        if kind!='PAYABLE' and event and date.fromisoformat(event)>today:return unresolved_parts(raw)
        due,_=date_value(semantic,today,role='due')
        payload=dict(amount=amount,currency=currency,event_date=event if mentioned else today.isoformat(),counterparty_id=party,
                     description=raw[:2000],due_date=due,money_account_id=account_for(semantic,refs.get('accounts',()),currency),attributions=[])
        areas=entity_matches(semantic,refs.get('areas',()),'area')
        if kind=='PAYABLE' and len(areas)==1 and amount:payload['attributions']=[dict(work_area_id=str(value(areas[0],'id')),amount=amount)]
        if kind=='PAYMENT' and re.search(r'\b(?:долг\w*|погаш\w*)\b',t):payload['payment_purpose']='debt'
        return [(kind,payload,provenance(payload,raw,inferred=('counterparty_id','money_account_id'),defaults=tuple(k for k,flag in [('event_date',not mentioned),('currency',source_currency(semantic,None) is None)] if flag)))]
    if _depot_evidence(raw):
        if '?' in raw:return unresolved_parts(raw)
        depot=depot_text(raw,refs.get('counterparties',()))
        return [('DEPOT_RECEIPT',depot,{'lines':{'category':'SOURCE FACT','pages':pages},'qualities':{'category':'INFERENCE','basis':'depot_scoped_unverified_candidate'},'stated_total':{'category':'INFERENCE' if depot.get('stated_total') is not None else 'UNKNOWN','basis':'document_total_candidate_requires_review'}})]
    if task:
        f=task[1];workers=f.get('suggested_worker_ids',[])
        payload=dict(title=raw[:200],original_instruction=raw,greenhouse_id=f.get('greenhouse_id'),planned_date=f.get('planned_date'),worker_id=workers[0] if len(workers)==1 else None)
        worker=next((w for w in refs.get('workers',()) if str(value(w,'id'))==payload['worker_id']),None)
        if worker:payload['language']=value(worker,'language')
        return [(task_audience(semantic,refs.get('workers',())),payload,provenance(payload,raw,inferred=('greenhouse_id','worker_id','language')))]
    inventory=extract_inventory(raw,products=refs.get('products',()),locations=refs.get('locations',()),areas=refs.get('areas',()),today=today)
    if inventory:
        f=inventory[1];kind={'receipt':'STOCK_RECEIPT','application':'STOCK_APPLICATION'}.get(f.get('kind'))
        if not kind:return unresolved_parts(raw)
        event,mentioned=date_value(re.split(r'(?i)[,;]\s*(?:осталось|остаток)\b',semantic)[0],today)
        if event and date.fromisoformat(event)>today:return unresolved_parts(raw)
        payload=dict(event_date=event if mentioned else today.isoformat(),lines=f['lines'],notes=raw[:2000])
        return [(kind,payload,provenance(payload,raw,inferred=('lines',),defaults=('event_date',) if not mentioned else ()))]
    depot=depot_text(raw,refs.get('counterparties',()))
    if depot:return [('DEPOT_RECEIPT',depot,{'lines':{'category':'SOURCE FACT','pages':pages},'qualities':{'category':'INFERENCE','basis':'depot_scoped_unverified_candidate'},'stated_total':{'category':'INFERENCE' if depot.get('stated_total') is not None else 'UNKNOWN','basis':'document_total_candidate_requires_review'}})]
    if re.search(INVOICE,t) and (re.match(r'^(?:fatura|invoice)\b',t) or re.search(r'\b(?:счет\s+поставщика|(?:пришел|получили|получил|прислал|есть|вот)\s+(?:fatura|invoice))\b',t)):
        if re.search(r'\b(?:потом|завтра|пришлют|позже|не\s+(?:пришел|получил|дали|выдали))\b',t):return unresolved_parts(raw)
        event,mentioned=date_value(semantic,today)
        payload=dict(description=raw[:2000],amount=money_amount(semantic),event_date=event if mentioned else today.isoformat(),currency=source_currency(semantic))
        parties=entity_matches(semantic,refs.get('counterparties',()),inflect=True)
        if len(parties)==1:payload['counterparty_id']=str(value(parties[0],'id'))
        return [('PAYABLE',payload,provenance(payload,raw,defaults=('event_date','currency'))),('INVOICE_MATCH',dict(note=raw[:2000]),{})]
    return unresolved_parts(raw)


def prepare_owner_source(raw,*,today,refs,binary=False,pages=()):
    """Shared text/voice runtime entry. No mode-sensitive semantic branch."""
    def annotated(slots):
        for kind,payload,prov in slots:
            for key,item in payload.items():
                entry=prov.setdefault(key,{'category':'UNKNOWN' if item is None or not raw else 'SOURCE FACT' if key in ('note','notes','description','title','original_instruction') else 'INFERENCE','source_text':raw})
                entry['interpretation_version']=INTERPRETATION_VERSION
        return slots
    output=[]
    # An explicitly labelled bill list consists of independent payable slots;
    # it is neither physical receipt evidence nor evidence of cash payment.
    bill_list=re.match(r'(?is)^\s*(сегодняшние\s+)?счета\s*:\s*(.+)$',raw) if not binary else None
    if bill_list:
        entries=re.split(r'[,;]\s+(?=\d)|\n+',bill_list.group(2))
        if len(entries)>1 and all(money_amount(e,implicit=True) for e in entries):
            for entry in entries:
                semantic=('Сегодня ' if bill_list.group(1) else '')+'Должен '+entry
                output.extend(_prepare_one(entry,semantic,today=today,refs=refs,pages=pages))
            return annotated(output)
    for nouns,kind in [(r'чек|квитанция|документ','DEPOT_RECEIPT'),(r'платеж|перевод','PAYMENT')]:
        entries=enumerated_fragments(raw,nouns)
        if not entries:continue
        header,fragments=entries
        if not fragments:return annotated(unresolved_parts(raw))
        count=re.search(r'\b(дважды|трижды)\b',fold(header))
        if count and len(fragments)!={'дважды':2,'трижды':3}[count.group(1)]:return annotated(unresolved_parts(raw))
        eligible=_depot_evidence(raw) if kind=='DEPOT_RECEIPT' else bool(re.search(PAID,fold(header)))
        if kind=='DEPOT_RECEIPT' and (_depot_evidence(header) or not all(_depot_evidence(f) for f in fragments)):return annotated(unresolved_parts(raw))
        if not eligible:continue
        slots=[]
        for fragment in fragments:
            scoped=header+fragment
            candidate=_prepare_one(scoped,corrected(scoped,**refs),today=today,refs=refs,pages=pages)
            if len(candidate)!=1 or candidate[0][0]!=kind:return annotated(unresolved_parts(raw))
            for entry in candidate[0][2].values():
                entry['source_text']=raw
                entry['scope']={'shared_context':header,'event_fragment':fragment}
            slots.extend(candidate)
        return annotated(slots)
    if _depot_evidence(raw) and re.search(r'\b(?:(?:разных|отдельных)\s+|(?:два|две|три|четыре|[2-9]\d*)\s+)(?:чека|чеков|квитанции|документа|документов)\b|\b(?:другой|отдельный)\s+чек\b',fold(raw)):
        return annotated(unresolved_parts(raw))
    parts=_parts(raw,binary=binary,refs=refs)
    if not binary:
        worker_names=[re.escape(n) for worker in refs.get('workers',()) for n in names(worker,'worker')]
        expanded=[]
        for part in parts:
            clauses=re.split(r'(?:,\s*(?:а\s+)?|\s+(?:и|а)\s+)(?=(?:'+ '|'.join(worker_names)+r')(?!\w))',part,flags=re.I) if worker_names else [part]
            if len(clauses)>1 and all(extract_task(c,areas=refs.get('areas',()),workers=refs.get('workers',()),today=today) for c in clauses):expanded.extend(clauses)
            else:expanded.append(part)
        parts=expanded
    if not binary and len(parts)>1:
        parsed=[extract_task(corrected(part,**refs),areas=refs.get('areas',()),workers=refs.get('workers',()),today=today) for part in parts]
        if all(parsed) and all(not f[1].get('planned_date') and not f[1].get('greenhouse_id') and not f[1].get('suggested_worker_ids') for f in parsed[1:]):parts=[raw]
    for part in parts:
        semantic=corrected(part,**refs)
        slots=_prepare_one(part,semantic,today=today,refs=refs,pages=pages)
        if semantic!=comparison_source(part):
            for _,payload,prov in slots:
                for key,item in prov.items():
                    item['normalization']='explicit_same_source_correction'
                    item['source_text']=part
        if len(parts)>1 and re.match(r'(?i)^(?:привет\s+)?брат\b',corrected(raw,**refs)) and not re.match(r'(?i)^я\b',part):
            slots=[('WORKER_TASK',p,prov) if k=='PERSONAL_TASK' else (k,p,prov) for k,p,prov in slots]
        # Independent unsupported remainder survives as its own review slot.
        if output and slots[0][0]=='UNRESOLVED' and output[-1][0]=='UNRESOLVED' and len(output[-1][1]['note'])+1+len(slots[0][1]['note'])<=2000:
            output[-1]=unresolved(output[-1][1]['note']+'\n'+slots[0][1]['note'])
            output.extend(slots[1:])
        else:output.extend(slots)
    return annotated(output or unresolved_parts(raw))
