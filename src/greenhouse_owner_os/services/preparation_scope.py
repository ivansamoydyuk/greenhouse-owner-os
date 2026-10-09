"""Source-level boundaries applied before interpreting any business candidate."""
import re
from .preparation_language import DATE_PATTERN, NUMBER, fold, names, nonfinancial_modal


PAID=r'\b(?:заплатил(?:а|и)?|оплатил(?:а|и)?|оплачивал(?:а|и)?|отдал(?:а|и)?|передал(?:а|и)?|перевел(?:а|и)?|выплатил(?:а|и)?|выдал(?:а|и)?|отправил(?:а|и)?|перечислил(?:а|и)?|перечислено|рассчитался|рассчиталась|оплачен(?:а|о|ы)?|(?:платеж|оплата)\b[^.!?;]{0,80}\b(?:завершен(?:а)?|состоял(?:ась|ся)|прошл(?:а|о))|получил(?:а|и)?\s+от\s+меня|(?:закрыл|погасил)(?:а|и)?\s+(?:часть\s+)?долг(?:а)?)\b'
OWED=r'\b(?:должен|должна|должны|нужно\s+(?:будет\s+)?(?:заплатить|оплатить)|осталось\s+выплатить|к\s+оплате|(?:итоговый\s+|мой\s+|моего\s+|подтвержденного\s+|сумма\s+)?долг(?:а)?)\b'
PURCHASE=r'\b(?:купил(?:а|и)?|куплен(?:а|о|ы)?|потратил(?:а|и)?|заправил(?:а|и)?)\b'


def financial_evidence_view(raw):
    """Document delivery verbs do not assert money movement.

    Keep offsets and the entire source; exclude only the verb governed by an
    explicit document object. Other financial clauses keep normal scope gates.
    """
    t=fold(raw)
    return re.sub(r'\b(?:выдал|передал|отправил)(?:а|и)?\b(?=\s+(?:(?:мне|нам|новую|новый|этот|эту)\s+)*(?:приемк\w*|чек\w*|квитанци\w*|накладн\w*|счет\w*|документ\w*|invoice|fatura)\b)',lambda m:' '*len(m.group()),t)


