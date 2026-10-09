"""Conservative transcript extraction shared by text and voice. Never posts."""
import re
from datetime import datetime,date
from zoneinfo import ZoneInfo
from .preparation_language import AREA_ALIASES, corrected, date_value, entity_matches, fold, value

def extract_harvest(text,*,counterparties=(),areas=(),today=None):
    t=fold(corrected(text,areas=areas,counterparties=counterparties));f={};g=[]
    if not any(w in t for w in ('срезал','срезали','срезано','деревьев','принял','депо','банан','урожай')):return None
    # Actual payments/advances keep their existing Finance path.
    if any(w in t for w in ('аванс','получил деньги','получил оплату','депо заплатил','депо оплатил')):return None
    today=today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    if any(w in t for w in ('срезал','срезали','срезано')):f['kind']='report'
    elif ('машин' in t or 'грузовик' in t) and ('отправил' in t or 'отправили' in t):f['kind']='batch'
    else:f['kind']='settlement'
    event,_=date_value(t,today)
    if event:f['event_date']=event
    else:g.append('Укажите дату события; дата ввода её не заменяет.')
    matches=[]
    aliases={**AREA_ALIASES,'Özvadi':(*AREA_ALIASES['Özvadi'],'poncho'),'Yeşilöz':(*AREA_ALIASES['Yeşilöz'],'arabik'),'İmamlı':(*AREA_ALIASES['İmamlı'],'green house','greenhouse')}
    for a in areas:
        if getattr(a,'kind','greenhouse')!='greenhouse' or value(a,'active',True) is False:continue
        found=next((re.search(r'(?<!\w)'+re.escape(w)+r'(?!\w)',t) for w in [fold(a.name),*(fold(n) for n in aliases.get(a.name,()))] if re.search(r'(?<!\w)'+re.escape(w)+r'(?!\w)',t)),None)
        if found:
            source_name=next((m.group() for alias in [a.name,*aliases.get(a.name,())] if (m:=re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',text,re.I))),None)
            matches.append((a,source_name or text))
    if len(matches)==1:f.update(original_greenhouse=matches[0][1],greenhouse_id=str(matches[0][0].id))
    else:g.append('Выберите теплицу; неизвестное или смешанное место не угадывается.')
    if f['kind']=='report':
        counts=re.findall(r'(\d+)\s*(?:дерев\w*|куст\w*|кист\w*|грозд\w*)',t)
        if len(counts)==1:f['cut_count']=int(counts[0])
        else:g.append('Количество срезанных деревьев неизвестно; оставьте пустым или уточните.')
        g.append('Укажите, кто сообщил о срезе.')
    elif f['kind']=='batch':
        f['label']=text;g.append('Свяжите только подтверждённые отчёты; близость дат не доказывает связь.')
    else:
        g.append('Продажа требует расчёта депо и исходного чека; сообщение само не проводится.')
        if 'долж' in t:f['direction']='receivable'
        deps=[cp for cp in counterparties if getattr(cp,'counterparty_type','both') in ('depot','customer','both') and (cp.name.casefold() in t or (getattr(cp,'counterparty_type','both')=='depot' and len(cp.name.split()[0])>=4 and re.search(r'(?<!\w)'+re.escape(cp.name.split()[0].casefold())+r'(?!\w)',t)))]
        if len(deps)==1:f['depot_id']=str(deps[0].id)
        else:g.append('Выберите депо из существующих контрагентов.')
        weights=re.findall(r'(\d+(?:[.,]\d{1,3})?)\s*кг',t)
        prices=re.findall(r'по\s+(\d+(?:[.,]\d{1,2})?)\s*(лир\w*|try|₺)',t)
        qs=re.findall(r'(?<!\w)(sera|grand|yerli|i̇kinci|ikinci|2 inci|kava)(?!\w)',t)
        if len(weights)==len(prices)==len(qs)==1:
            q=re.search(re.escape(qs[0]),t)
            f['lines']=[dict(original_quality_name=text[q.start():q.end()],net_weight_kg=weights[0].replace(',','.'),unit_price=prices[0][0].replace(',','.'),source_line='text:1')];f['currency']='TRY'
        else:g.append('Проверьте отдельные строки чека: качество, кг, цена, исходная сумма.')
        amounts=re.findall(r'(\d+(?:[.,]\d{1,2})?)\s*(?:лир\w*|try|₺)',t)
        if len(amounts)==1 and 'долж' in t:f.update(alacak_value=amounts[0].replace(',','.'),currency='TRY')
        due=re.findall(r'до\s+(\d{1,2})\s+(октября|ноября|декабря)(?:\s+(20\d{2}))?',t)
        if len(due)==1:
            d,m,year=due[0]
            if year:
                try:f['due_date']=date(int(year),{'октября':10,'ноября':11,'декабря':12}[m],int(d)).isoformat()
                except ValueError:g.append('Исправьте ожидаемую дату оплаты.')
            else:g.append('Укажите год ожидаемой оплаты; он не подставляется автоматически.')
        if re.search(r'\d{1,2}\s+(октября|ноября|декабря)(?!\s+20\d{2})',t) and not due:g.append('Укажите год ожидаемой оплаты; он не подставляется автоматически.')
        g.append('Выберите фактическую оплату, долг или аванс только после проверки чека. Alacak не является оплатой.')
    return 'HARVEST_DEPOT',f,g
