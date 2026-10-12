"""Read-only greenhouse composition of the existing source authorities.

Finance owns recognition and virtual GENERAL allocation. Harvest Journey owns
exact/estimated attribution and stale-estimate exclusion. This module only
selects and sums their current sources; it never writes or posts anything.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode
from uuid import UUID
from zoneinfo import ZoneInfo
from sqlalchemy import text
from greenhouse_owner_os.domain.errors import ValidationError
from greenhouse_owner_os.domain.greenhouse_daily import GreenhouseDaily
from .capture import BorrowedUow
from .finance_analytics import FinanceAnalyticsService, amount, kg, root_id
from .fertilizer import FertilizerService
from .inventory import rows, precise
from .workers import WorkersService

ZERO = Decimal(0)
QUALITIES = ('HIGHEST', 'FIRST', 'SECOND', 'UNKNOWN')


def decimal(value):
    return Decimal(str(value))


def ratio(numerator, denominator):
    return format((decimal(numerator) / decimal(denominator)).quantize(Decimal('.0001')), 'f') if denominator is not None and decimal(denominator) > 0 else None


def season_start(day):
    return date(day.year if day.month >= 10 else day.year - 1, 10, 1)


def period_months(start, end):
    result = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append(f'{year:04}-{month:02}')
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def comparison_window(start, end, today):
    """Align offsets from Oct 1, with equal elapsed days even in leap seasons."""
    season = season_start(start)
    boundary = date(season.year + 1, 10, 1)
    if end >= boundary:
        return None, 'PERIOD_SPANS_SEASONS'
    if season.year <= 1900:
        return None, 'PRIOR_SEASON_UNAVAILABLE'
    previous = date(season.year - 1, 10, 1)
    # A leap-season's additional day cannot be compared to a nonexistent day.
    last_comparable = season + (season - previous) - timedelta(days=1)
    elapsed_end = min(end, today, last_comparable)
    if start > elapsed_end:
        return None, 'PERIOD_HAS_NOT_ELAPSED'
    return (start, elapsed_end, previous + (start - season), previous + (elapsed_end - season)), None


def verified_area(gid, areas, references, start, end):
    """A policy allocation weight is not evidence of a dated physical area."""
    by_area = defaultdict(list)
    for reference in references:
        by_area[reference['work_area_id']].append(reference)

    def interval(aid):
        candidates = sorted(by_area[aid], key=lambda x: (x['effective_from'], x['id']))
        before = [x for x in candidates if x['effective_from'] <= str(start)]
        current = before[-1] if before else None
        changes = [x for x in candidates if str(start) < x['effective_from'] <= str(end)]
        selected = ([current] if current else []) + changes
        if not current or current['area_da'] is None or not current.get('verification'):
            return None, selected, 'UNKNOWN'
        if decimal(current['area_da']) <= 0:
            return None, selected, 'UNKNOWN'
        if any(x['area_da'] is None or decimal(x['area_da']) != decimal(current['area_da']) for x in changes):
            return None, selected, 'CHANGED_IN_PERIOD'
        return decimal(current['area_da']), selected, 'VERIFIED_FULL_PERIOD'

    direct, used, status = interval(gid)
    if direct is not None:
        parts = []
    else:
        # Root references used solely for a worker do not supply a denominator.
        parts = [x['id'] for x in areas.values() if x.get('parent_id') == gid]
        if status == 'CHANGED_IN_PERIOD' or not parts:
            return dict(area_da=None, status=status, reference_ids=[x['id'] for x in used], missing_work_area_ids=[gid], validity_from=None, validity_to=None)
        used = []; missing = []; total = ZERO; statuses = []
        for aid in parts:
            value, evidence, state = interval(aid)
            used.extend(evidence); statuses.append(state)
            if value is None: missing.append(aid)
            else: total += value
        if missing:
            return dict(area_da=None, status='CHANGED_IN_PERIOD' if 'CHANGED_IN_PERIOD' in statuses else 'UNKNOWN', reference_ids=sorted({x['id'] for x in used}), missing_work_area_ids=sorted(missing), validity_from=None, validity_to=None)
        direct = total
    return dict(area_da=format(direct, 'f'), status='VERIFIED_FULL_PERIOD', reference_ids=sorted({x['id'] for x in used}), missing_work_area_ids=[], validity_from=start, validity_to=end)


def finance_scope(finance, gid, area):
    authoritative = next(x for x in finance['areas'] if x['greenhouse_id'] == gid)
    result = {k: authoritative[k] for k in ('revenue', 'operating_cost', 'operating_result', 'exact_cost', 'virtual_general_cost', 'estimated_revenue', 'cost_per_kg')}
    events = []
    for event in finance['economic_events']:
        allocations = [x for x in event['allocations'] if x.get('greenhouse_id') == gid]
        if allocations:
            events.append(dict(event, selected_amount=amount(sum((decimal(x['amount']) for x in allocations), ZERO))))
    result['economic_events'] = events
    result['source_count'] = len({(x['source']['kind'], x['source']['id']) for x in events})
    result['state'] = 'CONFIRMED_OPERATIONS' if events else 'NO_CONFIRMED_OPERATIONS'
    result['per_da'] = {key: ratio(result[key], area['area_da']) for key in ('revenue', 'operating_cost', 'operating_result')}
    return result


def field_metric(reports):
    known = [r for r in reports if r['cut_count'] is not None]
    missing = len(reports) - len(known)
    return dict(value=str(sum(r['cut_count'] for r in known)) if known else None,
                exact_value=str(sum(r['cut_count'] for r in known)) if known else None,
                source_ids=sorted({r['evidence_id'] for r in reports}), source_count=len(reports), missing_count=missing,
                classification='NO_DATA' if not reports else 'PARTIAL' if missing else 'EXACT')


def selected_line(line, estimate_lines, gid, areas, allocation='all'):
    """Use saved attribution facts, never reconstruct an apportionment."""
    exact_kg = exact_amount = estimated_kg = estimated_amount = ZERO
    assigned_kg = assigned_amount = ZERO
    for item in line['allocations']:
        assigned_kg += decimal(item['net_weight_kg']); assigned_amount += decimal(item['amount'])
        if root_id(item['work_area_id'], areas) == gid:
            exact_kg += decimal(item['net_weight_kg']); exact_amount += decimal(item['amount'])
    for item in estimate_lines:
        if item['line_id'] != line['id'] or item['work_area_id'] is None: continue
        assigned_kg += decimal(item['net_weight_kg']); assigned_amount += decimal(item['amount'])
        if root_id(item['work_area_id'], areas) == gid:
            estimated_kg += decimal(item['net_weight_kg']); estimated_amount += decimal(item['amount'])
    chosen_kg = (exact_kg if allocation != 'estimated' else ZERO) + (estimated_kg if allocation != 'exact' else ZERO)
    chosen_amount = (exact_amount if allocation != 'estimated' else ZERO) + (estimated_amount if allocation != 'exact' else ZERO)
    return dict(net_weight_kg=kg(chosen_kg), amount=amount(chosen_amount), exact_kg=kg(exact_kg), exact_amount=amount(exact_amount),
                estimated_kg=kg(estimated_kg), estimated_amount=amount(estimated_amount),
                unallocated_kg=kg(decimal(line['net_weight_kg']) - assigned_kg), unallocated_amount=amount(decimal(line['amount']) - assigned_amount))


def quality_summary(events, currency):
    """Every selected kg, including unknown quality, remains in the denominator."""
    buckets = {key: dict(kg=ZERO, amount=ZERO, priced_kg=ZERO, ids=set()) for key in QUALITIES}
    for event in events:
        for line in event['lines']:
            bucket = buckets[line['quality']]
            value = decimal(line['net_weight_kg'])
            bucket['kg'] += value
            if value or decimal(line['amount']): bucket['ids'].add(line['id'])
            if event['currency'] == currency:
                bucket['amount'] += decimal(line['amount']); bucket['priced_kg'] += value
    total = sum((b['kg'] for b in buckets.values()), ZERO)
    return [dict(quality=key, net_weight_kg=kg(b['kg']), share_percent=ratio(b['kg'] * 100, total),
                 priced_kg=kg(b['priced_kg']), amount=amount(b['amount']), weighted_price_per_kg=ratio(b['amount'], b['priced_kg']), line_ids=sorted(b['ids']))
            for key, b in buckets.items()]


def day_worker_money(summary, finance, start, end):
    result = dict(summary)
    gid = summary.get('greenhouse_id')
    ids = set(summary.get('obligation_ids', []))
    current_ids = set(summary.get('current_obligation_ids', ids)) | ids
    expense = ZERO
    for event in finance['economic_events']:
        if event['classification'] == 'OPERATING_COST' and event['source']['kind'] == 'obligation' and event['source']['id'] in ids:
            expense += sum((decimal(x['amount']) for x in event['allocations'] if x.get('greenhouse_id') == gid), ZERO)
    documents = [x for x in finance['debts']['documents'] if x['obligation_id'] in current_ids]
    payments = []; settlements = []
    uncertain_debt = []; uncertain_payments = []; uncertain_settlements = []; known_debt = ZERO
    for document in documents:
        if document['status'] != 'posted': continue
        assigned = sum((decimal(x['amount']) for x in document['attributions'] if x.get('greenhouse_id') == gid), ZERO)
        original = decimal(document['original_amount'])
        remaining = decimal(document['remaining_amount'])
        fully_scoped = assigned == original
        history = [x for x in document['history'] if x['status'] == 'posted' and str(start) <= x['event_date'] <= str(end)]
        if not fully_scoped:
            # Before settlement the exact accrued portion is fully unpaid; after
            # complete settlement each portion is zero. Partial settlement alone
            # does not identify which greenhouse portion remains unpaid.
            if document['direction'] == 'payable':
                if remaining == original: known_debt += assigned
                elif remaining != ZERO: uncertain_debt.append(document['obligation_id'])
            if any(x['kind'] == 'payment' for x in history):
                uncertain_payments.append(document['obligation_id'])
            if any(x['kind'] != 'payment' for x in history):
                uncertain_settlements.append(document['obligation_id'])
            continue
        if document['direction'] == 'payable': known_debt += remaining
        for item in history:
            (payments if item['kind'] == 'payment' else settlements).append(dict(item, obligation_id=document['obligation_id']))
    paid = amount(sum((decimal(x['amount']) for x in payments), ZERO))
    result.update(currency=finance['currency'], accrued_amount=amount(expense),
                  paid_amount=None if uncertain_payments else paid, known_paid_amount=paid,
                  settled_without_payment_amount=None if uncertain_settlements else amount(sum((decimal(x['amount']) for x in settlements), ZERO)),
                  current_debt=None if uncertain_debt else amount(known_debt), known_current_debt=amount(known_debt),
                  payment_allocation_unknown=bool(uncertain_payments), debt_allocation_unknown=bool(uncertain_debt),
                  settlement_allocation_unknown=bool(uncertain_settlements),
                  unresolved_obligation_ids=sorted(set(uncertain_debt + uncertain_payments + uncertain_settlements)),
                  payment_events=payments, settlement_events=settlements, documents=documents,
                  debt_scope='CURRENT_PROVEN_GREENHOUSE_DAY_WORKER_OBLIGATIONS_ALL_DATES',
                  cost_basis='EXISTING_FINANCE_COST_ALREADY_INCLUDED')
    return result


def upcoming_application(item, today):
    return item['event_date'] >= str(today) and item['status'] in ('DRAFT', 'APPROVED', 'PARTIALLY_APPLIED', 'REVIEWED_NOT_APPLIED')


class GreenhouseDailyService:
    def __init__(self, factory):
        self.factory = factory

    def _harvest_data(self, u, e, start, end, areas, finance_service):
        receipts = finance_service._harvest_sources(u, e, start, end, areas)
        for receipt in receipts:
            sid = receipt['receipt']['id']
            receipt['lines'] = rows(u, 'SELECT * FROM harvest.settlement_lines WHERE settlement_id=:s ORDER BY source_line,id', s=sid)
            reviews = rows(u, '''SELECT q.*,r.version proposal_version,r.payload,r.basis,r.provenance
                FROM harvest.receipt_quality_reviews q JOIN core.confirmation_effects c ON c.proposal_id=q.proposal_id AND c.settlement_id=q.settlement_id
                JOIN core.proposal_revisions r ON r.proposal_id=c.proposal_id AND r.version=c.version AND r.entity_id=c.entity_id
                WHERE q.settlement_id=:s ORDER BY q.line_no''', s=sid)
            for line in receipt['lines']:
                line['allocations'] = rows(u, 'SELECT * FROM harvest.line_allocations WHERE line_id=:l ORDER BY work_area_id', l=line['id'])
                matched = []
                for review in reviews:
                    original = review['payload'].get('lines', [])
                    index = review['line_no']
                    if index >= len(original): continue
                    original = original[index]
                    # Receipt order is not a persistent line identity. Match the
                    # original facts, preserving ambiguity rather than guessing.
                    if original.get('original_quality_name') == line['original_quality_name'] and original.get('source_line') == line['source_line'] and decimal(original['net_weight_kg']) == decimal(line['net_weight_kg']) and review['quality'] == line['normalized_quality']:
                        matched.append(review)
                line['quality_reviews'] = [{k: r[k] for k in ('line_no', 'quality', 'proposal_id', 'proposal_version', 'reviewed_by')} for r in matched]
                mappings = []
                for review in matched:
                    mappings.extend(dict(m, depot_id=receipt['receipt']['depot_id']) for m in review['basis'].get('quality_mappings', []) if m['original_label'] == line['original_quality_name'].casefold())
                line['mapping_versions'] = mappings
                line['quality'] = line['normalized_quality'] if matched and line['normalized_quality'] in QUALITIES[:3] else 'UNKNOWN'
        reports = rows(u, 'SELECT * FROM harvest.worker_reports WHERE entity_id=:e AND event_date BETWEEN :f AND :t ORDER BY event_date,id', e=e, f=start, t=end)
        return receipts, reports

    def _harvest_scope(self, receipts, reports, gid, areas, currency, allocation, season, finance_service, period_start=None, period_end=None):
        denominator = finance_service._denominator(receipts, gid)
        included = {x['settlement_id'] for x in denominator['sources']}
        events = []
        for item in receipts:
            receipt = item['receipt']
            if receipt['id'] not in included: continue
            if allocation == 'exact' and gid not in item['exact']: continue
            if allocation == 'estimated' and gid not in item['estimated']: continue
            estimate = item['estimate']
            lines = []
            for original in item['lines']:
                scoped = selected_line(original, estimate['lines'] if estimate else [], gid, areas, allocation)
                lines.append(dict(id=original['id'], original_quality_name=original['original_quality_name'], stored_quality=original['normalized_quality'], quality=original['quality'],
                    quality_reviews=original['quality_reviews'], mapping_versions=original['mapping_versions'],
                    classification_provenance='OWNER_RECEIPT_REVIEW' if original['quality_reviews'] else 'LEGACY_UNVERIFIED',
                    mapping_basis='CONFIRMATION_REVIEW_CONTEXT_NOT_RETROACTIVE_CLASSIFICATION', source_line=original['source_line'],
                    source_net_weight_kg=original['net_weight_kg'], source_amount=original['amount'], **scoped))
            events.append(dict(id=receipt['id'], settlement_id=receipt['id'], event_date=receipt['event_date'], depot_id=receipt['depot_id'], currency=receipt['currency'],
                evidence_id=receipt['evidence_id'], obligation_id=receipt['obligation_id'], lines=lines,
                estimate_revision_id=estimate['id'] if estimate else None, estimate_version=estimate['version'] if estimate else None,
                stale_estimate_revision_id=item['stale']['id'] if item['stale'] else None, unknown_basis_count=str(item['unknown_basis']),
                exact_kg=kg(item['exact'].get(gid, ZERO)), estimated_kg=kg(item['estimated'].get(gid, ZERO)), unallocated_kg=kg(item['unallocated']),
                selected_attribution_known=(allocation != 'estimated' and gid in item['exact']) or (allocation != 'exact' and gid in item['estimated']),
                selected_kg=kg(sum((decimal(line['net_weight_kg']) for line in lines), ZERO)),
                selected_amount=amount(sum((decimal(line['amount']) for line in lines), ZERO))))
        quality = quality_summary(events, currency)
        priced_kg = sum((decimal(x['priced_kg']) for x in quality), ZERO)
        priced_amount = sum((decimal(x['amount']) for x in quality), ZERO)
        selected_reports = [x for x in reports if x['greenhouse_id'] == gid]
        branches = field_metric(selected_reports)
        value = denominator['confirmed_kg'] if allocation == 'all' else denominator['exact_kg' if allocation == 'exact' else 'estimated_kg']
        if not events: value = None
        months = []
        for key in period_months(period_start or season, period_end or date(season.year + 1, 9, 30)):
            me = [x for x in events if x['event_date'].startswith(key)]
            mf = [x for x in selected_reports if x['event_date'].startswith(key)]
            known = [x for x in me if x['selected_attribution_known']]
            priced = [x for x in known if x['currency'] == currency]
            missing = sum(not x['selected_attribution_known'] or decimal(x['unallocated_kg']) > 0 or decimal(x['unknown_basis_count']) > 0 for x in me)
            months.append(dict(month=key, branches=field_metric(mf), depot_kg=kg(sum((decimal(x['selected_kg']) for x in known), ZERO)) if known else None,
                revenue=amount(sum((decimal(x['selected_amount']) for x in priced), ZERO)) if priced else None, currency=currency,
                source_count=len(me), known_source_count=len(known), missing_source_count=missing, state='NO_DATA' if not me else 'UNKNOWN' if not known else 'PARTIAL' if missing else 'KNOWN', settlement_ids=[x['id'] for x in me], evidence_ids=[x['evidence_id'] for x in me],
                exact_kg=kg(sum((decimal(x['exact_kg']) for x in me), ZERO)), estimated_kg=kg(sum((decimal(x['estimated_kg']) for x in me), ZERO)),
                unallocated_kg=kg(sum((decimal(x['unallocated_kg']) for x in me), ZERO))))
        warnings = []
        if denominator['basis_incomplete']: warnings.append('LINKED_SOURCE_ORIGIN_INCOMPLETE')
        if denominator['stale_estimate_count']: warnings.append('STALE_ESTIMATE_EXCLUDED')
        if any(x['quality'] == 'UNKNOWN' and decimal(x['net_weight_kg']) for x in quality): warnings.append('QUALITY_UNVERIFIED_WEIGHT_RETAINED')
        if allocation != 'all': warnings.append('ALLOCATION_FILTER_APPLIES_TO_HARVEST_ONLY')
        return dict(branches=branches, depot_kg=dict(value=value, exact_value=denominator['exact_kg'], estimated_value=denominator['estimated_kg'], unallocated_value=denominator['unallocated_kg'],
                    source_ids=sorted({x['evidence_id'] for x in events}), source_count=len(events), missing_count=sum(decimal(x['unallocated_kg']) > 0 or decimal(x['unknown_basis_count']) > 0 for x in events), classification='UNAVAILABLE' if not events else 'PARTIAL' if denominator['basis_incomplete'] else 'ESTIMATED' if allocation != 'exact' and decimal(denominator['estimated_kg']) else 'EXACT'),
                    denominator=denominator, weighted_price_per_kg=ratio(priced_amount, priced_kg), priced_kg=kg(priced_kg), priced_amount=amount(priced_amount),
                    quality_rows=quality, events=events, field_events=selected_reports, months=months, warnings=warnings)

    @precise
    def read(self, e, a, date_from, date_to, currency='TRY', greenhouse_id=None, allocation='all', compare_previous=True):
        if date_from > date_to or (date_to - date_from).days > 366 or date_from.year < 1901 or date_to.year > 9998 or allocation not in ('all', 'exact', 'estimated'):
            raise ValidationError('Выберите корректный период до 366 дней и вид распределения')
        currency = currency.strip().upper()
        today = datetime.now(ZoneInfo('Europe/Istanbul')).date()
        season = season_start(date_from)
        gid = str(greenhouse_id) if greenhouse_id else None
        with self.factory() as u:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            borrowed = lambda: BorrowedUow(u)
            finance_service = FinanceAnalyticsService(borrowed, snapshot_owned=True)
            finance = finance_service.read(e, a, date_from, date_to, currency)
            areas = {x['id']: x for x in rows(u, 'SELECT * FROM core.work_areas WHERE entity_id=:e ORDER BY name,id', e=e)}
            roots = [x for x in areas.values() if x['kind'] == 'greenhouse' and not x['parent_id']]
            if gid and gid not in {x['id'] for x in roots}: raise ValidationError('Теплица не найдена в этой компании')
            references = rows(u, 'SELECT * FROM inventory.fertilizer_reference_versions WHERE entity_id=:e ORDER BY effective_from,id', e=e)
            receipts, reports = self._harvest_data(u, e, date_from, date_to, areas, finance_service)
            tasks = WorkersService(borrowed).overview(e)
            applications = FertilizerService(borrowed).calendar(e, a, date_from, date_to)['items']
            from .day_workers import DayWorkersService
            day_workers = DayWorkersService(borrowed).summary(e, a, date_from, date_to)
            window, comparison_reason = comparison_window(date_from, date_to, today) if compare_previous else (None, 'COMPARISON_NOT_REQUESTED')
            comparison_finance = comparison_harvest = comparison_reports = current_comparison_finance = None
            if window:
                current_start, current_end, previous_start, previous_end = window
                comparison_finance = finance_service.read(e, a, previous_start, previous_end, currency)
                comparison_harvest, comparison_reports = self._harvest_data(u, e, previous_start, previous_end, areas, finance_service)
                if current_end != date_to:
                    current_comparison_finance = finance_service.read(e, a, current_start, current_end, currency)
            greenhouses = []
            for greenhouse in roots:
                selected_id = greenhouse['id']
                area = verified_area(selected_id, areas, references, date_from, date_to)
                financial = finance_scope(finance, selected_id, area)
                harvest = self._harvest_scope(receipts, reports, selected_id, areas, currency, allocation, season, finance_service, date_from, date_to)
                selected_tasks = [x for x in tasks['tasks'] if x.get('greenhouse_id') == selected_id or (not x.get('greenhouse_id') and root_id(x.get('work_area_id'), areas) == selected_id)]
                # Undated and overdue open tasks remain visible, with their current
                # status explicitly separate from selected-period economics.
                selected_tasks = [x for x in selected_tasks if not x.get('planned_date') or str(date_from) <= x['planned_date'] <= str(date_to) or x['status'] not in ('completed', 'cancelled')]
                task_ids = {x['id'] for x in selected_tasks}
                selected_applications = [x for x in applications if x['greenhouse_id'] == selected_id]
                day_summary = next((x for x in day_workers['greenhouses'] if x['greenhouse_id'] == selected_id), {})
                work = dict(tasks=selected_tasks, attention=[x for x in tasks['attention'] if x['id'] in task_ids], applications=selected_applications,
                            upcoming_applications=[x for x in selected_applications if upcoming_application(x, today)],
                            day_workers=day_worker_money(day_summary, finance, date_from, date_to))
                comparison = dict(status='UNAVAILABLE', reason=comparison_reason)
                if window:
                    previous_area = verified_area(selected_id, areas, references, previous_start, previous_end)
                    previous_financial = finance_scope(comparison_finance, selected_id, previous_area)
                    previous_harvest = self._harvest_scope(comparison_harvest, comparison_reports, selected_id, areas, currency, allocation, season_start(previous_start), finance_service, previous_start, previous_end)
                    has_previous = bool(previous_financial['economic_events'] or previous_harvest['events'] or previous_harvest['field_events'])
                    current_financial = finance_scope(current_comparison_finance or finance, selected_id, verified_area(selected_id, areas, references, current_start, current_end))
                    current_harvest = self._harvest_scope([x for x in receipts if x['receipt']['event_date'] <= str(current_end)], [x for x in reports if x['event_date'] <= str(current_end)], selected_id, areas, currency, allocation, season, finance_service, current_start, current_end)
                    def comparable(financial, harvest):
                        return dict(revenue=financial['revenue'], operating_cost=financial['operating_cost'], operating_result=financial['operating_result'], branches=harvest['branches'], depot_kg=harvest['depot_kg'], per_da=financial['per_da'])
                    comparison = dict(status='AVAILABLE' if has_previous else 'NO_PRIOR_SOURCES', reason=None if has_previous else 'NO_CONFIRMED_PRIOR_PERIOD_SOURCES',
                        date_from=previous_start, date_to=previous_end, current_date_from=current_start, current_date_to=current_end,
                        previous=comparable(previous_financial, previous_harvest) if has_previous else None, current=comparable(current_financial, current_harvest))
                query = urlencode(dict(date_from=str(date_from), date_to=str(date_to), currency=currency, greenhouse_id=selected_id))
                links = dict(finance=f'/v1/entities/{e}/finance/analytics?{query}',
                    harvest=f'/v1/entities/{e}/harvest/monthly?'+urlencode(dict(season_start_year=season.year, greenhouse_id=selected_id, metric='kg', currency=currency, allocation=allocation)),
                    profile=f'/v1/entities/{e}/greenhouse-daily?{query}&allocation={allocation}', day_workers=f'/v1/entities/{e}/day-workers')
                greenhouses.append(dict(id=selected_id, name=greenhouse['name'], area=area, finance=financial, harvest=harvest, work=work, comparison=comparison, links=links))
            unknown_reports = [x for x in reports if not x['greenhouse_id'] or x['greenhouse_id'] not in areas]
            unresolved = [x for x in finance['economic_events'] if any(a['classification'] == 'UNRESOLVED' for a in x['allocations'])]
            coverage = dict(unresolved_cost=finance['economics']['unresolved_cost'], unallocated_revenue=finance['economics']['unallocated_revenue'],
                unallocated_depot_kg=kg(sum((x['unallocated'] for x in receipts), ZERO)), unknown_field_reports=unknown_reports,
                unallocated_settlement_ids=[x['receipt']['id'] for x in receipts if x['unallocated'] > 0 or x['unknown_basis'] > 0],
                unresolved_economic_events=unresolved, inventory_cost_rows=finance['coverage']['inventory_cost_rows'],
                unclassified_obligation_ids=finance['coverage']['unclassified_obligation_ids'], day_workers_warnings=day_workers.get('warnings', []),
                day_workers_unknown_plan_date_ids=day_workers.get('unknown_plan_date_ids', []),
                day_workers_unallocated_actual_ids=day_workers.get('unallocated_actual_ids', []),
                day_workers_unresolved_day_actual_ids=day_workers.get('unresolved_day_actual_ids', []),
                unallocated_depot_sources=[dict(settlement_id=x['receipt']['id'],evidence_id=x['receipt']['evidence_id'],event_date=x['receipt']['event_date'],currency=x['receipt']['currency'],
                    unallocated_kg=kg(x['unallocated']),unknown_basis_count=str(x['unknown_basis']),lines=x['lines']) for x in receipts if x['unallocated'] > 0 or x['unknown_basis'] > 0],
                scope='ENTITY_UNALLOCATED_COVERAGE_NOT_CHARGED_TO_EACH_GREENHOUSE')
            result = dict(entity_id=e, snapshot_at=finance['snapshot_at'], filters=dict(date_from=date_from, date_to=date_to, currency=currency, greenhouse_id=gid, allocation=allocation,
                season_start_year=season.year, season_start=season, season_end=date(season.year + 1, 10, 1)), greenhouses=greenhouses,
                selected=next((x for x in greenhouses if x['id'] == gid), None), coverage=coverage,
                warnings=sorted(set(finance['coverage']['warnings'] + ['BANK_BALANCES_NOT_GREENHOUSE_ALLOCATED', 'PAYMENTS_NOT_ADDED_TO_COST', 'AREA_VALIDITY_REQUIRED_FOR_PER_DA'])))
            return GreenhouseDaily.model_validate(result).model_dump(mode='json')
