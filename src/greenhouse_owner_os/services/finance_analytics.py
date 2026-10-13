"""One read-only snapshot of trusted Finance and source evidence.

The ledger remains authoritative. Virtual area allocation is never persisted.
Inventory valuations are coverage, never a second expense recognition. This is
current-active economic analysis, explicitly not a historical closing policy.
"""
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo
from sqlalchemy import select, text
from greenhouse_owner_os.domain.errors import NotFoundError, ValidationError
from greenhouse_owner_os.domain.finance_analytics import FinanceAnalytics, DebtDocument
from greenhouse_owner_os.domain.harvest_journey import apportion
from greenhouse_owner_os.infrastructure.models import EntityRow, WorkAreaRow
from .inventory import rows, precise
from .owner_capture import OwnerCaptureService
from .harvest_journey import HarvestJourneyService

ZERO = Decimal(0)
POLICY_ID = 'ADR033-AREA-2026-10-01'
POLICY_DATE = date(2026, 10, 1)
TARGETS = (('Yeşilöz', 20), ('İmamlı', 20), ('Özvadi', 30))
SOURCE_TABLES = {
    'obligation': 'finance.obligations', 'payment': 'finance.payments',
    'transfer': 'finance.own_account_transfers', 'owner_funding': 'finance.owner_fundings',
    'direct_settlement': 'finance.direct_settlements', 'advance_application': 'finance.advance_applications',
    'direct_expense': 'finance.direct_expenses',
}


def amount(value):
    return format(Decimal(value).quantize(Decimal('.01')), 'f')


def kg(value):
    return format(Decimal(value).quantize(Decimal('.001')), 'f')


def root_id(area_id, areas):
    """Only follow existing same-entity references; no spelling or alias guesses."""
    current = str(area_id) if area_id else None
    visited = set()
    while current in areas and current not in visited:
        visited.add(current)
        row = areas[current]
        if row['kind'] == 'greenhouse' and not row['parent_id']:
            return current
        current = row['parent_id']
    return None


def area_policy(areas):
    targets = []
    for name, weight in TARGETS:
        matches = [r for r in areas.values() if r['name'] == name and r['kind'] == 'greenhouse' and not r['parent_id']]
        targets.append(dict(name=name, greenhouse_id=matches[0]['id'] if len(matches) == 1 else None,
                            numerator=weight, denominator=70, area_da=str(weight),
                            display_percentage='42.8571' if weight == 30 else '28.5714'))
    return dict(id=POLICY_ID, version=1, effective_from=str(POLICY_DATE), effective_to=None,
                algorithm='largest-remainder-cent-v1', affects_ledger=False, targets=targets,
                status='AVAILABLE' if all(t['greenhouse_id'] for t in targets) else 'UNRESOLVED_REFERENCE')


def allocate_cost(total, event_date, direct, general, areas, policy):
    """Direct exact evidence first; only confirmed GENERAL remainder is virtual."""
    result = []
    assigned = ZERO
    for item in direct:
        value = Decimal(item['amount'])
        assigned += value
        aid = str(item['work_area_id'])
        result.append(dict(work_area_id=aid, greenhouse_id=root_id(aid, areas), name=areas.get(aid, {}).get('name'),
                           amount=amount(value), classification='EXACT', provenance=item.get('source', 'OWNER_CONFIRMED_ATTRIBUTION')))
    residual = total - assigned
    # Negative economic corrections retain signed values. They are not new
    # source evidence and are never fed as positive weights into allocation.
    if residual:
        if general and event_date >= POLICY_DATE and policy['status'] == 'AVAILABLE' and residual > 0:
            weights = {t['greenhouse_id']: Decimal(t['numerator']) for t in policy['targets']}
            for aid, value in apportion(residual, '.01', weights, Decimal(70)).items():
                result.append(dict(work_area_id=aid, greenhouse_id=aid, name=areas[aid]['name'], amount=amount(value),
                                   classification='GENERAL_VIRTUAL', provenance='OWNER_CONFIRMED_ABSENT_GEOGRAPHY',
                                   policy_id=POLICY_ID, policy_version=1))
        else:
            why = 'GENERAL_POLICY_NOT_EFFECTIVE' if general and event_date < POLICY_DATE else 'GENERAL_REFERENCE_UNRESOLVED' if general else 'SOURCE_GEOGRAPHY_UNRESOLVED'
            result.append(dict(amount=amount(residual), classification='UNRESOLVED', provenance=why))
    return result


