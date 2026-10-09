"""Conservative shared text/voice parser; verified master data only."""
import re
from datetime import datetime
from zoneinfo import ZoneInfo
from .preparation_language import NUMBER, UNIT, corrected, date_value, entity_matches, fold, names, number, value

UNITS={'кг':'kg','килограмм':'kg','килограмма':'kg','килограммов':'kg','kg':'kg','л':'L','литр':'L','литра':'L','литров':'L','литры':'L','l':'L','мешок':'bag','мешка':'bag','мешков':'bag','мешки':'bag','bag':'bag','шт':'piece','штук':'piece','штука':'piece','штуки':'piece','piece':'piece','бидон':'container','бидона':'container','бидонов':'container','упаковок':'package','упаковки':'package','упаковка':'package'}

def extract_inventory(raw,*,products=(),locations=(),areas=(),today=None):
    semantic=corrected(raw,products=products,locations=locations,areas=areas)
    # An explicit remainder/stock-status postscript is evidence, not another
    # application line or its event date. It remains verbatim in notes.
    semantic=re.split(r'(?i)[,;]\s*(?:осталось|остаток)\b',semantic)[0]
    contrast=re.split(r'(?i),\s*но\s+',semantic,maxsplit=1)
    if len(contrast)==2 and re.search(r'(?i)\bне\s+(?:привез\w*|достав\w*|получ\w*)\b',contrast[1]):
        supplied=entity_matches(contrast[0],products);absent=entity_matches(contrast[1],products)
        if supplied and absent and not {str(value(p,'id')) for p in supplied}&{str(value(p,'id')) for p in absent}:semantic=contrast[0]
    t=fold(semantic)
    if '?' in t or re.search(r'\b(?:ли|если|когда)\b',t):return None
    quantities=[m for m in re.finditer(r'(?<![\w.+-])('+NUMBER+r')\s*('+UNIT+r')(?!\w)',t) if not re.search(r'\bне\s*$',t[:m.start()])]
    if re.search(r'\b(?:не|нельзя)\s+(?:привез\w*|получ\w*|достав\w*|внес\w*|дали|списы\w*|израсходовал\w*|использовал\w*|принял\w*|принят\w*|оприходован\w*|применил\w*|ушло)\b|\bне\s+(?:новый\s+)?(?:нужен|приход|применение)\b|\b(?:вернул\w*|перелил\w*)\b',t):return None
    if re.search(r'\b(перевезли|переместили|перемещение)\b',t):kind='transfer'
    elif re.search(r'\b(привезли|привез|привезла|приход|получили товар|доставили|поступило|поступили|приняли\s+поставку|оприходован(?:а|о|ы)?\s+физически|полученная\s+(?:вчера\s+|сегодня\s+)?партия|склад\b[^.!?]{0,40}\bпринял)\b',t) or quantities and re.search(r'\bполучил(?:и|а)?\b',t):kind='receipt'
    elif re.search(r'\b(внесли|внес|внесла|внесение|внести|план внесения|израсходовали|использовали|применили|сделанному\s+внесению|фактически\b[^.!?]{0,90}\bушло)\b',t) or quantities and re.search(r'\bдали\b',t):
        kind='plan' if re.search(r'\b(план|планируем|внести)\b',t) else 'application'
    else:return None
    if re.search(r'\b(завтра|послезавтра|планируем|рекоменд\w*|собираемся)\b',t):kind='plan'
    gaps=[];fields=dict(kind=kind,lines=[],notes=raw)
    today=today or datetime.now(ZoneInfo('Europe/Istanbul')).date()
    event,_=date_value(semantic,today)
    if event:fields['event_date']=event
    else:gaps.append('Укажите дату фактического события.')
    gs=entity_matches(semantic,[a for a in areas if value(a,'kind','greenhouse')=='greenhouse'],'area')
    gid=str(value(gs[0],'id')) if len(gs)==1 else None
    if kind in ('application','plan') and not gid:gaps.append('Выберите проверенную теплицу.')
    ls=entity_matches(semantic,locations)
    loc=str(value(ls[0],'id')) if len(ls)==1 else None;dest=None
    if kind=='transfer':
        sources=[l for l in ls if re.search(r'(?:со|из|с)\s+'+re.escape(fold(value(l,'name')))+r'(?!\w)',t)]
        targets=[l for l in ls if re.search(r'(?:в|на)\s+'+re.escape(fold(value(l,'name')))+r'(?!\w)',t)]
        if len(sources)==len(targets)==1:loc=str(value(sources[0],'id'));dest=str(value(targets[0],'id'))
        else:gaps.append('Выберите исходный склад и склад назначения.')
    if not loc:gaps.append('Выберите склад: место внесения не означает склад хранения.')
    conflict=bool(re.search(r'\bодин\b.*\bговорит\b.*\bдругой\b',t))
    for idx,m in enumerate(quantities):
        if conflict and idx:continue
        end=quantities[idx+1].start() if idx+1<len(quantities) else len(t)
        start=quantities[idx-1].end() if idx else 0
        left=t[start:m.start()];right=t[m.end():end]
        right=re.split(r'[,;]?\s+(?:а\s+)?не\s+(?='+NUMBER+r')',right)[0]
        before=[p for p in products if value(p,'active',True) is not False and any(re.search(r'(?<!\w)'+re.escape(fold(n))+r'\s*[:=\-]?\s*$',left) for n in names(p))]
        known=before or entity_matches(right,products)
        if not known and len(quantities)==1:known=entity_matches(t,products)
        product=known[0] if len(known)==1 else None
        original=re.split(r'\s+(?:со склада|со |из |в |на |и\s*$)',right)[0].strip(' ,.;') or (value(product,'name') if product else 'Неизвестный товар')
        # Original evidence is carried verbatim even when a corrected comparison
        # view has different offsets. Never rewrite the retained source.
        orig_quant=m.group();orig_unit=m.group(2)
        exact=re.search(r'(?i)(?<!\w)'+re.escape(m.group(1))+r'\s*'+re.escape(orig_unit)+r'(?!\w)',raw)
        if exact:orig_quant=exact.group();orig_unit=raw[exact.end()-len(orig_unit):exact.end()]
        elif semantic!=raw:
            correction=re.search(r'(?i)\bне\s+'+NUMBER+r'\s*(?:'+UNIT+r')?\s*,\s*(?:а\s+)?'+NUMBER+r'\s*(?:'+UNIT+r')?',raw)
            if correction:orig_quant=correction.group()[:200]
        if product:
            spellings=[match.group() for n in names(product) if (match:=re.search(re.escape(n),raw,re.I))]
            if spellings:original=max(spellings,key=len)
        if not product:gaps.append('Неизвестный/неоднозначный товар: '+original)
        unit=UNITS[m.group(2)];qty=number(m.group(1))
        approximate=re.search(r'\b(?:около|примерно|приблизительно|почти|до|больше|меньше|не более)\s*$',left) or re.search(r'(?:от\s+'+NUMBER+r'\s+до|между\s+'+NUMBER+r'\s+и|'+NUMBER+r'\s*[-–])\s*$',left)
        metadata_quantity=bool(re.search(r'\b(?:рассчитан\w*|вмещает|емкость|содержимое|внутри неизвестно)\b',t) or re.search(r'\bна\s+(?:донюм|donum|га|гектар|декар)\b',right))
        if idx and UNITS[quantities[idx-1].group(2)] in ('bag','package','container') and re.search(r'\bпо\s*$',left):continue
        if conflict or metadata_quantity or approximate or len(quantities)==1 and re.search(r'\b(?:около|примерно|приблизительно|почти)\b',t):qty=None
        local_areas=entity_matches(right,[a for a in areas if value(a,'kind','greenhouse')=='greenhouse'],'area')
        line_gid=str(value(local_areas[0],'id')) if len(local_areas)==1 else gid
        local_locations=entity_matches(right,locations)
        line_loc=str(value(local_locations[0],'id')) if len(local_locations)==1 else loc
        if not qty:gaps.append('Укажите точное положительное количество.')
        fields['lines'].append(dict(original_product_name=original[:200],original_quantity=orig_quant,original_unit=orig_unit,quantity=qty,unit=unit,product_id=str(value(product,'id')) if product else None,location_id=line_loc,greenhouse_id=line_gid,destination_id=dest))
        if unit in ('bag','container','package'):gaps.append('Проверьте размер упаковки в справочнике товара.')
    if not quantities:
        known=entity_matches(t,products);product=known[0] if len(known)==1 else None
        fields['lines']=[dict(original_product_name=raw[:200],product_id=str(value(product,'id')) if product else None,location_id=loc,greenhouse_id=gid)];gaps.append('Укажите точное количество и единицу каждого товара.')
    return 'INVENTORY',fields,list(dict.fromkeys(gaps))
