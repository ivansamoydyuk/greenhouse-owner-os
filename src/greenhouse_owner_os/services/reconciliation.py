"""Read-only deterministic Finance checks; never repairs domain or journal rows."""
from collections import Counter, defaultdict
from decimal import Decimal
from sqlalchemy import select, text
from greenhouse_owner_os.infrastructure.models import (
    ObligationRow, PaymentRow, PaymentAllocationRow, AdvanceRow, ApplicationRow,
    PostingRow, PostingEntryRow, LedgerAccountRow, MoneyAccountRow, CounterpartyRow,
    TransferRow, FundingRow, SettlementRow,
)


def reconcile(session):
    # A consistent snapshot is necessary: independent READ COMMITTED selects could
    # otherwise report a valid concurrent transaction as a false inconsistency.
    session.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
    models = [ObligationRow, PaymentRow, PaymentAllocationRow, AdvanceRow, ApplicationRow,
              PostingRow, PostingEntryRow, LedgerAccountRow, MoneyAccountRow, CounterpartyRow,
              TransferRow, FundingRow, SettlementRow]
    rows = {m: {r.id:r for r in session.scalars(select(m).order_by(m.id)).all()} for m in models}
    return check_snapshot(rows)


def check_snapshot(rows):
    findings = []
    def fail(code, rid): findings.append({'code':code,'id':str(rid)})
    obs,pays,allocs,advs,apps,posts,entries,ledgers,accounts,cps,transfers,fundings,settlements = (
        rows[m] for m in [ObligationRow, PaymentRow, PaymentAllocationRow, AdvanceRow, ApplicationRow,
        PostingRow, PostingEntryRow, LedgerAccountRow, MoneyAccountRow, CounterpartyRow, TransferRow, FundingRow, SettlementRow])
    journals = defaultdict(Counter)
    for e in entries.values():
        p,l = posts.get(e.posting_id),ledgers.get(e.ledger_account_id)
        if not p or not l: fail('orphan_entry',e.id); continue
        if (p.entity_id,p.currency)!=(l.entity_id,l.currency): fail('entry_scope_mismatch',e.id)
        if e.amount<=0 or e.side not in ('debit','credit'): fail('invalid_entry',e.id)
        journals[p.id][(e.ledger_account_id,e.side)] += e.amount
    def canonical(counter): return {k:v for k,v in counter.items() if v}
    for p in posts.values():
        j=journals[p.id]
        count=sum(1 for e in entries.values() if e.posting_id==p.id)
        if count<2 or sum(v if side=='debit' else -v for (_,side),v in j.items())!=0: fail('unbalanced_posting',p.id)
        if p.reversal_of_id:
            original=posts.get(p.reversal_of_id)
            if not original or original.reversal_of_id or (p.source_type,p.source_id,p.entity_id,p.currency)!=(original.source_type,original.source_id,original.entity_id,original.currency) or p.event_date<original.event_date:
                fail('orphan_or_invalid_reversal',p.id)
            else:
                expected=Counter({(a,'credit' if s=='debit' else 'debit'):v for (a,s),v in journals[original.id].items()})
                if canonical(expected)!=canonical(j): fail('inexact_reversal',p.id)
    allocated=defaultdict(Decimal); payment_totals=defaultdict(Decimal)
    for a in allocs.values():
        p,o=pays.get(a.payment_id),obs.get(a.obligation_id)
        if not p or not o: fail('orphan_allocation',a.id); continue
        if a.amount<=0 or (p.entity_id,p.counterparty_id,p.currency)!= (o.entity_id,o.counterparty_id,o.currency) or o.direction!=('payable' if p.direction=='out' else 'receivable'): fail('allocation_scope_mismatch',a.id)
        payment_totals[p.id]+=a.amount
        if p.status=='posted':
            if o.status!='posted': fail('inactive_allocated_obligation',a.id)
            allocated[o.id]+=a.amount
    applied_adv=defaultdict(Decimal)
    for a in apps.values():
        adv,o=advs.get(a.advance_id),obs.get(a.obligation_id)
        if not adv or not o: fail('orphan_application',a.id); continue
        if a.amount<=0 or (a.entity_id,a.counterparty_id,a.currency,a.direction)!=(adv.entity_id,adv.counterparty_id,adv.currency,adv.direction) or (a.entity_id,a.counterparty_id,a.currency)!=(o.entity_id,o.counterparty_id,o.currency) or o.direction!=('payable' if a.direction=='paid' else 'receivable') or a.event_date<max(adv.event_date,o.event_date): fail('application_scope_mismatch',a.id)
        if a.status=='posted':
            p=pays.get(adv.source_payment_id)
            if adv.status!='open' or not p or p.status!='posted' or o.status!='posted': fail('inactive_application_source',a.id)
            applied_adv[adv.id]+=a.amount;allocated[o.id]+=a.amount
    for d in settlements.values():
        for oid,direction in [(d.receivable_id,'receivable'),(d.payable_id,'payable')]:
            o=obs.get(oid)
            if not o or (o.entity_id,o.currency,o.direction)!=(d.entity_id,d.currency,direction): fail('settlement_scope_mismatch',d.id)
            elif d.status=='posted':
                if o.status!='posted': fail('inactive_settlement_source',d.id)
                allocated[oid]+=d.amount
    advances_by_payment=defaultdict(list)
    for a in advs.values():
        advances_by_payment[a.source_payment_id].append(a)
        p=pays.get(a.source_payment_id)
        if not p or (a.entity_id,a.counterparty_id,a.currency,a.direction)!=(p.entity_id,p.counterparty_id,p.currency,'paid' if p.direction=='out' else 'received') or a.event_date!=p.event_date or a.original_amount!=p.amount-payment_totals[p.id] or a.status!=('reversed' if p.status=='reversed' else 'open') or p.status not in ('posted','reversed'): fail('advance_source_inconsistency',a.id)
        if applied_adv[a.id]>a.original_amount or (a.status=='reversed' and applied_adv[a.id]): fail('negative_advance_remaining',a.id)
    for p in pays.values():
        if payment_totals[p.id]>p.amount: fail('payment_overallocated',p.id)
        if p.status in ('posted','reversed') and len(advances_by_payment[p.id])!=(1 if p.amount>payment_totals[p.id] else 0): fail('payment_advance_inconsistency',p.id)
    for o in obs.values():
        if allocated[o.id]>o.original_amount or (o.status=='reversed' and allocated[o.id]): fail('negative_obligation_remaining',o.id)
    system={(l.entity_id,l.currency,l.system_code):l.id for l in ledgers.values() if l.system_code}
    def control(r,code): return system.get((r.entity_id,r.currency,code))
    def cash(aid): return accounts[aid].ledger_account_id if aid in accounts else None
    domains={'obligation':obs,'payment':pays,'advance_application':apps,'transfer':transfers,'owner_funding':fundings,'direct_settlement':settlements}
    for kind,records in domains.items():
        for r in records.values():
            if hasattr(r,'counterparty_id') and (r.counterparty_id not in cps or cps[r.counterparty_id].entity_id!=r.entity_id): fail('counterparty_scope_mismatch',r.id)
            if r.replacement_of_id:
                old=records.get(r.replacement_of_id)
                if not old or old.status!='reversed' or (old.entity_id,old.currency,old.counterparty_id,old.direction)!=(r.entity_id,r.currency,r.counterparty_id,r.direction): fail('orphan_replacement',r.id)
            if r.status not in ('posted','reversed'): continue
            p=posts.get(r.posting_id)
            if not p or (p.entity_id,p.currency,p.source_type,p.source_id,p.event_date,p.reversal_of_id)!=(r.entity_id,r.currency,kind,r.id,r.event_date,None): fail('domain_posting_linkage',r.id);continue
            if r.status=='reversed':
                rev=posts.get(r.reversal_posting_id)
                if not rev or rev.reversal_of_id!=p.id or rev.created_by!=r.reversed_by or rev.reason!=r.reversal_reason: fail('domain_reversal_linkage',r.id)
            expected=Counter()
            def line(account,side,amount): expected[(account,side)]+=amount
            if kind=='obligation':
                line(control(r,'AP_CONTROL' if r.direction=='payable' else 'AR_CONTROL'),'credit' if r.direction=='payable' else 'debit',r.original_amount)
                line(r.offset_ledger_account_id,'debit' if r.direction=='payable' else 'credit',r.original_amount)
            elif kind=='payment':
                outgoing=r.direction=='out';side='debit' if outgoing else 'credit'
                line(cash(r.money_account_id),'credit' if outgoing else 'debit',r.amount)
                if payment_totals[r.id]: line(control(r,'AP_CONTROL' if outgoing else 'AR_CONTROL'),side,payment_totals[r.id])
                if r.amount>payment_totals[r.id]: line(control(r,'SUPPLIER_ADVANCE' if outgoing else 'CUSTOMER_ADVANCE'),side,r.amount-payment_totals[r.id])
            elif kind=='advance_application':
                line(control(r,'AP_CONTROL' if r.direction=='paid' else 'CUSTOMER_ADVANCE'),'debit',r.amount)
                line(control(r,'SUPPLIER_ADVANCE' if r.direction=='paid' else 'AR_CONTROL'),'credit',r.amount)
            elif kind=='direct_settlement':
                line(control(r,'AP_CONTROL'),'debit',r.amount);line(control(r,'AR_CONTROL'),'credit',r.amount)
            elif kind=='owner_funding':
                line(cash(r.money_account_id),'debit',r.amount);line(control(r,'OWNER_FUNDING'),'credit',r.amount)
            else:
                line(cash(r.source_account_id),'credit',r.amount);line(cash(r.destination_account_id),'debit',r.amount)
                if r.fee: line(cash(r.source_account_id),'credit',r.fee);line(r.fee_account_id,'debit',r.fee)
            if canonical(expected)!=canonical(journals[p.id]): fail('domain_journal_effect_mismatch',r.id)
    for p in posts.values():
        records=domains.get(p.source_type,{})
        r=records.get(p.source_id)
        if not r or p.id!=(r.reversal_posting_id if p.reversal_of_id else r.posting_id): fail('orphan_posting_source',p.id)
    # Independent subledger vs control-account aggregation within entity/currency.
    expected_balances=defaultdict(Decimal)
    for o in obs.values():
        if o.status=='posted': expected_balances[(o.entity_id,o.currency,'AP_CONTROL' if o.direction=='payable' else 'AR_CONTROL')]+=o.original_amount-allocated[o.id]
    for a in advs.values():
        if a.status=='open': expected_balances[(a.entity_id,a.currency,'SUPPLIER_ADVANCE' if a.direction=='paid' else 'CUSTOMER_ADVANCE')]+=a.original_amount-applied_adv[a.id]
    actual=defaultdict(Decimal)
    for e in entries.values():
        l=ledgers.get(e.ledger_account_id)
        if l and l.system_code in ('AP_CONTROL','AR_CONTROL','SUPPLIER_ADVANCE','CUSTOMER_ADVANCE'):
            sign=1 if e.side==('credit' if l.system_code in ('AP_CONTROL','CUSTOMER_ADVANCE') else 'debit') else -1
            actual[(l.entity_id,l.currency,l.system_code)]+=sign*e.amount
    for key in sorted(set(expected_balances)|set(actual),key=str):
        if expected_balances[key]!=actual[key]: fail('control_balance_mismatch',':'.join(map(str,key)))
    return {'ok':not findings,'findings':sorted(findings,key=lambda f:(f['code'],f['id']))}
