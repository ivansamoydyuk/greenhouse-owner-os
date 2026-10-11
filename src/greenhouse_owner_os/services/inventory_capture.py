"""Conservative shared text/voice parser; verified master data only."""
import re
from datetime import datetime
from decimal import Decimal, localcontext
from zoneinfo import ZoneInfo
from .preparation_language import NUMBER, UNIT, corrected, date_value, entity_matches, fold, names, number, value

UNITS={'кг':'kg','килограмм':'kg','килограмма':'kg','килограммов':'kg','kg':'kg','л':'L','литр':'L','литра':'L','литров':'L','литры':'L','l':'L','мешок':'bag','мешка':'bag','мешков':'bag','мешки':'bag','bag':'bag','шт':'piece','штук':'piece','штука':'piece','штуки':'piece','piece':'piece','бидон':'container','бидона':'container','бидонов':'container','упаковок':'package','упаковки':'package','упаковка':'package'}

AREA_UNIT=r'(?:донюм(?:а|ов)?|donum|da|га|гектар(?:а|ов)?|декар(?:а|ов)?)'
RATE_DENOMINATOR=r'(?:(?:/|\bper)\s*(?:(?P<denominator>[^\s;/]+)\s*)?|\b(?:на|для)\s+(?:(?:каждый|каждого|каждую|один|одного|1)\s+|кажд(?:ые|ых)\s+(?P<plural_denominator>[^\s;/]+)\s+)?)(?P<area_unit>'+AREA_UNIT+r')(?!\w)'
DISTRIBUTIVE_DENOMINATOR=r'\bна\s+(?P<denominator>[^\s;/]+)\s*(?P<area_unit>'+AREA_UNIT+r')(?!\w)'
DOCUMENT_RECEIVED=r'\bполучил(?:и|а)?\s+(?:(?:новый|новую|этот|эту|поздний|позднюю)\s+)*(?:сч[её]т\w*|накладн\w*|документ\w*|invoice|fatura)\b'
NO_ARRIVAL=r'\b(?:(?:нового|физического)\s+)*(?:прихода|поступления|прибытия)(?:\s+товара)?\b[^.!?;]{0,120}\bне\s+(?:было|произошло)\b|\b(?:товар|поставк\w*|парти\w*)\b[^.!?;]{0,50}\bне\s+(?:привоз\w*|привез\w*|поступ\w*|приним\w*|принят\w*)\b'


def no_arrival_evidence(raw):
    return bool(re.search(NO_ARRIVAL,fold(raw)))


def is_rate_quantity(raw):
    """Used only to explain a missing actual quantity, never to authorize it."""
    t=fold(raw or '')
    return bool(re.search(RATE_DENOMINATOR,t) or re.search(r'\bпо\b',t) and re.search(DISTRIBUTIVE_DENOMINATOR,t))


def rate_quantity_clarification(original_quantity,notes):
    area=_area_evidence(notes or '')
    context=('; '+area['source_text']) if area else ''
    return 'Укажите общее количество: '+original_quantity+context+' — доза на площадь, не общий расход; проверьте площадь и указанный итог.'


def _area_evidence(raw):
    t=fold(raw)
    matches=list(re.finditer(r'\bплощад(?:ь|и)\s+(?:(?P<approx>около|примерно|приблизительно)\s+)?(?P<quantity>'+NUMBER+r')\s*(?P<unit>'+AREA_UNIT+r')(?!\w)',t))
    if len(matches)!=1:return None
    m=matches[0]
    partial=bool(re.search(r'\b(?:части|часть|частично|частично\s+обработали)\b',t))
    return dict(quantity=number(m['quantity']),unit=m['unit'],source_text=raw[m.start():m.end()] if len(raw)==len(t) else m.group(),scope='UNKNOWN' if m['approx'] or partial else 'EXPLICIT')


def _same_area_unit(left,right):
    def family(unit):return 'ha' if unit=='га' or unit.startswith('гектар') else 'da'
    return family(left)==family(right)


def extract_inventory(raw,*,products=(),locations=(),areas=(),today=None,preparation_evidence=None):
    return _inventory(raw,products=products,locations=locations,areas=areas,today=today,preparation_evidence=preparation_evidence)


def inventory_document_facts(raw,*,products=(),locations=(),areas=(),today=None):
    """Extract nonposting invoice line facts, without asserting goods arrival."""
    result=_inventory(raw,products=products,locations=locations,areas=areas,today=today,document_evidence=True)
    return [line for line in result[1]['lines'] if line.get('product_id') or line.get('original_quantity')] if result else []


