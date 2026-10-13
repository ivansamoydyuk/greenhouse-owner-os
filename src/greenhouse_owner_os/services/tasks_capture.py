"""Conservative instruction extraction from supplied, current references only."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from .task_audience_language import task_actor_scope, task_recipient_evidence
from .preparation_language import corrected, date_value, entity_matches, explicit_entity_mentions, fold, value, nonfinancial_modal, meta_or_report, relative_time_context, NEXT_WEEK, names

# Action/object frames support either word order. The small spelling allowance
# applies only to these ordinary verbs; greenhouse/worker IDs remain exact.
IRRIGATION_REWORK=r'переделать|переделывать|переделай(?:те)?|переделат|передлать|перенастроить|перенастрой(?:те)?|настроить|настрой(?:те)?|наладить|наладь(?:те)?|изменить|измени(?:те)?|поменять|поменяй(?:те)?'
IRRIGATION_OBJECT=r'полив(?:а)?|орошени(?:е|я)'


def irrigation_rework(raw):
    """Recognize an intended irrigation change, never a completion report."""
    t=fold(raw)
    for clause in re.split(r'[.!?;]',t):
        if not re.search(r'\b(?:'+IRRIGATION_REWORK+r')\b',clause) or not re.search(r'\b(?:'+IRRIGATION_OBJECT+r')\b',clause):continue
        if re.search(r'\b(?:не\s+(?:нужно|надо|необходимо)|(?:нужно|надо)\s+было|требовалось|если)\b',clause):continue
        if re.search(r'\bне\s+(?:'+IRRIGATION_REWORK+r')\b',clause):continue
        return True
    return False

# Bounded verb forms: completed actions are intentionally absent.
CATEGORIES={
    'irrigation':r'полить|поливай(?:те)?|поливать|поливаешь|польет|долей(?:те)?|делай(?:те)?\s+(?:обычный\s+)?полив|дать\s+полив',
    'ventilation':r'(?:открыть|закрыть|открывать|закрывать|закрытие|открытие|открой(?:те)?|закрой(?:те)?|откроет|закроет)(?:[\s,]+(?:пожалуйста|только|верхние|нижние|все|боковые|левые|правые|маленькую|большую))*[\s,]+(?:окн\w*|теплиц\w*|вентиляц\w*)',
    'harvest':r'режет|режьте|резать|срезать|порезать|подготовить\b.*\b(?:машин\w*|срезк\w*)',
    'cleanup':r'убрать|убирай(?:те)?|расчистить|очистить|вычистить|вычищать|подстричь|обрезать|подрезать',
    'inspection':r'проверь(?:те)?|проверить|проверять|посмотреть|посмотри(?:те)?|осмотреть|собрать\s+образцы',
    'fertilizer':r'внести|внесите|обработать|опрыскать|замешивать|замешать|размешивай(?:те)?|наливаешь|берешь',
    'repair':r'отремонтировать|починить|починит|покрасить|доставать|перетянуть|посоединять|доделает',
    'transport':r'отвезти|привезти|заехать|заедь(?:те)?|заедьте|езжайте|съездить|забрать|возьмите',
    'general':r'задача|поручение|поручаю|твоя\s+работа|работа\s+для|вызывать|вызвать|спросить|куплю|распишу|перевести\s+деньги|(?:пускай|пусть)\s+выключает|не\s+забудь\s+написать|помогать|помочь|помоги|помогите|подвязать|поднять|собрать|позвонить|связаться|договориться|договорись|напомни|напомнить|уточнить|сверить|(?:отправить\b[^.!?]*\bфото|фото\b[^.!?]*\bотправить)|пришли\s+фото|сфотографировать|прислать|сообщить|продолжить|берите|приходи|ставите|проходите|пиши|говори|разберусь|сделай|спрятать|отдавайте|экономить|сфотографируй|сравнить|сравни|подписать|подготовить|посчитай|посчитать|оставь|убери|приступить|поставь\s+мне|себе\s+запиши|запиши\s+(?:мне|себе)|себе\s+записать|найти|написать|покажи|(?:скажи|попроси)\b.*\bчтобы\b.*\b(?:сфотографировал|прислал|полил|закрыл)',
}


def ambiguous_area_coordination(raw, areas):
    """A known place plus an unknown coordinated place is not a sole location.

    This is a scope veto only, never typo correction or identity resolution.
    Require explicit location-list grammar; action/time conjunctions do not
    provide a second geographical member. Negated/replaced groups stay outside
    positive location scope.
    """
    t=fold(raw)
    aliases={fold(alias) for area in areas if value(area,'active',True) is not False for alias in names(area,'area')}
    if not aliases:return False
    registered='|'.join(re.escape(alias) for alias in sorted(aliases,key=len,reverse=True))
    atom=r'(?:(?:'+registered+r')(?!\w)|[^\W\d_][\w-]*(?:\s+(?:№\s*)?\d+(?!\d))?)'
    pattern=r'\b(?:на|в)\s+('+atom+r'(?:\s+(?:и|или|либо)\s+(?:(?:на|в)\s+)?'+atom+r')+)'
    modifiers={'нужно','надо','необходимо','затем','потом','завтра','сегодня','послезавтра','утром','вечером','сначала',
               'следующей','следующую','следующая','следущей','следущую','следущая',
               'проверь','проверьте','посмотри','посмотрите','закрой','закройте','открой','откройте','полей','полейте','убери','уберите'}
    for match in re.finditer(pattern,t):
        members=[re.sub(r'^(?:на|в)\s+','',part) for part in re.split(r'\s+(?:и|или|либо)\s+',match.group(1))]
        def nominal(member):
            return member not in modifiers and not re.fullmatch(IRRIGATION_REWORK,member) and not member.endswith(('ть','ти','чь','йте')) and not any(re.fullmatch(p,member) for p in CATEGORIES.values())
        # The older exact matcher negates a directly prefixed member only.
        # Never let a later member of one negated list become positive scope.
        if re.search(r'\bне\s*$',t[:match.start()]) and all(member in aliases or nominal(member) for member in members):return True
        if any(member in aliases for member in members) and any(member not in aliases and nominal(member) for member in members):return True
    return False

def task_audience(raw, workers=(), *, areas=()):
    """Audience evidence is independent of detecting an instruction.

    A bare infinitive, numbered task or imperative does not establish that
    this is the owner's personal task. Keep that choice open for review.
    """
    t=fold(raw)
    # Explicit other-person/owner contrast supplies audience evidence that a
    # bare imperative lacks. Scheduling prompts ("поставь мне") stay personal.
    if re.search(r'\b(?:ты|вы)\b[^.!?;]*\b(?:мне|для\s+меня|на\s+мое\s+имя)\b',t):return 'WORKER_TASK'
    if re.search(r'\b(?:заедьте|езжайте|возьмите)\b',t) and re.search(r'\bна\s+мое\s+имя\b',t):return 'WORKER_TASK'
    personal=re.search(r'(?<!не )\b(?:для\s+меня|моя\s+задача|мое\s+дело|мой\s+список|себе|я\s+(?:сам|сама)|(?:напомни|запиши|поставь)\s+мне)\b',t)
    if re.search(r'\bмне\b[^.!?;]{0,60}\bнужна\s+задача\b',t):personal=True
    if personal:return 'PERSONAL_TASK'
    # The owner speaking in first person is not the intended executor of a
    # delegation or a desired subordinate action ("мне нужно, чтобы …").
    if re.search(r'(?<!не )\b(?:поручаю|поручи(?:ть|те)?)\b',t):return 'WORKER_TASK'
    desired=re.search(r'\b(?:нужно|надо|необходимо)\s*,?\s*чтобы\s+(.*)',t)
    subject=task_actor_scope(desired.group(1) if desired else t,workers=workers)
    subject=re.sub(r'^\s*(?:пускай|пусть)\s+', '', subject)
    # Full names and shared current person candidates establish audience, not
    # a unique ID. A lone generic/partial reference still establishes neither.
    people=entity_matches(subject,workers,'worker',inflect=True)
    recipient=(explicit_entity_mentions(subject,workers,'worker',inflect=True) or len(people)>1
               or task_recipient_evidence(subject,areas=areas,subjunctive=bool(desired)))
    role=re.search(r'(?<!не )\b(?:работник(?:и|у|ам)?|брат(?:у)?|ребята|исполнитель|мастер|для\s+(?:работник(?:а|ов)|исполнителя|брата|мастера))\b',subject)
    if desired and (recipient or role):return 'WORKER_TASK'
    if re.search(r'\bнапомни(?:ть)?\b',t) and (recipient or role):return 'WORKER_TASK'
    # First-person future intention and an explicit personal reminder are
    # positive personal evidence; the bare word "я" is not ("я поручаю").
    if re.search(r'\b(?:я\s+(?:(?:сегодня|завтра)\s+)?(?:проверю|позвоню|сделаю|заберу|полю|полью)|куплю|распишу|разберусь|напомни(?:ть)?\s+(?!работник|ему|ей|им))\b',t):return 'PERSONAL_TASK'
    if not desired and re.search(r'(?<!не )\b(?:мне\s+(?:же\s+)?(?:нужно|надо)|я\s+(?:завтра|сегодня))\b',t):return 'PERSONAL_TASK'
    if recipient or role:return 'WORKER_TASK'
    # An omitted actor with an intentional directive predicate is delegation.
    # An explicit inanimate subject before that predicate is not this frame.
    if re.search(r'\b(?:пускай|пусть)\s+(?:выключает|включает|доделает|проверит|починит|поливает|закрывает|открывает)(?!\w)',t):return 'WORKER_TASK'
    if nonfinancial_modal(t) and re.search(r'\b(?:он|она|один из)\b',subject):return 'WORKER_TASK'
    return 'UNKNOWN'


def extract_task(raw,*,areas=(),workers=(),today=None):
    semantic=corrected(raw,areas=areas,workers=workers,task_dates=True);t=fold(semantic)
    if meta_or_report(semantic):return None
    if re.search(r'\b(?:чтобы\s+(?:ты|вы|он)\s+мог\w*|я\s+тебе\s+напишу|хотел\s+напомнить|записано\s+в\s+плане|можешь\s+найти\s+фото)\b',t):return None
    if re.search(r'\b(?:нужно ли|обещал\w*|отработал\w*|поручили|проверить вручную)\b',t):return None
    if re.fullmatch(r'(?:проверь|проверьте|проверить|посмотри|посмотрите)(?:\s+(?:еще|раз|пожалуйста))*[.!]?',t.strip()):return None
    category='irrigation' if irrigation_rework(t) else next((k for k,p in CATEGORIES.items() if re.search(r'(?<!\w)(?:'+p+r')(?!\w)',t)),None)
    if not category and re.search(r'\bя\s+(?:(?:сегодня|завтра)\s+)?(?:проверю|позвоню|сделаю|заберу|полью)\b',t):category='general'
    # In a desired-action clause these past forms are subjunctive instructions,
    # never completed-work evidence. Require the explicit modal + чтобы frame.
    desired=re.search(r'\b(?:нужно|надо|необходимо)\s*,?\s*чтобы\b(.*)',t)
    if not category and desired:
        category=next((kind for kind,verbs in {'irrigation':r'полил\w*', 'ventilation':r'(?:закрыл|открыл)\w*', 'cleanup':r'убрал\w*', 'inspection':r'проверил\w*', 'repair':r'починил\w*'}.items() if re.search(r'\b(?:'+verbs+r')\b',desired.group(1))),None)
    # Bounded declarative instruction idioms remain instructions, not actuals.
    if not category and re.search(r'\bзакрыть\s+нужно\b',t):category='ventilation'
    if not category and re.search(r'\b(?:нужна\b.*\bмашина|сначала\b.*\bводы\b.*\bпотом|максимум\s+даем\b.*\bминут)',t):category='general'
    restriction=re.split(r'[,;]\s*(?:со\s+склада\s+)?пока\s+не\s+списывать\b',t,maxsplit=1)
    action_scope=restriction[0] if len(restriction)>1 and category in ('fertilizer','irrigation') else t
    if re.search(r'\bне\s+(?:назнача(?:ю|й(?:те)?|ть)|назнач(?:ить|ь(?:те)?)|запиши(?:те)?|записывай(?:те)?)\b',action_scope):return None
    if re.search(r'\bне\s+(?:'+'|'.join(pattern for kind,pattern in CATEGORIES.items() if kind!='general')+r'|куплю|распишу|разберусь|проверю|позвоню|сделаю|заберу|полью|поручаю|поручить)\b',action_scope):return None
    if re.search(r'\b(?:не могу|не может|не успею|не смогу|попробую|не поливать|не\s+(?:поручай\w*|поручать|нужно|надо)|пока не (?:поливать|вносить|списывать|открывать|закрывать)|попроще|я бы хотел|мог бы|можете)\b',action_scope):return None
    if not category or re.search(r'\b(?:обещали|планирую|планировал|рекоменд\w*|совету\w*|наверное|возможно)\b',t):return None
    if re.search(r'\b(?:должен|должна|должны)\b',t) and not nonfinancial_modal(t):return None
    if '?' in t and not re.search(r'\b(?:проверь|проверьте|проверить)\b',t):return None
    if re.search(r'\b(?:сколько|как\s+быстро|сможешь|полил\s+ли|закрыл\s+ли)\b',t) and not re.search(r'\b(?:проверь|проверьте|проверить|пиши|говори|поставь|запиши|не\s+забудь\s+написать)\b',t):return None
    if re.search(r'\bони\b.*\bприходят\b',t):return None
    today=today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    if re.search(r'\bсравни\b',t) and not date_value(semantic,today,role='planned')[0] and task_audience(semantic,workers,areas=areas)!='WORKER_TASK':return None
    f=dict(title=raw[:200],original_instruction=raw,description=raw,category=category,priority='normal');gaps=[]
    relative=relative_time_context(semantic)
    day,_=date_value(re.split(r'(?i)[-—]\s*я\s+скажу\b',semantic)[0],today,role='planned')
    if day:f['planned_date']=day
    else:gaps.append('На какой день следующей недели поставить?' if relative=='следующая неделя' else 'Укажите точную дату задачи; срок и время не угаданы')
    mentioned=entity_matches(semantic,[a for a in areas if value(a,'kind','greenhouse')=='greenhouse'],'area')
    if len(mentioned)==1 and not ambiguous_area_coordination(semantic,areas):f['greenhouse_id']=str(value(mentioned[0],'id'))
    else:gaps.append('Выберите теплицу; место неоднозначно или неизвестно')
    if re.search(r'(част[ьи]|верх|низ|средн)',t):gaps.append('Часть теплицы требует выбора из текущего справочника')
    # A contacted/helped/recruited person is not the grammatical assignee.
    subject=task_actor_scope(semantic,workers=workers)
    matches=entity_matches(subject,workers,'worker',inflect=True,require_explicit=True)
    # A recognized person next to an unrecognized/alternative co-addressee is
    # not the sole assignee. Never drop the unknown member of an explicit list.
    coordinated=re.match(r'^\s*(?:(?:для|работникам|поручи(?:ть|те)?)\s+)?([^\W\d_][\w-]+(?:\s+(?:и|или|либо)\s+[^\W\d_][\w-]+)+)\s+(?:сегодня|завтра|послезавтра|'+NEXT_WEEK+'|'+'|'.join(CATEGORIES.values())+r')\b',semantic,re.I)
    if coordinated:matches=[]
    if re.search(r'(?<!не )\bдля\s+меня\b',t):matches=[]
    f['suggested_worker_ids']=[str(value(matches[0],'id'))] if len(matches)==1 else []
    if len(matches)!=1:gaps.append('Выберите работников; неизвестное или неоднозначное имя не назначено')
    if re.search(r'(ежедневно|каждый день)',t):f['recurrence_suggestion']={'frequency':'daily'}
    elif re.search(r'(каждую пятницу|по пятницам)',t):f['recurrence_suggestion']={'frequency':'weekly','weekday':4}
    elif 'раз в неделю' in t:gaps.append('Повтор раз в неделю: нужен явный день')
    if 'утром' in t or 'вечером' in t:gaps.append('Утро/вечер сохранены в поручении; точное время уточните')
    return 'TASKS',f,gaps