class FinanceAnalyticsService:
    def __init__(self, factory, *, snapshot_owned=False):
        # Internal composition only: the caller must already own a read-only snapshot.
        self.snapshot_owned = snapshot_owned
        self.factory = factory
        self.owner = OwnerCaptureService(factory)
        self.harvest = HarvestJourneyService(factory)

    def _snapshot(self, u):
        if not self.snapshot_owned:
            u.session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        elif (u.session.scalar(text('SHOW transaction_isolation')) != 'repeatable read'
              or u.session.scalar(text('SHOW transaction_read_only')) != 'on'):
            raise ValidationError('The composed read requires an owned repeatable-read read-only snapshot')

    def _sources(self, u, e):
        parties = {r['id']: r['name'] for r in rows(u, 'SELECT id,name FROM core.counterparties WHERE entity_id=:e', e=e)}
        sources = {}
        records = {}
        for kind, table in SOURCE_TABLES.items():
            for r in rows(u, 'SELECT * FROM '+table+' WHERE entity_id=:e', e=e):
                key = (kind, r['id'])
                records[key] = r
                sources[key] = dict(kind=kind, id=r['id'], status=r['status'], description=r.get('description') or r.get('reason'),
                                    counterparty_id=r.get('counterparty_id'), counterparty_name=parties.get(r.get('counterparty_id')),source_reference=r.get('source'),
                                    replacement_of_id=r.get('replacement_of_id'))
        for r in rows(u, '''SELECT c.actual_id,c.obligation_id,d.request_id
                FROM workers.day_accruals c JOIN workers.day_actuals d ON d.id=c.actual_id AND d.entity_id=c.entity_id
                WHERE c.entity_id=:e''', e=e):
            key = ('obligation', r['obligation_id'])
            if key in sources:
                sources[key].update(day_actual_id=r['actual_id'], day_request_id=r['request_id'])
        reviews = {}
        for r in rows(u, '''SELECT c.obligation_id,c.payment_id,c.direct_expense_id,c.proposal_id,c.version,
                p.inbox_id,r.payload,r.provenance,r.missing
                FROM core.confirmation_effects c JOIN core.proposed_actions p ON p.id=c.proposal_id AND p.entity_id=c.entity_id
                JOIN core.proposal_revisions r ON r.proposal_id=c.proposal_id AND r.version=c.version AND r.entity_id=c.entity_id
                WHERE c.entity_id=:e''', e=e):
            for kind, col in [('obligation', 'obligation_id'), ('payment', 'payment_id'), ('direct_expense', 'direct_expense_id')]:
                if r[col]:
                    key = (kind, r[col])
                    if key in sources:
                        sources[key].update(proposal_id=r['proposal_id'], proposal_version=r['version'], original_id=r['inbox_id'])
                        reviews[key] = r
        for r in rows(u, 'SELECT id,obligation_id,evidence_id FROM harvest.depot_settlements WHERE entity_id=:e', e=e):
            key = ('obligation', r['obligation_id'])
            if key in sources:
                sources[key].update(harvest_settlement_id=r['id'], evidence_id=r['evidence_id'])
        for r in rows(u, "SELECT id,finance_id,original_text FROM core.capture_drafts WHERE entity_id=:e AND finance_kind='obligation' AND finance_id IS NOT NULL", e=e):
            key = ('obligation', r['finance_id'])
            if key in sources:
                sources[key]['capture_id'] = r['id']
                sources[key]['original_text'] = r['original_text']
        return sources, records, reviews, parties

    def _debt_documents(self, u, e, currency, sources, areas, parties):
        documents = []
        today = datetime.now(ZoneInfo('Europe/Istanbul')).date()
        data = rows(u, '''SELECT b.*,o.status,o.event_date,o.due_date,o.obligation_class,o.economic_source,
                x.opening_date,x.source opening_source
            FROM finance.obligation_balances b JOIN finance.obligations o ON o.id=b.obligation_id
            LEFT JOIN finance.opening_payables x ON x.obligation_id=o.id AND x.entity_id=o.entity_id
            WHERE b.entity_id=:e AND (CAST(:currency AS text) IS NULL OR b.currency=:currency)
            ORDER BY o.event_date DESC,o.id''', e=e, currency=currency)
        attrs = defaultdict(list)
        for r in rows(u, '''SELECT a.* FROM finance.obligation_attributions a JOIN finance.obligations o ON o.id=a.obligation_id
                            WHERE o.entity_id=:e ORDER BY a.work_area_id''', e=e):
            attrs[r['obligation_id']].append(r)
        history = defaultdict(list)
        for r in rows(u, '''SELECT a.obligation_id,p.id,p.status,p.event_date,a.amount,p.currency,p.money_account_id,
                p.posting_id,p.reversal_posting_id,p.source FROM finance.payment_allocations a
                JOIN finance.payments p ON p.id=a.payment_id WHERE p.entity_id=:e ORDER BY p.event_date,p.id''', e=e):
            oid = r.pop('obligation_id'); history[oid].append(dict(kind='payment', **r))
        for r in rows(u, 'SELECT * FROM finance.direct_settlements WHERE entity_id=:e ORDER BY event_date,id', e=e):
            h = dict(kind='direct_settlement', **{k:r.get(k) for k in ('id','status','event_date','amount','currency','posting_id','reversal_posting_id','source')})
            history[r['receivable_id']].append(h); history[r['payable_id']].append(h)
        for r in rows(u, 'SELECT * FROM finance.advance_applications WHERE entity_id=:e ORDER BY event_date,id', e=e):
            history[r['obligation_id']].append(dict(kind='advance_application', **{k:r.get(k) for k in ('id','status','event_date','amount','currency','advance_id','posting_id','reversal_posting_id','source')}))
        for r in data:
            oid = r['obligation_id']
            documents.append(dict(**{k:r[k] for k in ('obligation_id','counterparty_id','direction','currency','original_amount','allocated_amount','remaining_amount','status','settlement_status','event_date','due_date','obligation_class','economic_source','opening_date','opening_source')},
                counterparty_name=parties[r['counterparty_id']], opening=bool(r['opening_date']),
                overdue=r['status']=='posted' and Decimal(r['remaining_amount'])>0 and bool(r['due_date']) and date.fromisoformat(r['due_date'])<today,
                source=sources[('obligation', oid)],
                attributions=[dict(work_area_id=a['work_area_id'],greenhouse_id=root_id(a['work_area_id'],areas),name=areas.get(a['work_area_id'],{}).get('name'),amount=a['amount'],classification='EXACT',provenance=a['source']) for a in attrs[oid]],
                history=sorted(history[oid], key=lambda h:(h['event_date'],h['id']))))
        grouped = {}
        receivable = payable = ZERO
        for r in documents:
            if r['status'] != 'posted': continue
            value = Decimal(r['remaining_amount'])
            group = grouped.setdefault(r['counterparty_id'], dict(counterparty_id=r['counterparty_id'], name=r['counterparty_name'], receivable=ZERO, payable=ZERO, document_count=0))
            group[r['direction']] += value; group['document_count'] += 1
            if r['direction'] == 'receivable': receivable += value
            else: payable += value
        return dict(receivable=amount(receivable), payable=amount(payable), documents=documents,
                    parties=[dict(g, receivable=amount(g['receivable']), payable=amount(g['payable'])) for g in grouped.values()])

    def document(self, e, a, oid):
        with self.factory() as u:
            self._snapshot(u)
            self.owner.owner(u,e,a)
            sources, _, _, parties = self._sources(u,e)
            areas = {r['id']:r for r in rows(u,'SELECT * FROM core.work_areas WHERE entity_id=:e',e=e)}
            documents = self._debt_documents(u,e,None,sources,areas,parties)['documents']
            document = next((r for r in documents if r['obligation_id']==str(oid)),None)
            if not document: raise NotFoundError('Документ не найден в этой компании')
            return DebtDocument.model_validate(document).model_dump(mode='json')

    def _harvest_sources(self, u, e, start, end, areas):
        receipts = []
        for receipt in rows(u, "SELECT * FROM harvest.depot_settlements WHERE entity_id=:e AND status='posted' AND event_date BETWEEN :start AND :end ORDER BY event_date,id", e=e,start=start,end=end):
            journey = self.harvest._view(u,e,UUID(receipt['id']))
            estimate = journey['current_estimate']
            stale = journey['estimates'][-1] if journey['estimates'] and journey['estimates'][-1]['stale'] else None
            total = ZERO; exact = defaultdict(lambda:ZERO); estimated = defaultdict(lambda:ZERO)
            allocations = []
            for line in rows(u,'SELECT * FROM harvest.settlement_lines WHERE settlement_id=:s ORDER BY id',s=receipt['id']):
                total += Decimal(line['net_weight_kg'])
                for alloc in rows(u,'SELECT * FROM harvest.line_allocations WHERE line_id=:l ORDER BY work_area_id',l=line['id']):
                    gid = root_id(alloc['work_area_id'],areas)
                    if gid:
                        exact[gid] += Decimal(alloc['net_weight_kg'])
                        allocations.append(dict(work_area_id=alloc['work_area_id'],greenhouse_id=gid,name=areas[alloc['work_area_id']]['name'],amount=amount(alloc['amount']),classification='EXACT',provenance='CONFIRMED_DEPOT_LINE'))
            if estimate:
                for alloc in estimate['lines']:
                    gid = root_id(alloc['work_area_id'],areas)
                    if gid:
                        estimated[gid] += Decimal(alloc['net_weight_kg'])
                        allocations.append(dict(work_area_id=alloc['work_area_id'],greenhouse_id=gid,name=areas[alloc['work_area_id']]['name'],amount=amount(alloc['amount']),classification='ESTIMATED',provenance='OWNER_CONFIRMED_HARVEST_ESTIMATE',estimate_revision_id=estimate['id']))
            linked_roots={r['greenhouse_id'] for r in rows(u,'SELECT DISTINCT greenhouse_id FROM harvest.worker_reports WHERE id=ANY(CAST(:ids AS uuid[]))',ids=journey['report_ids'])} if journey['report_ids'] else set()
            linked_roots.discard(None)
            if receipt['greenhouse_id']:linked_roots.add(receipt['greenhouse_id'])
            receipts.append(dict(receipt=receipt,total=total,exact=exact,estimated=estimated,allocations=allocations,
                                 linked_roots=linked_roots,unknown_basis=Decimal(estimate['unknown_basis_count']) if estimate else ZERO,
                                 unallocated=total-sum(exact.values(),ZERO)-sum(estimated.values(),ZERO),estimate=estimate,stale=stale))
        return receipts

    def _denominator(self, receipts, gid):
        exact = estimated = unallocated = ZERO; sources = []; stale = 0; incomplete = False
        for r in receipts:
            ex = r['exact'].get(gid,ZERO) if gid else r['total']
            est = r['estimated'].get(gid,ZERO) if gid else ZERO
            missing = r['unallocated']
            basis_missing = r['unknown_basis'] > 0
            selected_known = gid in r['exact'] or gid in r['estimated']
            # Match Harvest's source-aware scope: unknown farm receipts are not
            # copied into every greenhouse. Linked missing origins remain visible.
            if gid and not selected_known and not ((missing>0 or basis_missing) and gid in r['linked_roots']):
                continue
            incomplete = incomplete or bool(gid and (missing>0 or basis_missing))
            exact += ex; estimated += est; unallocated += missing
            stale += bool(r['stale'])
            sources.append(dict(settlement_id=r['receipt']['id'],event_date=r['receipt']['event_date'],evidence_id=r['receipt']['evidence_id'],
                exact_kg=kg(ex),estimated_kg=kg(est),unallocated_kg=kg(missing),source_total_kg=kg(r['total']),
                estimate_revision_id=r['estimate']['id'] if r['estimate'] else None,unknown_basis_count=str(r['unknown_basis']),
                stale_estimate_revision_id=r['stale']['id'] if r['stale'] else None))
        total = exact+estimated
        status = 'UNAVAILABLE' if total<=0 else 'PARTIAL' if incomplete else 'ESTIMATED' if estimated else 'EXACT'
        return dict(confirmed_kg=kg(total) if total>0 else None,exact_kg=kg(exact),estimated_kg=kg(estimated),unallocated_kg=kg(unallocated),classification=status,
                    source_count=len(sources),stale_estimate_count=stale,basis_incomplete=incomplete,sources=sources)

    @staticmethod
    def _ratio(cost, denominator, unresolved=False):
        total = Decimal(denominator['confirmed_kg']) if denominator['confirmed_kg'] is not None else ZERO
        partial = unresolved or denominator['classification']=='PARTIAL'
        ratio = format((cost/total).quantize(Decimal('.0001')),'f') if total>0 and not partial else None
        return dict(value=None,known_finance_cost_per_kg=ratio,
                    classification='UNAVAILABLE' if total<=0 else 'PARTIAL' if partial else 'ESTIMATED_DENOMINATOR' if denominator['estimated_kg']!='0.000' else 'KNOWN_FINANCE_ONLY',
                    reason='Полная производственная себестоимость не установлена. Отдельный показатель использует только подтверждённые расходы Finance; складская стоимость повторно не прибавляется.')

    @precise
    def read(self,e,a,date_from,date_to,currency='TRY',greenhouse_id=None,money_account_id=None):
        currency=currency.strip().upper()
        if len(currency)!=3 or not currency.isascii() or not currency.isalpha() or date_from>date_to:
            raise ValidationError('Укажите корректный период и одну валюту')
        with self.factory() as u:
            self._snapshot(u)
            self.owner.owner(u,e,a)
            entity=u.session.get(EntityRow,e)
            if not entity: raise NotFoundError('Компания не найдена')
            snapshot=u.session.scalar(text('SELECT transaction_timestamp()'))
            areas={r['id']:r for r in rows(u,'SELECT * FROM core.work_areas WHERE entity_id=:e ORDER BY name,id',e=e)}
            gid=str(greenhouse_id) if greenhouse_id else None
            if gid and (gid not in areas or areas[gid]['kind']!='greenhouse' or areas[gid]['parent_id']):
                raise ValidationError('Теплица не найдена в этой компании')
            policy=area_policy(areas)
            sources,records,reviews,parties=self._sources(u,e)
            debts=self._debt_documents(u,e,currency,sources,areas,parties)
            # This immutable journal, including compensation postings, is the
            # sole cash basis. Current active economic sources are a different view.
            journal=rows(u,'''SELECT p.id posting_id,p.source_type,p.source_id,p.event_date,p.currency,p.reversal_of_id,
                pe.id entry_id,pe.ledger_account_id,pe.side,pe.amount,l.account_class,l.name ledger_account_name,
                ma.id money_account_id,ma.name account_name
                FROM finance.postings p JOIN finance.posting_entries pe ON pe.posting_id=p.id
                JOIN finance.ledger_accounts l ON l.id=pe.ledger_account_id AND l.entity_id=p.entity_id
                LEFT JOIN finance.money_accounts ma ON ma.ledger_account_id=l.id AND ma.entity_id=p.entity_id
                WHERE p.entity_id=:e AND p.currency=:currency ORDER BY p.event_date,p.id,pe.id''',e=e,currency=currency)
            account_rows=rows(u,'''SELECT ma.*,b.balance FROM finance.money_accounts ma
                JOIN finance.money_account_balances b ON b.money_account_id=ma.id AND b.entity_id=ma.entity_id
                WHERE ma.entity_id=:e AND ma.currency=:currency ORDER BY ma.name,ma.id''',e=e,currency=currency)
            if money_account_id and not any(r['id']==str(money_account_id) for r in account_rows):
                raise ValidationError('Счёт не найден в выбранной компании и валюте')
            available_accounts=[dict(money_account_id=r['id'],name=r['name'],currency=r['currency']) for r in account_rows]
            account_rows=[r for r in account_rows if not money_account_id or r['id']==str(money_account_id)]
            selected_accounts={r['id'] for r in account_rows}; movements=[]; account_values=defaultdict(lambda:dict(prior=ZERO,incoming=ZERO,outgoing=ZERO))
            for j in journal:
                aid=j['money_account_id']
                if not aid or aid not in selected_accounts:continue
                signed=Decimal(j['amount'])*(1 if j['side']=='debit' else -1); day=date.fromisoformat(j['event_date'])
                values=account_values[aid]
                if day<date_from:values['prior']+=signed
                if not date_from<=day<=date_to:continue
                values['incoming' if signed>0 else 'outgoing']+=abs(signed)
                source=sources.get((j['source_type'],j['source_id']),dict(kind=j['source_type'],id=j['source_id'],status='JOURNAL'))
                movements.append(dict(posting_id=j['posting_id'],money_account_id=aid,account_name=j['account_name'],currency=currency,event_date=j['event_date'],
                    direction='in' if signed>0 else 'out',amount=amount(abs(signed)),signed_amount=amount(signed),reversal_of_id=j['reversal_of_id'],source=source))
            accounts=[]
            for r in account_rows:
                v=account_values[r['id']]; net=v['incoming']-v['outgoing']
                accounts.append(dict(money_account_id=r['id'],name=r['name'],account_type=r['account_type'],currency=currency,active=r['active'],current_balance=amount(r['balance']),
                    opening_balance=amount(v['prior']) if date_from>=entity.finance_cutover_date else None,
                    closing_balance=amount(v['prior']+net) if date_to>=entity.finance_cutover_date else None,
                    incoming=amount(v['incoming']),outgoing=amount(v['outgoing']),net_change=amount(net),
                    opening_basis='ZERO_AT_CUTOVER_PLUS_PRIOR_JOURNAL' if date_from>=entity.finance_cutover_date else 'UNKNOWN_BEFORE_CUTOVER'))
            receipts=self._harvest_sources(u,e,date_from,date_to,areas)
            receipts_by_obligation={r['receipt']['obligation_id']:r for r in receipts}
            attrs=defaultdict(list)
            for r in rows(u,'''SELECT a.* FROM finance.obligation_attributions a JOIN finance.obligations o ON o.id=a.obligation_id
                                WHERE o.entity_id=:e ORDER BY a.work_area_id''',e=e):attrs[r['obligation_id']].append(r)
            openings={r['obligation_id'] for r in rows(u,'SELECT obligation_id FROM finance.opening_payables WHERE entity_id=:e',e=e)}
            economic_events=[]
            for j in journal:
                day=date.fromisoformat(j['event_date']); key=(j['source_type'],j['source_id']); record=records.get(key)
                if j['reversal_of_id'] or not date_from<=day<=date_to or not record or record['status']!='posted':continue
                if record.get('obligation_class')=='STRATEGIC' or j['source_id'] in openings:continue
                if j['account_class'] not in ('expense','consumption','revenue'):continue
                is_cost=j['account_class'] in ('expense','consumption')
                value=Decimal(j['amount'])*(1 if j['side']==('debit' if is_cost else 'credit') else -1)
                reviewed=reviews.get(key); direct=attrs[j['source_id']] if j['source_type']=='obligation' else []
                if j['source_type']=='direct_expense' and reviewed:direct=reviewed['payload'].get('attributions',[])
                # Only owner-confirmed empty geography is intentional GENERAL.
                # A partly assigned or legacy residual is not such evidence.
                general=bool(is_cost and reviewed and not reviewed['payload'].get('attributions') and not reviewed['missing'])
                if is_cost:
                    allocations=allocate_cost(value,day,direct,general,areas,policy)
                else:
                    receipt=receipts_by_obligation.get(j['source_id'])
                    allocations=list(receipt['allocations']) if receipt else allocate_cost(value,day,direct,False,areas,policy)
                    residual=value-sum((Decimal(x['amount']) for x in allocations),ZERO)
                    if receipt and residual:allocations.append(dict(amount=amount(residual),classification='UNRESOLVED',provenance='HARVEST_SOURCE_UNALLOCATED'))
                selected=sum((Decimal(x['amount']) for x in allocations if x.get('greenhouse_id')==gid),ZERO) if gid else value
                geography='GENERAL' if general else 'ESTIMATED' if any(x['classification']=='ESTIMATED' for x in allocations) else 'PARTIAL' if direct and sum((Decimal(x['amount']) for x in direct),ZERO)!=value else 'EXACT' if allocations and all(x['classification']=='EXACT' for x in allocations) else 'UNRESOLVED'
                economic_events.append(dict(posting_id=j['posting_id'],entry_id=j['entry_id'],event_date=j['event_date'],currency=currency,
                    classification='OPERATING_COST' if is_cost else 'REVENUE',account_class=j['account_class'],ledger_account_id=j['ledger_account_id'],ledger_account_name=j['ledger_account_name'],
                    amount=amount(value),selected_amount=amount(selected),source_geography=geography,source=sources[key],allocations=allocations))
            def economics(scope):
                revenue=cost=exact=virtual=unresolved=unallocated=estimated_revenue=ZERO
                for event in economic_events:
                    is_cost=event['classification']=='OPERATING_COST'
                    selected=sum((Decimal(x['amount']) for x in event['allocations'] if x.get('greenhouse_id')==scope),ZERO) if scope else Decimal(event['amount'])
                    if is_cost:cost+=selected
                    else:revenue+=selected
                    for x in event['allocations']:
                        n=Decimal(x['amount'])
                        if x['classification']=='UNRESOLVED':
                            if is_cost:unresolved+=n
                            else:unallocated+=n
                        if scope and x.get('greenhouse_id')!=scope:continue
                        if is_cost and x['classification']=='EXACT':exact+=n
                        if is_cost and x['classification']=='GENERAL_VIRTUAL':virtual+=n
                        if not is_cost and x['classification']=='ESTIMATED':estimated_revenue+=n
                return dict(revenue=amount(revenue),operating_cost=amount(cost),operating_result=amount(revenue-cost),exact_cost=amount(exact),
                    virtual_general_cost=amount(virtual),unresolved_cost=amount(unresolved),unallocated_revenue=amount(unallocated),
                    completeness='PARTIAL_SCOPE' if scope and (unresolved or unallocated) else 'CONFIRMED_FINANCE_ONLY'),estimated_revenue
            selected_economics,_=economics(gid)
            denominator=self._denominator(receipts,gid)
            area_results=[]
            for aid,area in areas.items():
                if area['kind']!='greenhouse' or area['parent_id']:continue
                sums,estimated= economics(aid);den=self._denominator(receipts,aid)
                area_results.append(dict(greenhouse_id=aid,name=area['name'],**{k:sums[k] for k in ('revenue','operating_cost','operating_result','exact_cost','virtual_general_cost')},
                    estimated_revenue=amount(estimated),harvest_denominator=den,cost_per_kg=dict(currency=currency,**self._ratio(Decimal(sums['operating_cost']),den,Decimal(sums['unresolved_cost'])!=0))))
            inventory=[]
            for r in rows(u,'''SELECT ev.id event_id,ev.kind,ev.event_date,ev.payload->>'finance_obligation_id' finance_obligation_id,
                m.product_id,p.name product_name,p.base_unit,p.currency,sum(abs(m.quantity)) quantity,
                sum(abs(m.uncosted_quantity)) uncosted_quantity,sum(abs(m.known_cost)) known_cost
                FROM inventory.events ev JOIN inventory.movements m ON m.event_id=ev.id AND m.role<>'reversal'
                JOIN inventory.products p ON p.id=m.product_id
                WHERE ev.entity_id=:e AND ev.status='posted' AND ev.kind IN ('receipt','application','writeoff')
                  AND ev.event_date BETWEEN :start AND :end AND (p.currency=:currency OR p.currency IS NULL)
                GROUP BY ev.id,m.product_id,p.name,p.base_unit,p.currency ORDER BY ev.event_date,ev.id,m.product_id''',e=e,start=date_from,end=date_to,currency=currency):
                links={x['finance_obligation_id'] for x in rows(u,'SELECT finance_obligation_id FROM core.invoice_receipt_links WHERE entity_id=:e AND receipt_id=:id AND finance_obligation_id IS NOT NULL',e=e,id=r['event_id'])}
                direct=r.pop('finance_obligation_id')
                if direct:links.add(direct)
                recognized={x for x in links if records.get(('obligation',x),{}).get('status')=='posted' and any(ev['source']['id']==x and ev['classification']=='OPERATING_COST' for ev in economic_events)}
                inventory.append(dict(**r,finance_obligation_ids=sorted(links),recognition='FINANCE_BASIS_ONLY' if recognized else 'UNRESOLVED_LINK_OR_TIMING'))
            classified={r['source']['id'] for r in economic_events if r['source']['kind']=='obligation'}
            unclassified=[r['id'] for (kind,_),r in records.items() if kind=='obligation' and r['status']=='posted' and r['currency']==currency and r['id'] not in classified and r['id'] not in openings and r['obligation_class']!='STRATEGIC' and date_from<=date.fromisoformat(r['event_date'])<=date_to]
            warnings=['CURRENT_ACTIVE_NOT_HISTORICAL_AS_OF','COMPLETE_PRODUCTION_COST_NOT_ESTABLISHED','CASH_AND_DEBTS_NOT_GREENHOUSE_ALLOCATED']
            if date_from<entity.finance_cutover_date:warnings.append('PERIOD_START_BEFORE_VERIFIED_FINANCE_CUTOVER')
            if policy['status']!='AVAILABLE':warnings.append('AREA_POLICY_REFERENCE_UNRESOLVED')
            if inventory:warnings.append('INVENTORY_VALUATION_NOT_ADDED_TO_FINANCE_EXPENSE')
            if any(Decimal(r['uncosted_quantity'])>0 for r in inventory):warnings.append('INVENTORY_COST_UNKNOWN')
            if any(r['recognition']=='UNRESOLVED_LINK_OR_TIMING' for r in inventory):warnings.append('INVENTORY_FINANCE_OVERLAP_OR_TIMING_UNRESOLVED')
            if unclassified:warnings.append('ECONOMIC_CLASSIFICATION_UNRESOLVED')
            if denominator['stale_estimate_count']:warnings.append('STALE_HARVEST_ESTIMATE_EXCLUDED')
            incoming=sum((Decimal(r['incoming']) for r in accounts),ZERO);outgoing=sum((Decimal(r['outgoing']) for r in accounts),ZERO)
            result=dict(entity_id=e,snapshot_at=snapshot,currency=currency,period=dict(date_from=date_from,date_to=date_to),greenhouse_id=greenhouse_id,money_account_id=money_account_id,
                cutover_date=entity.finance_cutover_date,finance_opening_policy=entity.finance_opening_policy,accounts=accounts,available_accounts=available_accounts,
                cash_totals=dict(incoming=amount(incoming),outgoing=amount(outgoing),net_change=amount(incoming-outgoing)),movements=movements,debts=debts,economics=selected_economics,
                economic_events=economic_events,areas=area_results,harvest_denominator=denominator,
                cost_per_kg=dict(currency=currency,**self._ratio(Decimal(selected_economics['operating_cost']),denominator,bool(gid and Decimal(selected_economics['unresolved_cost'])!=0))),allocation_policy=policy,
                coverage=dict(inventory_cost_rows=inventory,unclassified_obligation_ids=unclassified,warnings=warnings,source_count=len({(r['source']['kind'],r['source']['id']) for r in economic_events}),economic_event_count=len(economic_events),movement_count=len(movements)))
            return FinanceAnalytics.model_validate(result).model_dump(mode='json')
