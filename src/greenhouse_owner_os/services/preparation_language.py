"""Pure, bounded language utilities for reviewed capture candidates.

No fuzzy names, external translation, default identities or business effects.
Normalization is a comparison view only: callers retain the original source.
"""
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext

AREA_ALIASES = {
    'İmamlı': ('имамлы', 'имаммлы', 'imamlı', 'imamli'),
    'Özvadi': ('озвади', 'özvadi', 'ozvadi'),
    'Yeşilöz': ('ешилоз', 'ешилёз', 'ешилез', 'yeşilöz', 'yesiloz'),
}


def value(row, key, default=None):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def fold(raw):
    """Case/accent comparison without turning Cyrillic й into и."""
    text=unicodedata.normalize('NFC',str(raw)).casefold().replace('ı','i').replace('ё','е')
    result=[]
    for char in text:
        if 'LATIN' in unicodedata.name(char,''):
            result.extend(c for c in unicodedata.normalize('NFD',char) if not unicodedata.combining(c))
        elif char!='\u0307':result.append(char)
    return ''.join(result)


def name_key(raw):
    # Cross-script spellings require an explicit alias/native-name in a current
    # reference. Transliteration alone is not verified identity authority.
    return fold(raw)


def bounded(term):
    return r'(?<!\w)' + re.escape(term) + r'(?!\w)'


def names(row, kind='name'):
    raw = [value(row, 'name')] if kind != 'worker' else [value(row, 'display_name'), value(row, 'native_name')]
    raw += list(value(row, 'aliases', []) or [])
    if kind == 'area':raw += list(AREA_ALIASES.get(value(row, 'name'), ()))
    return [n for n in raw if n]


def _entity_match_groups(raw, refs, kind='name', *, inflect=False):
    t=re.sub(r'\s+',' ',name_key(raw));exact=[];partial=[];qualified_spans=[];unverified_area_spans=[]
    def positive(m):
        if re.search(r'\bне\s+(?:(?:из|в|на|с|со)\s+)?$',t[max(0,m.start()-12):m.start()]):return False
        # A numbered site is a distinct reference, not an alias for its base
        # name. A complete registered alias (including the number) still wins.
        qualifier=re.match(r'(?:\s+(?:№\s*)?|\s*[-–]\s*)\d+(?![\w./:-])',t[m.end():]) if kind=='area' else None
        if qualifier and re.match(r'\s*(?:'+UNIT+'|'+CURRENCY+r'|минут\w*|час\w*|дн\w*|день|'+ '|'.join(MONTHS)+r')(?!\w)',t[m.end()+qualifier.end():]):qualifier=None
        if qualifier:
            unverified_area_spans.append((m.start(),m.end()+qualifier.end()))
            return False
        return True
    for row in refs:
        if value(row,'active',True) is False:continue
        full=[];short=[]
        for alias in names(row,kind):
            key=re.sub(r'\s+',' ',name_key(alias));words=key.split();variants=[key]
            if inflect:
                if len(words)==1:variants += [key+s for s in (('у','а','ом','е') if re.search('[а-я]',key) else ('у','а','ом'))]
                if len(words)>1 and re.fullmatch(r'[а-я]+',words[0]):variants += [words[0]+suffix+' '+' '.join(words[1:]) for suffix in ('у','а','ом','е')]
                if words and all(w.endswith(('ые','ие')) for w in words):
                    variants.append(' '.join(w[:-2]+('ым' if w.endswith('ые') else 'им') for w in words))
            for form in variants:
                for m in re.finditer(bounded(form),t):
                    if positive(m):
                        full.append(m.span())
                        if len(words)>1 or kind=='area':qualified_spans.append(m.span())
            if inflect and len(words)>1 and len(words[0])>=3:
                for m in re.finditer(r'(?<!\w)'+re.escape(words[0])+r'(?:у|а|ом|е)?(?!\w)',t):
                    if positive(m):short.append(m.span())
        if full:exact.append(row)
        elif short:partial.append((row,short))
    # A qualified name suppresses only competing prefix matches at that same
    # mention. It must not erase a different person elsewhere in the sentence.
    if any(not any(a<=start and end<=b for a,b in qualified_spans) for start,end in unverified_area_spans):return [],[]
    unresolved=[row for row,spans in partial if any(not any(a<=start and end<=b for a,b in qualified_spans) for start,end in spans)]
    return exact,unresolved