def other_financial_actor(raw,refs):
    """Resolve actor and creditor direction before paid or owed selection."""
    t=financial_evidence_view(raw);obligation=r'\b(?:должен|должна|должны|обязан|обязана|обязаны)\b'
    invoice_receipt=r'\bполучил(?:а|и)?(?=\s+(?:invoice|fatura|счет\s+поставщика)\b)'
    predicate=re.search(PAID+'|'+obligation+'|'+PURCHASE+'|'+invoice_receipt,t)
    # Obligation morphology also expresses a worker's future task. Do not
    # convert nonfinancial modality into either debt or a finance-scope veto.
    if predicate and re.fullmatch(obligation,predicate.group()) and nonfinancial_modal(t):return False
    if predicate and re.match(r'получил(?:а|и)?\s+от\s+меня',predicate.group()):return False
    if predicate and re.fullmatch(obligation,predicate.group()) and (re.search(r'\b(?:мне|нам)\b',t[:predicate.start()]) or re.match(r'\s*(?:мне|нам)\b',t[predicate.end():])):return True
    recipients=[];counterparty_names=[]
    for kind,rows in [('name',refs.get('counterparties',())),('worker',refs.get('workers',()))]:
        for row in rows:
            for name in names(row,kind):
                if kind=='name':counterparty_names.append(fold(name))
                words=fold(name).split()
                if re.fullmatch(r'[а-я]+',words[0]):
                    recipients.extend(words[0]+ending+(' '+' '.join(words[1:]) if len(words)>1 else '') for ending in ('у','е'))
                    recipients.extend(words[0]+ending for ending in ('у','е'))
                if words and all(w.endswith(('ые','ие')) for w in words):recipients.append(' '.join(w[:-2]+('ым' if w.endswith('ые') else 'им') for w in words))
    recipient=r'(?:'+'|'.join(re.escape(s) for s in recipients)+r')(?!\w)' if recipients else r'(?!)'
    known_party=r'(?:'+'|'.join(re.escape(s) for s in counterparty_names)+r')(?!\w)' if counterparty_names else r'(?!)'
    creditor_role=r'\b(?:поставщику|подрядчику|исполнителю|работнику|сотруднику|кредитору|арендодателю|поставщикам|подрядчикам|исполнителям|работникам|сотрудникам|кредиторам|арендодателям)\b'
    relational=r'\b(?:за|на|по|в|во|из|с|со|от|для|у|к|о|об|до)\b'
    def nominal_prefix(before):
        before=re.sub(DATE_PATTERN,' ',before)
        return re.sub(r'\s+',' ',re.sub(r'\b(?:сегодня|вчера|позавчера|уже)\b',' ',before)).strip(' ,:;.-')
    def owner_debt_prefix(before):
        before=nominal_prefix(before)
        neutral=r'(?:остаток|сумма|итоговый|подтвержденный|остался)(?:\s+(?:именно|моего))?|в\s+счет|из'
        accounting=r'(?:в\s+учете\s+)?(?:нужен|учтен|отражен|признан|подтвержден|зафиксирован|учесть|отразить|признать|зафиксировать|подтверждаю)(?:\s+(?:мой|наш))?'
        # A bounded intransitive accrual assertion describes the owner's
        # debt in ordinary ellipsis, or explicitly says 'у меня/у нас'. A
        # third-party subject or possessor is not part of this grammar.
        accrual=r'(?:(?:у\s+(?:меня|нас))\s+)?(?:возник|образовался|сформировался|появился|остался)(?:\s+(?:мой|наш))?'
        return not before or bool(re.fullmatch(neutral+'|'+accounting+'|'+accrual,before) or re.fullmatch(recipient,before))
    passive=bool(predicate and re.match(r'(?:куплен|оплачен|перечислено|платеж|оплата)',predicate.group()))
    # A completed nominal settlement is an event frame, not a nominal
    # possessor: "погашение части долга <creditor> проведено ...". It permits
    # an exact known creditor spelling without inventing inflection. Unknown
    # possessors and explicit third-party actors still cannot establish owner
    # authority; neither reference membership nor payment wording alone does.
    debt=re.search(r'\bдолг(?:а)?\b',t)
    if passive and debt and debt.start()<predicate.start():
        settlement=r'(?:погашение|закрытие|оплата|выплата)\s+(?:части\s+)?(?:(?:моего|нашего)\s+)?'
        if re.fullmatch(settlement,nominal_prefix(t[:debt.start()])+' '):
            after=t[debt.end():].lstrip()
            completed=re.search(r'\b(?:(?:проведен|завершен|выполнен|произведен)(?:а|о|ы)?|состоял(?:ась|ось|ся)|перечислено)\b',after)
            if not completed or re.search(r'\b(?:мне|нам)\b',after):return True
            creditor=nominal_prefix(after[:completed.start()])
            # Affirmative completion only. A negated/future qualifier is not
            # source evidence of a completed payment.
            if re.search(r'\b(?:не|будет|будут)\b',creditor):return True
            return bool(creditor and not (re.fullmatch(recipient,creditor) or re.fullmatch(known_party,creditor)))
    # A passive payment qualification does not establish who owns the debt.
    nominal_debt=bool(passive and debt and debt.start()<predicate.start())
    if not predicate or nominal_debt:
        if not debt:return False
        if re.search(r'\b(?:мне|нам)\b',t[debt.end():]):return True
        before=t[:debt.start()]
        if re.search(r'\b(?:мой|моего|наш|нашего)\s+(?:(?:подтвержденн\w*|текущ\w*|оставш\w*|итогов\w*)\s+)*$',before):return False
        after=t[debt.end():].lstrip()
        # A creditor-role dative expresses the direction structurally even if
        # the following name has no Russian case ending. It does not establish
        # an unknown entity or override a third-party nominal debtor prefix.
        if re.match(recipient,after) or re.match(creditor_role,after):return not owner_debt_prefix(before)
        debt_state=r'\b(?:составляет|равен|подтвержден|погашен|закрыт|оплачен|остался|еще|пока|не)\b'
        if re.match(r'[^\W\d_][\w-]*',after) and not any(re.match(p,after) for p in (relational,NUMBER,debt_state)):return True
        return not owner_debt_prefix(before)
    prefix=t[:predicate.start()]
    # A preposed nominal actor is not proof of owner identity, whether its
    # spelling is Cyrillic, Latin or another script and whether it is known.
    # Keep ordinary verb-first ellipsis, explicit first person and verified
    # dative recipients; no unknown name is resolved to a reference here.
    nominal=re.sub(DATE_PATTERN,' ',prefix)
    nominal=re.sub(r'\b(?:сегодня|вчера|позавчера|уже|сам|сама|сами|сразу|только|дважды|трижды|лично)\b',' ',nominal)
    nominal=re.sub(r'\s+',' ',nominal).strip(' ,:;.-')
    if not nominal or re.search(r'\b(?:я|мы)\b',nominal):return False
    dative=re.match(recipient,nominal)
    if dative and (not nominal[dative.end():].strip() or re.match(relational,nominal[dative.end():].strip())):return False
    # Passive purchase/payment objects do not name a person performing an act.
    if passive:return False
    # In active clauses references recognize only a dative recipient; they
    # never enumerate possible other actors. A nominal lead remains unsafe
    # with arbitrary script, name length, or intervening payment-purpose words.
    if re.match(relational,nominal):return False
    return bool(re.match(r'[^\W\d_]',nominal))