def _inventory(raw,*,products=(),locations=(),areas=(),today=None,preparation_evidence=None,document_evidence=False):
    semantic=corrected(raw,products=products,locations=locations,areas=areas)
    # An explicit remainder/stock-status postscript is evidence, not another
    # application line or its event date. It remains verbatim in notes.
    semantic=re.split(r'(?i)[,;]\s*(?:осталось|остаток)\b',semantic)[0]
    contrast=re.split(r'(?i),\s*но\s+',semantic,maxsplit=1)
    if len(contrast)==2 and re.search(r'(?i)\bне\s+(?:привез\w*|достав\w*|получ\w*)\b',contrast[1]):
        supplied=entity_matches(contrast[0],products);absent=entity_matches(contrast[1],products)
        if supplied and absent and not {str(value(p,'id')) for p in supplied}&{str(value(p,'id')) for p in absent}:semantic=contrast[0]
    t=fold(semantic)
    if not document_evidence and re.search(DOCUMENT_RECEIVED,t):
        return None
    if '?' in t or re.search(r'\b(?:ли|если|когда)\b',t):return None
    quantities=[m for m in re.finditer(r'(?<![\w.+-])('+NUMBER+r')\s*('+UNIT+r')(?!\w)',t) if not re.search(r'\bне\s*$',t[:m.start()])]
    if not document_evidence and re.search(r'\b(?:не|нельзя)\s+(?:привез\w*|получ\w*|достав\w*|внес\w*|дали|списы\w*|израсходовал\w*|использовал\w*|принял\w*|принят\w*|оприходован\w*|применил\w*|ушло)\b|\bне\s+(?:новый\s+)?(?:нужен|приход|применение)\b|\b(?:вернул\w*|перелил\w*)\b',t):return None
    if document_evidence:kind='document'
    elif re.search(r'\b(перевезли|переместили|перемещение)\b',t):kind='transfer'
    elif re.search(r'\b(привезли|привез|привезла|приход|получили товар|доставили|поступило|поступили|приняли\s+поставку|оприходован(?:а|о|ы)?\s+физически|полученная\s+(?:вчера\s+|сегодня\s+)?партия|склад\b[^.!?]{0,40}\bпринял)\b',t) or quantities and re.search(r'\bполучил(?:и|а)?\b',t):kind='receipt'
    elif re.search(r'\b(внесли|внес|внесла|внесение|внести|план внесения|израсходовали|использовали|применили|сделанному\s+внесению|фактически\b[^.!?]{0,90}\bушло)\b',t) or quantities and re.search(r'\bдали\b',t):
        kind='plan' if re.search(r'\b(план|планируем|внести)\b',t) else 'application'
    else:return None
    if kind=='receipt' and no_arrival_evidence(t):return None
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
    quantity_evidence=[];area=_area_evidence(semantic)
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
        # A rate's denominator belongs to this quantity clause. The numeric
        # value is retained as evidence but is never an actual stock total.
        rate_scope=re.split(r'[,;.](?!\d)|[!?]',right,maxsplit=1)[0]
        rate=re.search(RATE_DENOMINATOR,rate_scope)
        distributive=re.search(r'\bпо\s*$',left)
        if not rate and distributive:rate=re.search(DISTRIBUTIVE_DENOMINATOR,rate_scope)
        total=bool(re.search(r'\b(?:всего|итого|общее\s+количество|общий\s+расход)\s*(?:фактически\s+)?(?:(?:внесли|внесено|израсходовали|ушло)\s*)?(?:фактически\s+)?$',left))
        role='RATE' if rate else 'EXPLICIT_TOTAL' if total else 'QUANTITY'
        evidence=dict(role=role,quantity=qty,unit=unit,source_quantity=orig_quant,product_id=str(value(product,'id')) if product else None)
        if rate:
            denominator=rate.groupdict().get('denominator') or rate.groupdict().get('plural_denominator')
            evidence.update(area_unit=rate['area_unit'],area_denominator=number(denominator) if denominator else '1',area=area)
            # Keep the full dose expression in the existing evidence field.
            # Raw offsets agree for ordinary numeric input; corrected input
            # keeps its complete original in notes and provenance instead.
            expression_start=m.start()-(len(left)-distributive.start()) if distributive else m.start()
            expression=semantic[expression_start:m.end()+rate.end()]
            orig_quant=raw[expression_start:m.end()+rate.end()] if raw==semantic else expression
            evidence['source_quantity']=orig_quant
        if area:evidence['area']=area
        approximate=re.search(r'\b(?:около|примерно|приблизительно|почти|до|больше|меньше|не более)\s*$',left) or re.search(r'(?:от\s+'+NUMBER+r'\s+до|между\s+'+NUMBER+r'\s+и|'+NUMBER+r'\s*[-–])\s*$',left)
        metadata_quantity=bool(re.search(r'\b(?:рассчитан\w*|вмещает|емкость|содержимое|внутри неизвестно)\b',t) or rate)
        if idx and UNITS[quantities[idx-1].group(2)] in ('bag','package','container') and re.search(r'\bпо\s*$',left):continue
        if conflict or metadata_quantity or approximate or len(quantities)==1 and re.search(r'\b(?:около|примерно|приблизительно|почти)\b',t):qty=None
        # A following explicit-total clause can introduce a different place
        # or part before its numeric token. Do not lend that context backwards
        # to this line; retain it as the next line's leading context instead.
        scoped_right=re.split(r'(?:[,;]\s*|\s+и\s+)(?=[^,;.?!]{0,150}\b(?:всего|итого|общее\s+количество|общий\s+расход)\b)',right,maxsplit=1)[0]
        leading_context=re.split(r'[,;]',left)[-1] if total or idx==0 else ''
        line_context=scoped_right+' '+leading_context
        local_areas=entity_matches(line_context,[a for a in areas if value(a,'kind','greenhouse')=='greenhouse'],'area')
        line_gid=str(value(local_areas[0],'id')) if len(local_areas)==1 else gid
        local_locations=entity_matches(line_context,locations)
        line_loc=str(value(local_locations[0],'id')) if len(local_locations)==1 else loc
        local_date,mentioned_date=date_value(line_context,today)
        part_scope=re.search(r'\b(?:(?:перв\w*|втор\w*|трет\w*|четверт\w*|\d+)\s+част\w*|част\w*\s*\d+)\b',line_context)
        evidence['scope']=dict(location_id=line_loc,greenhouse_id=line_gid,event_date=local_date if mentioned_date else event,part=part_scope.group() if part_scope else None)
        if not qty:gaps.append('Укажите общее количество: доза на площадь не является общим расходом.' if rate else 'Укажите точное положительное количество.')
        fields['lines'].append(dict(original_product_name=original[:200],original_quantity=orig_quant[:200],original_unit=orig_unit,quantity=qty,unit=unit,product_id=str(value(product,'id')) if product else None,location_id=line_loc,greenhouse_id=line_gid,destination_id=dest))
        quantity_evidence.append(evidence)
        if unit in ('bag','container','package'):gaps.append('Проверьте размер упаковки в справочнике товара.')
    if not quantities:
        known=entity_matches(t,products);product=known[0] if len(known)==1 else None
        fields['lines']=[dict(original_product_name=raw[:200],product_id=str(value(product,'id')) if product else None,location_id=loc,greenhouse_id=gid)];gaps.append('Укажите точное количество и единицу каждого товара.')
    # One explicit actual total can resolve one same-product/unit dose. It is
    # a source fact, not a computed posting. A contradictory exact rate/area
    # remains blocked; both values survive in structured preparation evidence.
    remove=set()
    for idx,evidence in enumerate(quantity_evidence):
        if evidence['role']!='EXPLICIT_TOTAL' or not evidence['product_id']:continue
        rates=[i for i,e in enumerate(quantity_evidence) if e['role']=='RATE' and (e['product_id'],e['scope'])==(evidence['product_id'],evidence['scope'])]
        totals=[e for e in quantity_evidence if e['role']=='EXPLICIT_TOTAL' and (e['product_id'],e['scope'])==(evidence['product_id'],evidence['scope'])]
        if len(rates)!=1 or len(totals)!=1:continue
        ridx=rates[0];rate_fact=quantity_evidence[ridx];rate_area=rate_fact.get('area')
        mismatch=rate_fact['unit']!=evidence['unit']
        if mismatch:evidence['unit_conflict']=dict(rate_unit=rate_fact['unit'],total_unit=evidence['unit'])
        if not rate_fact['quantity'] or not rate_fact['area_denominator'] or not evidence['quantity'] or rate_area and not rate_area['quantity']:
            mismatch=True
            evidence['invalid_scalar']=True
        if rate_area and not _same_area_unit(rate_fact['area_unit'],rate_area['unit']):
            mismatch=True
            evidence['area_unit_conflict']=dict(rate_area_unit=rate_fact['area_unit'],stated_area_unit=rate_area['unit'])
        if not mismatch and rate_area and rate_area['scope']=='EXPLICIT' and _same_area_unit(rate_fact['area_unit'],rate_area['unit']):
            with localcontext() as ctx:
                ctx.prec=60
                numerator=Decimal(rate_fact['quantity'])*Decimal(rate_area['quantity'])
                denominator=Decimal(rate_fact['area_denominator'])
                comparison=numerator/denominator
                mismatch=numerator!=Decimal(evidence['quantity'])*denominator
            evidence['consistency_check']=dict(rate=rate_fact['quantity'],area=rate_area['quantity'],area_denominator=rate_fact['area_denominator'],comparison_total=str(comparison),explicit_total=evidence['quantity'],conflict=mismatch,authority='NONPOSTING_CHECK')
        if mismatch:
            fields['lines'][idx]['quantity']=None
            begin=raw.find(rate_fact['source_quantity'])
            end=raw.find(evidence['source_quantity'],begin+len(rate_fact['source_quantity'])) if begin>=0 else -1
            original=raw[begin:end+len(evidence['source_quantity'])] if end>=0 else rate_fact['source_quantity']
            fields['lines'][idx]['original_quantity']=original[:200]
            gaps.append('Укажите общее количество: доза, площадь и указанный общий расход противоречат друг другу.')
        remove.add(ridx)
    if remove:fields['lines']=[line for idx,line in enumerate(fields['lines']) if idx not in remove]
    if preparation_evidence is not None and any(e['role']=='RATE' for e in quantity_evidence):
        preparation_evidence['quantity_evidence']=quantity_evidence
    return 'INVENTORY',fields,list(dict.fromkeys(gaps))