def explicit_entity_mentions(raw, refs, kind='name', *, inflect=False):
    """Audience evidence only; this list never resolves a sole assignee."""
    return _entity_match_groups(raw,refs,kind,inflect=inflect)[0]


def entity_matches(raw, refs, kind='name', *, inflect=False, require_explicit=False):
    exact,unresolved=_entity_match_groups(raw,refs,kind,inflect=inflect)
    # Candidate prefixes can explain ambiguity but are not registered aliases.
    # An uncovered partial mention also prevents an unrelated exact match from
    # silently becoming the sole identity in a multi-person instruction.
    if require_explicit:return [] if unresolved else exact
    return exact+unresolved


SMALL={'ноль':0,'один':1,'одна':1,'одно':1,'одну':1,'два':2,'две':2,'три':3,'четыре':4,'пять':5,'шесть':6,'семь':7,'восемь':8,'девять':9,'десять':10,'одиннадцать':11,'двенадцать':12,'тринадцать':13,'четырнадцать':14,'пятнадцать':15,'шестнадцать':16,'семнадцать':17,'восемнадцать':18,'девятнадцать':19,'двадцать':20,'тридцать':30,'сорок':40,'пятьдесят':50,'шестьдесят':60,'семьдесят':70,'восемьдесят':80,'девяносто':90,'сто':100,'двести':200,'триста':300,'четыреста':400,'пятьсот':500,'шестьсот':600,'семьсот':700,'восемьсот':800,'девятьсот':900}
SCALES={'тысяча':1000,'тысячи':1000,'тысяч':1000,'тыс':1000,'тыс.':1000,'миллион':1000000,'миллиона':1000000,'миллионов':1000000,'млн':1000000,'млн.':1000000}
WORDS='|'.join(sorted([*SMALL,*SCALES,'полтора','полторы'],key=len,reverse=True))
DIGITS=r'\d+(?:[ \u00a0\u202f]\d{3})*(?:[,.]\d+)?'
SCALE_WORDS='|'.join(re.escape(w) for w in sorted(SCALES,key=len,reverse=True))
NUMBER=rf'(?:{DIGITS}\s+(?:{SCALE_WORDS})(?:\s+(?:{WORDS}|\d+))*|{DIGITS}|(?:{WORDS})(?:\s+(?:{WORDS}))*)'
CURRENCY=r'(?:лир\w*|доллар\w*|евро|грив\w*|грн|try|tl|usd|eur|uah|gbp|rub|chf|aud|cad|pln|kzt|nok|sek|gel|azn|byn|sar|qar|aed|cny|jpy|inr|brl|a\$|₺|₴|\$|€)'
UNIT=r'(?:килограмм(?:ов|а)?|литр(?:ов|а|ы)?|меш(?:ков|ка|ки|ок)|бидон(?:ов|а)?|упаков(?:ок|ки|ка)|штук(?:и|а)?|piece|bag|kg|кг|шт|л|l)'


def number(raw, places=12):
    # Capture text is bounded to 10,000 characters; never derive an unbounded
    # arithmetic context from malformed provider output or non-string values.
    if not isinstance(raw,str) or len(raw)>10000:return None
    t=fold(raw).strip()
    if re.fullmatch(r'\d+(?:[ \u00a0\u202f]\d{3})*(?:[,.]\d+)?',t):
        result=re.sub(r'[ \u00a0\u202f]','',t).replace(',','.')
    elif t in ('полтора','полторы'):result='1.5'
    else:
        with localcontext() as context:
            context.prec=max(64,len(t)+12)
            total=group=Decimal(0);previous=1000;scale_before=1000000000;has_group=False
            for word in t.split():
                if word in SCALES:
                    scale=SCALES[word]
                    if scale>=scale_before:return None
                    total+=(group if has_group else 1)*scale;group=Decimal(0);previous=1000;scale_before=scale;has_group=False
                elif word in SMALL:
                    v=SMALL[word]
                    rank=100 if v>=100 else 10 if v>=10 else 1
                    if rank>=previous:return None
                    group+=v;previous=rank;has_group=True
                elif re.fullmatch(r'\d+(?:[,.]\d+)?',word) and not has_group:
                    group=Decimal(word.replace(',','.'));previous=0;has_group=True
                else:return None
            result=format(total+group,'f')
            if '.' in result:result=result.rstrip('0').rstrip('.')
    try:
        if not re.fullmatch((r'\d{1,18}(?:\.\d{1,'+str(places)+r'})?' if places else r'\d{1,18}'),result) or Decimal(result)<=0:return None
    except InvalidOperation:return None
    return result


