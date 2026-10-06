"""Conservative shared text/voice parser; verified master data only."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from greenhouse_owner_os.services.capture import AREA_ALIASES

UNITS={'кг':'kg','килограмм':'kg','килограмма':'kg','килограммов':'kg','kg':'kg','л':'L','литр':'L','литра':'L','литров':'L','литры':'L','l':'L','мешок':'bag','мешка':'bag','мешков':'bag','мешки':'bag','bag':'bag','шт':'piece','штук':'piece','piece':'piece','бидон':'container','бидона':'container','бидонов':'container','упаковок':'package','упаковки':'package'}
def extract_inventory(raw,*,products=(),locations=(),areas=(),today=None):
    t=raw.casefold()
    if re.search(r'\b(перевезли|переместили|перемещение)\b',t):kind='transfer'
    elif re.search(r'\b(привезли|приход|получили товар|доставили)\b',t):kind='receipt'
    elif re.search(r'\b(дали|внесли|внесение|внести|план внесения)\b',t):kind='plan' if re.search(r'\b(план|планируем|внести)\b',t) else 'application'
    else:return None
    gaps=[];fields=dict(kind=kind,lines=[],notes=raw)
    if re.search(r'\bсегодня\b',t):fields['event_date']=(today or datetime.now(ZoneInfo('Europe/Istanbul')).date()).isoformat()
    else:gaps.append('Укажите дату фактического события.')
    gs=[a for a in areas if a.kind=='greenhouse' and any(re.search(r'(?<!\w)'+re.escape(n)+r'(?!\w)',t) for n in AREA_ALIASES.get(a.name,(a.name.casefold(),)))]
    gid=str(gs[0].id) if len(gs)==1 else None
    if kind in ('application','plan') and not gid:gaps.append('Выберите проверенную теплицу.')
    ls=[l for l in locations if re.search(r'(?<!\w)'+re.escape(l['name'].casefold())+r'(?!\w)',t)]
    loc=ls[0]['id'] if len(ls)==1 else None;dest=None
    if kind=='transfer':
        sources=[l for l in ls if re.search(r'(?:со|из|с)\s+'+re.escape(l['name'].casefold())+r'(?!\w)',t)]
        targets=[l for l in ls if re.search(r'(?:в|на)\s+'+re.escape(l['name'].casefold())+r'(?!\w)',t)]
        if len(sources)==1 and len(targets)==1:loc=sources[0]['id'];dest=targets[0]['id']
        else:gaps.append('Выберите исходный склад и склад назначения.')
    if not loc:gaps.append('Выберите склад: место внесения не означает склад хранения.')
    pattern=r'(?<![\w.+-])(\d{1,18}(?:[,.]\d{1,12})?)\s*(килограммов|килограмма|килограмм|литров|литра|литры|литр|мешков|мешка|мешки|мешок|бидонов|бидона|бидон|упаковок|упаковки|штук|piece|bag|kg|кг|шт|л|l)(?!\w)'
    matches=list(re.finditer(pattern,t))
    for idx,m in enumerate(matches):
        end=matches[idx+1].start() if idx+1<len(matches) else len(t)
        original=raw[m.end():end].strip(' ,.;')
        original=re.split(r'(?i)\s+(?:со склада|со |из |в |на |и\s*$)',original)[0].strip(' ,.;') or 'Неизвестный товар'
        known=[]
        for p in products:
            aliases=[p['name'],*p['aliases']]
            if any(re.search(r'(?<!\w)'+re.escape(n.casefold())+r'(?!\w)',t[m.end():end]) for n in aliases):known.append(p)
        product=known[0] if len(known)==1 else None
        if not product:gaps.append('Неизвестный/неоднозначный товар: '+original)
        unit=UNITS[m.group(2)];qty=m.group(1).replace(',','.')
        ln=dict(original_product_name=original,original_quantity=raw[m.start():m.end()],original_unit=raw[m.start(2):m.end(2)],quantity=qty,unit=unit,product_id=product['id'] if product else None,location_id=loc,greenhouse_id=gid,destination_id=dest)
        if unit in ('bag','container','package'):gaps.append('Проверьте размер упаковки в справочнике товара.')
        fields['lines'].append(ln)
    if not matches:
        fields['lines']=[dict(original_product_name=raw[:200])];gaps.append('Укажите точное количество и единицу каждого товара.')
    return 'INVENTORY',fields,list(dict.fromkeys(gaps))
