from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from greenhouse_owner_os.domain.commands import CreateObligationCommand, CreatePaymentCommand
from greenhouse_owner_os.domain.finance import ObligationDirection, PaymentDirection
from greenhouse_owner_os.domain.history import ImportBatchCommand, HistoricalCandidateCommand
from greenhouse_owner_os.domain.errors import ValidationError, ConflictError

CUTOVER = date(2026, 10, 4)
OLD = date(2025, 9, 15)


def batch(fx, key=None, **changes):
    values = dict(entity_id=fx.entity_id, source='legacy-excel', source_file='archive.xlsx', fingerprint='a'*64)
    values.update(changes)
    return fx.service.create_import_batch(actor_id=fx.actor_id, idempotency_key=key or str(uuid4()), command=ImportBatchCommand(**values))


def candidate(fx, b, key=None, **changes):
    values = dict(source_row='Payments!12', event_date=OLD, raw_value={'amount':'1250.00', 'currency':'TRY', 'label':'Original debt'}, confidence=Decimal('.8'))
    values.update(changes)
    return fx.service.import_historical_candidate(actor_id=fx.actor_id, idempotency_key=key or str(uuid4()), batch_id=UUID(b['id']), command=HistoricalCandidateCommand(**values))


def draft(fx, kind='obligation', day=OLD):
    if kind == 'obligation':
        return fx.service.create_obligation(actor_id=fx.actor_id, idempotency_key=str(uuid4()), command=CreateObligationCommand(
            entity_id=fx.entity_id, counterparty_id=fx.counterparty_id, direction=ObligationDirection.PAYABLE,
            original_amount=Decimal('100'), currency='TRY', event_date=day, offset_ledger_account_id=fx.offset_account_id))
    return fx.service.create_payment(actor_id=fx.actor_id, idempotency_key=str(uuid4()), command=CreatePaymentCommand(
        entity_id=fx.entity_id, counterparty_id=fx.counterparty_id, money_account_id=fx.money_account_id,
        direction=PaymentDirection.OUT, amount=Decimal('100'), currency='TRY', event_date=day))


def post(fx, record, kind='obligation', key=None):
    return getattr(fx.service, 'post_'+kind)(actor_id=fx.actor_id, idempotency_key=key or str(uuid4()),
        expected_version=record['version'], **{kind+'_id':UUID(record['id'])})


def zero(fx):
    balances=fx.service.get_currency_balances(fx.entity_id)['balances']
    assert all(Decimal(x) == 0 for values in balances.values() for x in values.values())
    position=fx.service.get_counterparty_position(fx.entity_id,fx.counterparty_id,'TRY')
    assert all(position[k]=='0.00' for k in ('open_payable','open_receivable','supplier_advance_remaining','customer_advance_remaining'))


def zero_start_and_first_operation(fx):
    status=fx.service.get_cutover_status(fx.entity_id)
    assert status['cutover_date']=='2026-10-04' and status['opening_policy']=='ZERO'
    assert status['opening_postings_generated'] is False
    assert set(status['opening_balances'].values())=={'0.00'}
    zero(fx)
    b=batch(fx)
    for state in ('HISTORY', 'VERIFY', 'VERIFIED'):
        candidate(fx,b,source_row=state,verification_state=state)
    draft(fx)
    zero(fx)
    r=post(fx,draft(fx, day=CUTOVER))
    assert r['status']=='posted'
    balances=fx.service.get_currency_balances(fx.entity_id)['balances']['TRY']
    assert balances['payable']=='100.00'
    assert all(balances[k]=='0.00' for k in ('cash','receivable','advance_paid','advance_received'))
    # Cut-over API reports authoritative opening policy, never current balances.
    assert fx.service.get_cutover_status(fx.entity_id)==status


def old_drafts_rejected(fx):
    for kind in ('obligation', 'payment'):
        r=draft(fx,kind)
        assert r['event_date']==OLD.isoformat() and r['status']=='draft'
        for _ in range(2):
            with pytest.raises(ValidationError,match='pre-cut-over'):post(fx,r,kind,key='reject-'+kind)
    zero(fx)


def provenance_and_dedup(fx):
    b=batch(fx,key='b')
    assert batch(fx,key='b')==batch(fx)==b
    r=candidate(fx,b,key='c')
    assert candidate(fx,b,key='c')==candidate(fx,b)==r
    assert candidate(fx,b,confidence=Decimal('.800000'))==r
    assert r['event_date']=='2025-09-15' and r['verification_state']=='HISTORY'
    assert r['source']=='legacy-excel' and r['source_file']=='archive.xlsx' and r['source_row']=='Payments!12'
    assert r['import_batch_id']==b['id'] and r['posting_policy']=='NON_POSTING'
    assert datetime.fromisoformat(r['imported_at']).date() >= CUTOVER
    assert r['raw_value']['amount']=='1250.00' and r['created_by']==str(fx.actor_id)
    assert fx.service.get_historical_candidate(UUID(r['id']))==r
    assert fx.service.get_import_batch(UUID(b['id']))['candidates']==[r]
    with pytest.raises(ConflictError):candidate(fx,b,raw_value={'amount':'999'})
    with pytest.raises(ConflictError):candidate(fx,b,verification_state='VERIFIED')
    with pytest.raises(ConflictError):batch(fx,source_file='renamed.xlsx')
    zero(fx)


def history_dates_and_unknowns(fx):
    b=batch(fx)
    r=candidate(fx,b,source_row=None, source_document='receipt/9', event_date=None, raw_value=None, source_reference='archive://receipt/9', confidence=None, verification_state='VERIFY')
    assert r['event_date'] is None and r['raw_value'] is None and r['confidence'] is None
    assert r['source_document']=='receipt/9' and r['source_reference']=='archive://receipt/9'
    candidate(fx,b,source_row='new-date',event_date=CUTOVER,verification_state='VERIFIED')
    zero(fx)

CASES=[zero_start_and_first_operation, old_drafts_rejected, provenance_and_dedup, history_dates_and_unknowns]