def source_currency(raw, default='TRY'):
    t=fold(raw);found=set()
    if re.search(r'[£¥₽₹]|(?i:(?<![^\W\d])(?:AU|CA|NZ|HK|SG|A|C)\$)',str(raw)):return None
    explicit_codes=re.findall(r'(?<!\w)\d+(?:[.,]\d+)?\s*([A-Z]{3})(?!\w)',str(raw),re.I)
    if any(code.upper() not in ('TRY','USD','EUR','UAH') for code in explicit_codes):return None
    patterns={'TRY':r'try|tl|лир\w*|₺','USD':r'usd|доллар\w*|\$','EUR':r'eur|евро|€','UAH':r'uah|грн|грив\w*|₴'}
    for key,p in patterns.items():
        if re.search(r'(?<![^\W\d])(?:'+p+r')(?![^\W\d])',t):found.add(key)
    if re.search(r'(?<![^\W\d])(?:gbp|rub|chf|aed|cny|jpy|aud|cad|inr|руб\w*|фунт\w*)(?![^\W\d])',t):return None
    return next(iter(found)) if len(found)==1 else None if found else default


def nonfinancial_modal(raw):
    m=re.search(r'\bдолж(?:ен|на|ны)\s+(?:(?:сегодня|завтра|послезавтра|будет|бы|будут)\s+)?(\w+(?:ть|ти|чь))\b',fold(raw))
    return bool(m and m.group(1) not in ('заплатить','оплатить','выплатить','отдать','перевести'))


def meta_or_report(raw):
    """Descriptions of words, prior reports and unaccepted facts are not acts."""
    return bool(re.search(r'\b(?:в инструкции|в отчете|это отчет|это образец|это пример|не реальный|есть слово|на изображении слово|я\s+[^.!?]{0,40}\bобъяснял|надпись|фраза|описание устройства|не поручение|старой накладной|новое поручение не|кто кому|не подтверждал|не должник|моя доля не подтверждена)\b',fold(raw)))


def money_amount(raw, *, implicit=False):
    # Calendar hyphens must not look like a monetary range.
    t=fold(raw)
    t=re.sub(r'\b(?:с|от)\s+\d{1,2}\s+(?:по|до)\s+\d{1,2}\s+(?:'+ '|'.join(MONTHS)+r')(?:\s+20\d{2})?', ' ',t)
    t=re.sub(DATE_PATTERN,' ',t)
    if re.search(r'\b(?:сумм\w*|сколько)\s+(?:сейчас\s+|пока\s+|еще\s+)?(?:не\s+(?:знаю|записал|указал|помню|назвал)|неизвест\w*)\b',t):return None
    t=re.sub(r'\b(?:по\s+счету|номер(?:\s+\w+){0,2})\s*№?\s*\d+',' ',t)
    if re.search(r'\b(?:около|примерно|приблизительно|от\s+\d+\s+до)\b|\d+\s*[-–]\s*\d+',t):return None
    matches=list(re.finditer(r'(?<![\w.+-])('+NUMBER+r')\s*('+CURRENCY+r')(?!\w)',t))
    if matches:
        if len(matches)!=1:return None
        fraction=re.search(r'\b(?:половин\w*|четверть|треть)\b',t)
        if fraction and fraction.start()<matches[0].start() and not re.search(r'\b(?:это|то есть|именно)\b',t[fraction.end():matches[0].start()]):return None
        before=t[:matches[0].start()]
        if re.search(r'\bскидк\w*(?:\s+(?:составляет|равна|это))?\s*$',before):return None
        if re.search(r'(?<!\w)(?:'+NUMBER+r')\s*$',before):return None
        return number(matches[0].group(1),2)
    if not implicit or re.search(r'\bполовин\w*\b',t):return None
    # Calendar, units, invoice references and durations are not money.
    t=re.sub(r'\b(?:с|от)\s+\d{1,2}\s+(?:по|до)\s+\d{1,2}\s+(?:'+ '|'.join(MONTHS)+r')(?:\s+20\d{2})?', ' ',t)
    t=re.sub(DATE_PATTERN,' ',t)
    t=re.sub(r'(?<![\w.+-])'+NUMBER+r'\s*(?:'+UNIT+r'|дн\w*|ден\w*|день|минут\w*|час\w*|месяц\w*|недел\w*|человек|рабоч\w*|насос\w*|числа|число|вет\w*|дерев\w*|мм|см|м)(?!\w)',' ',t)
    t=re.sub(r'\b\d{1,2}:\d{2}\b|(?:№|номер)\s*\d+',' ',t)
    vals=[number(m.group(),2) for m in re.finditer(r'(?<![\w.+-])'+NUMBER+r'(?!\w)',t)]
    return vals[0] if len(vals)==1 else None


