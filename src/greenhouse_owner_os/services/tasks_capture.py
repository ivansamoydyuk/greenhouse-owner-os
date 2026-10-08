"""Conservative instruction extraction. No historical worker names or IDs."""
import re
from datetime import datetime,timedelta,date
from zoneinfo import ZoneInfo
from .capture import AREA_ALIASES
CATEGORIES={'irrigation':('полить','поливай','поливать'), 'ventilation':('открыть окна','закрыть окна'), 'harvest':('режет','режьте','срезать','порезать'), 'cleanup':('убрать','расчистить'), 'inspection':('проверьте','проверить'), 'fertilizer':('внести','внесите'), 'repair':('отремонтировать','починить'), 'transport':('отвезти','привезти'), 'general':('задача','поручение','помогать','заплатить')}
def extract_task(raw,*,areas=(),workers=(),today=None):
    t=raw.casefold(); category=next((k for k,terms in CATEGORIES.items() if any(w in t for w in terms)),None)
    if not category or any(w in t for w in ('должен','должны','обещали','планирую')):return None
    today=today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    f=dict(title=raw[:200],original_instruction=raw,description=raw,category=category,priority='normal');gaps=[]
    dates=[]
    if re.search(r'\bзавтра\b',t):dates.append(today+timedelta(days=1))
    if re.search(r'\bсегодня\b',t):dates.append(today)
    for iso in re.findall(r'\b20\d{2}-\d{2}-\d{2}\b',t):
        try:dates.append(date.fromisoformat(iso))
        except ValueError:gaps.append('Исправьте дату задачи')
    if len(set(dates))==1:f['planned_date']=dates[0].isoformat()
    else:gaps.append('Укажите точную дату задачи; срок и время не угаданы')
    mentioned=[a for a in areas if getattr(a,'kind','greenhouse')=='greenhouse' and any(alias in t for alias in AREA_ALIASES.get(a.name,(a.name.casefold(),)))]
    if len(mentioned)==1:f['greenhouse_id']=str(mentioned[0].id)
    elif 'всех теплиц' in t:gaps.append('Все теплицы: проверьте охват в описании; одна теплица не подставлена')
    else:gaps.append('Выберите теплицу; место неоднозначно или неизвестно')
    if re.search(r'(част[ьи]|верх|низ|средн)',t):gaps.append('Часть теплицы требует выбора из текущего справочника')
    matches=[w for w in workers if w['active'] and any(re.search(r'(?<!\w)'+re.escape(n.casefold())+r'(?!\w)',t) for n in (w['display_name'],w.get('native_name')) if n)]
    f['suggested_worker_ids']=[w['id'] for w in matches] if len(matches)==1 else []
    if len(matches)!=1:gaps.append('Выберите работников; неизвестное или неоднозначное имя не назначено')
    if re.search(r'(ежедневно|каждый день)',t):f['recurrence_suggestion']={'frequency':'daily'}
    elif re.search(r'(каждую пятницу|по пятницам)',t):f['recurrence_suggestion']={'frequency':'weekly','weekday':4}
    elif 'раз в неделю' in t:gaps.append('Повтор раз в неделю: нужен явный день')
    if 'утром' in t or 'вечером' in t:gaps.append('Утро/вечер сохранены в поручении; точное время уточните')
    return 'TASKS',f,gaps
