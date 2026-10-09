"""Conservative instruction extraction from supplied, current references only."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
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
    'general':r'задача|поручение|поручаю|твоя\s+работа|работа\s+для|вызывать|вызвать|спросить|куплю|распишу|перевести\s+деньги|(?:пускай|пусть)\s+выключает|не\s+забудь\s+написать|помогать|помочь|помоги|помогите|подвязать|поднять|собрать|позвонить|связаться|договориться|договорись|напомни|напомнить|уточнить|сверить|отправить\s+фото|пришли\s+фото|сфотографировать|прислать|сообщить|продолжить|берите|приходи|ставите|проходите|пиши|говори|разберусь|сделай|спрятать|отдавайте|экономить|сфотографируй|сравнить|сравни|подписать|подготовить|посчитай|посчитать|оставь|убери|приступить|поставь\s+мне|себе\s+запиши|запиши\s+(?:мне|себе)|себе\s+записать|найти|покажи|(?:скажи|попроси)\b.*\bчтобы\b.*\b(?:сфотографировал|прислал|полил|закрыл)',
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
    t=fold(raw)
    if re.search(r'\b(?:для\s+меня|моя\s+задача|мне\s+(?:же\s+)?нужно|себе|я\s+(?:сам|завтра|сегодня))\b',t):return 'PERSONAL_TASK'
    if explicit_entity_mentions(raw,workers,'worker',inflect=True):return 'WORKER_TASK'
    if nonfinancial_modal(t) and re.search(r'\b(?:он|она|один из)\b',t):return 'WORKER_TASK'
    if re.search(r'\b(?:работник\w*|брат(?:у|а)?|ребята|ты|вы|тебе|заедьте|езжайте|возьмите|пускай|пусть|берите|берешь|наливаешь|ставите|проходите)\b',t):return 'WORKER_TASK'
    # A named subject of a future third-person instruction remains a worker
    # candidate even when that person is absent from current references.
    if re.search(r'(?:^|\s)[A-ZА-ЯЁİŞÖÜÇ][\w-]+(?:\s+[A-ZА-ЯЁİŞÖÜÇ][\w-]+)?\s+(?:починит|польет|польёт|закроет|откроет|режет)(?!\w)',raw):return 'WORKER_TASK'
    # Unknown named addressee is a reviewable worker card, never a guessed ID.
    first=re.match(r'^\s*([^\W\d_][\w-]+)(?:\s*,|\s+(?:сегодня|завтра|послезавтра|'+NEXT_WEEK+r')\b)',raw,re.I)
    area_names={fold(alias) for area in areas for alias in names(area,'area')}
    if first and fold(first.group(1)) not in area_names and not re.fullmatch(IRRIGATION_REWORK+'|'+IRRIGATION_OBJECT,fold(first.group(1))) and not fold(first.group(1)).endswith(('ть','йте')) and fold(first.group(1)) not in {'я','мы','мне','нам','полив','шланг','трубу','труба','документы','на','во','в','сегодня','завтра','послезавтра','вчера','напомни','проверь','проверьте','нужно','надо','необходимо','бензин','насос','теплицу','электричество','траву','бурьян','сначала','потом','после','здесь','хорошо'}:return 'WORKER_TASK'
    return 'PERSONAL_TASK'


def extract_task(raw,*,areas=(),workers=(),today=None):
    semantic=corrected(raw,areas=areas,workers=workers,task_dates=True);t=fold(semantic)
    if meta_or_report(semantic):return None
    if re.search(r'\b(?:чтобы\s+(?:ты|вы|он)\s+мог\w*|я\s+тебе\s+напишу|хотел\s+напомнить|записано\s+в\s+плане|можешь\s+найти\s+фото)\b',t):return None
    if re.search(r'\b(?:нужно ли|обещал\w*|отработал\w*|поручили|проверить вручную)\b',t):return None
    if re.fullmatch(r'(?:проверь|проверьте|проверить|посмотри|посмотрите)(?:\s+(?:еще|раз|пожалуйста))*[.!]?',t.strip()):return None
    category='irrigation' if irrigation_rework(t) else next((k for k,p in CATEGORIES.items() if re.search(r'(?<!\w)(?:'+p+r')(?!\w)',t)),None)
    # Bounded declarative instruction idioms remain instructions, not actuals.
    if not category and re.search(r'\bзакрыть\s+нужно\b',t):category='ventilation'
    if not category and re.search(r'\b(?:нужна\b.*\bмашина|сначала\b.*\bводы\b.*\bпотом|максимум\s+даем\b.*\bминут)',t):category='general'
    restriction=re.split(r'[,;]\s*(?:со\s+склада\s+)?пока\s+не\s+списывать\b',t,maxsplit=1)
    action_scope=restriction[0] if len(restriction)>1 and category in ('fertilizer','irrigation') else t
    if re.search(r'\b(?:не могу|не может|не успею|не смогу|попробую|не поливать|пока не (?:поливать|вносить|списывать|открывать|закрывать)|попроще|я бы хотел|мог бы|можете)\b',action_scope):return None
    if not category or re.search(r'\b(?:обещали|планирую|планировал|рекоменд\w*|совету\w*|наверное|возможно)\b',t):return None
    if re.search(r'\b(?:должен|должна|должны)\b',t) and not nonfinancial_modal(t):return None
    if '?' in t and not re.search(r'\b(?:проверь|проверьте|проверить)\b',t):return None
    if re.search(r'\b(?:сколько|как\s+быстро|сможешь|полил\s+ли|закрыл\s+ли)\b',t) and not re.search(r'\b(?:проверь|проверьте|проверить|пиши|говори|поставь|запиши|не\s+забудь\s+написать)\b',t):return None
    if re.search(r'\bони\b.*\bприходят\b',t):return None
    today=today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    if re.search(r'\bсравни\b',t) and not date_value(semantic,today,role='planned')[0] and task_audience(semantic,workers,areas=areas)=='PERSONAL_TASK':return None
    f=dict(title=raw[:200],original_instruction=raw,description=raw,category=category,priority='normal');gaps=[]
    relative=relative_time_context(semantic)
    day,_=date_value(re.split(r'(?i)[-—]\s*я\s+скажу\b',semantic)[0],today,role='planned')
    if day:f['planned_date']=day
    else:gaps.append('На какой день следующей недели поставить?' if relative=='следующая неделя' else 'Укажите точную дату задачи; срок и время не угаданы')
    mentioned=entity_matches(semantic,[a for a in areas if value(a,'kind','greenhouse')=='greenhouse'],'area')
    if len(mentioned)==1 and not ambiguous_area_coordination(semantic,areas):f['greenhouse_id']=str(value(mentioned[0],'id'))
    else:gaps.append('Выберите теплицу; место неоднозначно или неизвестно')
    if re.search(r'(част[ьи]|верх|низ|средн)',t):gaps.append('Часть теплицы требует выбора из текущего справочника')
    # A person receiving help is not the grammatical assignee.
    subject=re.split(r'(?i)\b(?:помочь|помогать)\b',semantic)[0]
    matches=entity_matches(subject,workers,'worker',inflect=True,require_explicit=True) if subject!=semantic else entity_matches(semantic,workers,'worker',inflect=True,require_explicit=True)
    if re.search(r'\bдля\s+меня\b',t):matches=[]
    f['suggested_worker_ids']=[str(value(matches[0],'id'))] if len(matches)==1 else []
    if len(matches)!=1:gaps.append('Выберите работников; неизвестное или неоднозначное имя не назначено')
    if re.search(r'(ежедневно|каждый день)',t):f['recurrence_suggestion']={'frequency':'daily'}
    elif re.search(r'(каждую пятницу|по пятницам)',t):f['recurrence_suggestion']={'frequency':'weekly','weekday':4}
    elif 'раз в неделю' in t:gaps.append('Повтор раз в неделю: нужен явный день')
    if 'утром' in t or 'вечером' in t:gaps.append('Утро/вечер сохранены в поручении; точное время уточните')
    return 'TASKS',f,gaps