MONTHS={m:i+1 for i,m in enumerate(('января','февраля','марта','апреля','мая','июня','июля','августа','сентября','октября','ноября','декабря'))}
DATE_PATTERN=r'(?<!\d)(?:20\d{2}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]20\d{2}|\d{1,2}\s+(?:'+ '|'.join(MONTHS)+r')(?:\s+20\d{2})?)(?!\d)'
DUE_PATTERN=r'(?:\b(?:заплачу|оплачу|отдам|переведу|заплатят|оплатят|заплатить|оплатить|срок\s+оплаты|к\s+оплате\s+до|оплатить\s+до|заплатить\s+до|до)\s+)([^,;\n]+)'

# Bounded grammatical/spelling variants of a period, not a calendar date.
# This comparison-only vocabulary never rewrites source bytes or identities.
NEXT_WEEK=r'(?:(?:на|в)\s+)?(?:следующей|следующую|следующая|следущей|следущую|следущая)\s+(?:неделе|неделю|неделя)'


def relative_time_context(raw):
    """Keep an explicit, non-negated relative period without choosing a day."""
    t=re.sub(r'\s+',' ',fold(raw))
    for m in re.finditer(r'\b'+NEXT_WEEK+r'\b',t):
        if re.search(r'\bне\s+(?:(?:на|в)\s+)?$',t[:m.start()]):continue
        if re.match(r'\s*[,;]\s*нет\b',t[m.end():]):continue
        return 'следующая неделя'
    return None