def quoted_spans(raw):
    pairs={'«':'»','“':'”','„':'“','‘':'’','"':'"',"'":"'"};spans=[];start=None;closing=None
    for pos,char in enumerate(raw):
        if closing:
            if char==closing:spans.append((start,pos+1));start=closing=None
        elif char in pairs:
            if char=="'" and pos and pos+1<len(raw) and raw[pos-1].isalnum() and raw[pos+1].isalnum():continue
            start=pos;closing=pairs[char]
    if closing:spans.append((start,len(raw)))
    return spans


def split_outside_quotes(pattern,raw,*,flags=0):
    spans=quoted_spans(raw);parts=[];start=0
    for match in re.finditer(pattern,raw,flags):
        if any(a<=match.start()<b for a,b in spans):continue
        parts.append(raw[start:match.start()]);start=match.end()
    parts.append(raw[start:]);return parts


def reported_speech(raw):
    """Attribution and labelled quotes establish speaker scope before events."""
    t=fold(raw)
    if re.search(r'\b(?:по\s+словам|со\s+слов)\b',t):return True
    speech=r'\b(?:сказал(?:а|и)?|сообщил(?:а|и)?|написал(?:а|и)?|говорит|рассказал(?:а|и)?|объяснил(?:а|и)?)\b'
    # Objects such as "об оплате" may separate the reporting verb and colon.
    if re.search(speech+r"[^.!?;]{0,120}(?::|\bчто\b|[«“„‘\"']|\b(?:я|мы|он|она)\s)",t):return True
    return bool(re.match(r"^[^:\n]{1,120}:\s*[«“„‘\"']",t))


def future_financial_intent(raw):
    t=fold(raw)
    intent=re.search(r'\b(?:нужно\s+будет|надо\s+будет|должен\s+буду|собираюсь|планирую|намерен|хочу)\b[^.!?;]{0,100}\b(?:заплатить|оплатить|погасить|перевести|отдать)\b|\b(?:заплачу|оплачу|погашу|переведу|отдам)\b|\b(?:завтра|послезавтра)\b[^.!?;]{0,80}\b(?:погасить|перевести|отдать)\b',t)
    if not intent:return False
    # A separately asserted current obligation may carry a future due date.
    current=re.search(r'\b(?:должен|должна|должны)\b(?!\s+(?:буду|будет|быть))|\b(?:остался|подтвержденный|подтверждаю)\b[^.!?;]{0,50}\bдолг\b|\b(?:начислена\s+зарплата|неоплаченный\s+счет)\b',t)
    return not current


def enumerated_fragments(raw,nouns):
    # Only explicit ordinal/numbered source boundaries create multiple slots.
    # Equal amounts/labels alone never establish identity or multiplicity.
    ordinal=r'(?:первый|второй|третий|четвертый|пятый|пятая|первая|вторая|третья|четвертая|№?\s*\d+|[^\W\d_]|[ivxlcdm]+|[\w-]{1,40}(?=\s*:))(?!\w)'
    noun=r'(?:'+nouns+r')'
    pattern=r'\b(?:'+noun+r'\s+'+ordinal+r'|'+ordinal+r'\s+'+noun+r')\s*:?' 
    matches=list(re.finditer(pattern,raw,re.I))
    if len(matches)<2:return None
    header=raw[:matches[0].start()]
    if len({fold(m.group()).strip(' :') for m in matches})!=len(matches):return header,[]
    return header,[raw[m.start():matches[i+1].start() if i+1<len(matches) else len(raw)].strip(' ,;\n') for i,m in enumerate(matches)]
