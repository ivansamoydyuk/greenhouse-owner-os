"""Conservative typed candidates, with original lines and provenance retained."""
import re
import unicodedata
from datetime import timedelta
from decimal import Decimal
from .capture import extract


def turkish_number(value):
    value = value.strip().replace(' ', '')
    if ',' in value:
        value = value.replace('.', '').replace(',', '.')
    if not re.fullmatch(r'\d+(?:\.\d+)?', value):
        return None
    return format(Decimal(value), 'f')


def prepare(text, today, references, recognition=None):
    """Return independent typed slots; no reference creation or domain writes."""
    spans = [s for p in (recognition or {}).get('pages', []) for s in p['spans']]
    provenance = {f'line:{s["page"]}:{s["line"]}': dict(category='SOURCE_FACT', **s) for s in spans}
    t = text.casefold()
    # Receipt lines are parsed from actual OCR output, never from file names.
    depot_document = any(word in t for word in ('alacak', 'birinci', 'ikinci', 'i̇kinci'))
    if depot_document:
        lines, qualities, warnings = [], [], []
        labels = {'grand': 'HIGHEST', 'birinci': 'FIRST', 'ikinci': 'SECOND', 'i̇kinci': 'SECOND'}
        for ordinal, raw in enumerate(text.splitlines(), 1):
            match = re.match(r'^\s*(Grand|Birinci|İkinci|Ikinci)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s*$', raw, re.I)
            if match:
                label, kg, price, amount = match.groups()
                values = [turkish_number(v) for v in (kg, price, amount)]
                if any(v is None for v in values):
                    warnings.append('unreadable_line'); continue
                lines.append(dict(original_quality_name=label, net_weight_kg=values[0], unit_price=values[1],
                                  stated_amount=values[2], source_line=f'{ordinal}: {raw}'))
                # Görgün identity is mandatory for these scoped candidates.
                qualities.append(labels.get(label.casefold(), 'UNKNOWN') if 'görgün' in t or 'gorgun' in t else 'UNKNOWN')
                if (Decimal(values[0]) * Decimal(values[1])).quantize(Decimal('.01')) != Decimal(values[2]):
                    warnings.append('amount_discrepancy')
            elif any(label in raw.casefold() for label in labels):
                warnings.append('partial_or_unreadable_quality_line')
        candidates = [c for c in references['counterparties'] if c.name.casefold() in t]
        day = re.search(r'(?<!\d)(\d{2})[./](\d{2})[./](20\d{2})(?!\d)', text)
        event_date = None
        if day:
            from datetime import date
            try:event_date = date(int(day[3]), int(day[2]), int(day[1])).isoformat()
            except ValueError:warnings.append('invalid_date')
        def value(label):
            m = re.search(label+r'\s*[:=]?\s*([\d.,]+)', text, re.I)
            return turkish_number(m[1]) if m else None
        payload = dict(depot_id=str(candidates[0].id) if len(candidates)==1 else None, event_date=event_date,
                       currency='TRY', lines=lines, qualities=qualities, stated_total=value('Toplam'), alacak_value=value('Alacak'))
        if payload['stated_total'] is not None and sum((Decimal(l['stated_amount']) for l in lines), Decimal(0)) != Decimal(payload['stated_total']):
            warnings.append('stated_total_discrepancy')
        provenance['currency'] = dict(category='INFERENCE', basis='daily TRY context')
        return [('depot:0', 'DEPOT_SETTLEMENT', payload, provenance, warnings)]
    route, fields, _ = extract(text, today=today, **references)
    provenance.update({k:dict(category='SOURCE_FACT', basis='original text') for k in fields})
    if route == 'FINANCE' and fields.get('kind') in ('payment', 'obligation'):
        if not fields.get('amount'):
            nums = re.findall(r'(?<![\w.,+-])\d+(?:[ \u00a0]\d{3})*(?:[.,]\d{1,2})?(?!\w)', text)
            if len(nums)==1:
                fields['amount'] = nums[0].replace(' ', '').replace('\u00a0', '').replace(',', '.')
                provenance['amount'] = dict(category='SOURCE_FACT', basis='single amount in original')
        fields.setdefault('currency', 'TRY')
        fields.setdefault('event_date', today.isoformat())
        provenance['currency'] = dict(category='SOURCE_FACT' if any(x in t for x in ('try', 'лир', '₺')) else 'INFERENCE', basis='TRY daily context')
        provenance['event_date'] = dict(category='SOURCE_FACT' if 'сегодня' in t else 'INFERENCE', basis='capture date in Europe/Istanbul')
        payload = {k:v for k,v in fields.items() if k in ('amount','currency','event_date','counterparty_id','direction','due_date','attributions','money_account_id')}
        payload['description'] = text[:2000]
        return [('finance:0', 'PAYMENT' if fields['kind']=='payment' else 'PAYABLE', payload, provenance, [])]
    if route == 'INVENTORY' and fields.get('kind') in ('receipt','application'):
        payload = dict(event_date=fields.get('event_date') or today.isoformat(), lines=fields.get('lines',[]), notes=text[:2000])
        provenance['event_date'] = dict(category='INFERENCE', basis='capture date')
        return [('stock:0', 'INVENTORY_RECEIPT' if fields['kind']=='receipt' else 'INVENTORY_APPLICATION', payload, provenance, [])]
    if route == 'TASKS':
        workers = fields.get('suggested_worker_ids', [])
        payload = dict(title=fields['title'], original_instruction=text[:2000], greenhouse_id=fields.get('greenhouse_id'),
                       planned_date=fields.get('planned_date'), worker_ids=workers)
        if len(workers)==1:
            worker = next(w for w in references['workers'] if w['id']==workers[0])
            payload['language'] = worker['language']
        return [('task:0', 'WORKER_TASK' if workers else 'PERSONAL_TASK', payload, provenance, [])]
    if any(word in t for word in ('задач', 'позвонить', 'напомни', 'окна', 'полить', 'починит')):
        day = today + timedelta(days=1) if 'завтра' in t else today if 'сегодня' in t else None
        return [('task:0', 'PERSONAL_TASK', dict(title=text[:200],original_instruction=text[:2000],planned_date=str(day) if day else None), provenance, [])]
    return [('unresolved:0', 'UNRESOLVED', dict(text=text), provenance, ['route_unknown'])]