def date_value(raw,today,*,role='event'):
    """Return (unambiguous date, explicit/ambiguous temporal evidence)."""
    t=re.sub(r'\s+',' ',fold(raw));found=[];mentioned=False
    t=re.sub(r'\b(?:дата партии|дата изготовления|срок годности|годен до)\s*[:=]?\s*'+DATE_PATTERN,' ',t)
    due=list(re.finditer(DUE_PATTERN,t))
    if role=='due':t=' '.join(m.group(1) for m in due)
    elif role=='event':
        for m in reversed(due):t=t[:m.start()]+' '+t[m.end():]
    for word,offset in [('сегодняшний',0),('сегодняшнего',0),('сегодняшнюю',0),('сегодня',0),('вчера',-1),('позавчера',-2),('завтра',1),('послезавтра',2),('после завтра',2)]:
        if re.search(bounded(word),t):found.append(today+timedelta(days=offset));mentioned=True
    for m in re.finditer(DATE_PATTERN,t):
        mentioned=True;v=m.group()
        try:
            if re.fullmatch(r'20\d{2}-\d{2}-\d{2}',v):found.append(date.fromisoformat(v))
            elif re.fullmatch(r'\d{1,2}[./]\d{1,2}[./]20\d{2}',v):
                d,mo,y=re.split(r'[./]',v);found.append(date(int(y),int(mo),int(d)))
            else:
                parts=v.split()
                if len(parts)!=3:return None,True
                found.append(date(int(parts[2]),MONTHS[parts[1]],int(parts[0])))
        except ValueError:return None,True
    t=re.sub(r'\bчерез\s+(неделю|недели|недель|день|дня|дней)\s+('+NUMBER+r')\b',lambda m:'через '+m.group(2)+' '+m.group(1),t)
    for m in re.finditer(r'\bчерез\s+('+NUMBER+r')\s+(день|дня|дней|неделю|недели|недель)\b',t):
        mentioned=True;n=number(m.group(1),0)
        if n and int(n)<10000:found.append(today+timedelta(days=int(n)*(7 if m.group(2).startswith('недел') else 1)))
    relative=relative_time_context(t) if role=='planned' else None
    if relative or re.search(r'\b(?:месяц\w*\s+назад|на\s+следующей\s+неделе|в\s+пятницу|к\s+концу\s+месяца)\b',t):mentioned=True
    if relative and re.search(r'\b(?:или|либо)\b',t):return None,True
    # 'после завтра' must not introduce the overlapping standalone tomorrow.
    if 'после завтра' in t and re.search(r'(?<!после )\bзавтра\b',t) is None:
        found=[d for d in found if d!=today+timedelta(days=1)]
    if not mentioned and role=='planned' and re.search(bounded('сейчас'),t):found.append(today);mentioned=True
    unique=set(found)
    return (next(iter(unique)).isoformat() if len(unique)==1 else None),mentioned


def comparison_source(raw):
    """A non-authoritative view; the source itself is never rewritten."""
    text=re.sub(r'[^\S\n]+',' ',raw).strip()
    return re.sub(r'(?i)^(?:(?:ну|вот|это самое|смотри|значит|так|пожалуйста|в общем|короче|ага)\s+)+','',text)


def corrected(raw, *, counterparties=(), areas=(), workers=(), locations=(), accounts=(), task_dates=False, **unused):
    """Resolve explicit same-source replacement of a typed scalar only.

    Never remove a negated action ('не платил', 'не поливать'). Ambiguous
    free-form corrections are retained for review instead of interpreted.
    """
    t=comparison_source(raw)
    units=rf'(?:\s*(?:{UNIT}|{CURRENCY}))?'
    # Quantity correction inherits an explicitly spoken unit, never pack size.
    pattern=rf'(?i)\bне\s+({NUMBER})({units})\s*[,;]\s*(?:а\s+)?({NUMBER})({units})(?!\w)'
    def numeric(m):return m.group(3)+(m.group(4) or m.group(2))
    t=re.sub(pattern,numeric,t)
    pattern=rf'(?i)(?<![\w.+-])({NUMBER})({units})\s*[,;]\s*нет\s*[,;]?\s*({NUMBER})({units})(?!\w)'
    t=re.sub(pattern,numeric,t)
    atoms=[CURRENCY,r'сегодня',r'завтра',r'послезавтра',r'вчера',r'позавчера',r'после\s+завтра',r'наличными',r'(?:с|из)\s+банка']
    # Expanded relative/explicit date corrections belong to planned tasks.
    # They must not turn a financial deadline correction into an event date.
    if task_dates:atoms.extend([NEXT_WEEK,DATE_PATTERN])
    for refs,kind in [(counterparties,'name'),(areas,'area'),(workers,'worker'),(locations,'name'),(accounts,'name')]:
        for ref in refs:
            for n in names(ref,kind):atoms.append(re.escape(n)+r'(?:у|а|ом)?')
    atom=r'(?:'+ '|'.join(sorted(set(atoms),key=len,reverse=True))+r')'
    p=rf'(?i)\bне\s+((?:(?:из|в|на|со|с)\s+)?{atom})\s*[,;]\s*(?:а\s+)?((?:(?:из|в|на|со|с)\s+)?{atom})(?!\w)'
    t=re.sub(p,lambda m:m.group(2),t)
    p=rf'(?i)(?<!\w)({atom})\s*[,;]\s*нет\s*[,;]?\s*({atom})(?!\w)'
    t=re.sub(p,lambda m:m.group(2),t)
    return t
