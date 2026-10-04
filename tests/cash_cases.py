"""Cash acceptance cases shared between sequential double and PostgreSQL."""
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from pydantic import ValidationError as InputError
from greenhouse_owner_os.domain.cash import TransferCommand, FundingCommand, SettlementCommand, SupplierAdvanceCommand
from greenhouse_owner_os.domain.commands import ReverseCommand
from greenhouse_owner_os.domain.errors import ValidationError, ConflictError, IdempotencyConflictError
from tests.finance_integrity_cases import DAY, obligation, payment


def create(fx, kind, command, key=None):
    return fx.service.create_cash(kind=kind, actor_id=fx.actor_id, idempotency_key=key or str(uuid4()), command=command)


def post(fx, kind, r, key=None):
    return fx.service.post_cash(kind=kind, resource_id=UUID(r['id']), actor_id=fx.actor_id,
        idempotency_key=key or str(uuid4()), expected_version=r['version'])


def reverse(fx, kind, r, key=None):
    return fx.service.reverse_cash(kind=kind, resource_id=UUID(r['id']), actor_id=fx.actor_id,
        idempotency_key=key or str(uuid4()), command=ReverseCommand(expected_version=r['version'], event_date=DAY, reason='Correct cash operation'))


def base(fx, amount): return dict(entity_id=fx.entity_id, amount=Decimal(amount), currency='TRY', event_date=DAY, source='owner-test', reason='cash acceptance')


def transfer(fx, amount='20000', fee='0'):
    return TransferCommand(**base(fx,amount), source_account_id=fx.money_account_id, destination_account_id=fx.destination_id,
        fee=Decimal(fee), fee_account_id=fx.offset_account_id if Decimal(fee) else None)


def funding(fx, amount='100000'): return FundingCommand(**base(fx,amount), money_account_id=fx.money_account_id)


def settlement(fx, amount='20000', receivable=None, payable=None):
    ar=receivable or obligation(fx,'30000','receivable',counterparty=fx.other_counterparty_id); ap=payable or obligation(fx,'20000','payable')
    return SettlementCommand(**base(fx,amount), receivable_id=UUID(ar['id']), payable_id=UUID(ap['id']))


def balance(fx, account):
    with fx.service.uow_factory() as uow:
        ledger=uow.finance.get_money_account_ledger(account,fx.entity_id,'TRY')
        if hasattr(fx,'store'):
            lines=[l for p in fx.store.postings.values() for l in p.lines if l.ledger_account_id==ledger]
            return sum((l.amount if l.side.value=='debit' else -l.amount for l in lines), Decimal('0'))
        from sqlalchemy import text
        return uow.session.scalar(text('SELECT balance FROM finance.money_account_balances WHERE money_account_id=:id'),{'id': account})


def ledger_effect(fx, account):
    with fx.service.uow_factory() as uow:
        if hasattr(fx,'store'):
            lines=[l for p in fx.store.postings.values() for l in p.lines if l.ledger_account_id==account]
            return sum((l.amount if l.side.value=='debit' else -l.amount for l in lines), Decimal('0'))
        from sqlalchemy import text
        return uow.session.scalar(text("SELECT coalesce(sum(CASE side WHEN 'debit' THEN amount ELSE -amount END),0) FROM finance.posting_entries WHERE ledger_account_id=:id"),{'id':account})


def transfer_and_fee(fx):
    before=ledger_effect(fx,fx.offset_account_id)
    draft=create(fx,'transfer',transfer(fx,fee='50'))
    assert balance(fx,fx.money_account_id)==balance(fx,fx.destination_id)==0
    r=post(fx,'transfer',draft)
    assert balance(fx,fx.money_account_id)==Decimal('-20050')
    assert balance(fx,fx.destination_id)==Decimal('20000')
    assert ledger_effect(fx,fx.offset_account_id)-before==Decimal('50')
    with fx.service.uow_factory() as u:
        p=u.finance.get_posting(UUID(r['posting_id']))
        assert sorted(l.amount for l in p.lines)==[Decimal('50'),Decimal('50'),Decimal('20000'),Decimal('20000')]
    rev=reverse(fx,'transfer',r,key='fee-reverse')
    assert reverse(fx,'transfer',r,key='fee-reverse')==rev
    assert balance(fx,fx.money_account_id)==balance(fx,fx.destination_id)==0
    assert ledger_effect(fx,fx.offset_account_id)==before
    with pytest.raises(ConflictError): reverse(fx,'transfer',r)


def transfer_no_pnl_retry(fx):
    cmd=transfer(fx)
    d=create(fx,'transfer',cmd,key='create-transfer');assert create(fx,'transfer',cmd,key='create-transfer')==d
    with pytest.raises(IdempotencyConflictError): create(fx,'transfer',transfer(fx,'1'),key='create-transfer')
    r=post(fx,'transfer',d,key='post-transfer');assert post(fx,'transfer',d,key='post-transfer')==r
    assert balance(fx,fx.money_account_id)==Decimal('-20000')
    assert balance(fx,fx.destination_id)==Decimal('20000')
    assert ledger_effect(fx,fx.offset_account_id)==0
    reverse(fx,'transfer',r)
    assert balance(fx,fx.money_account_id)==balance(fx,fx.destination_id)==0


def owner_funding(fx):
    cmd=funding(fx);d=create(fx,'owner_funding',cmd,key='fund')
    assert create(fx,'owner_funding',cmd,key='fund')==d
    r=post(fx,'owner_funding',d,key='post-fund');assert post(fx,'owner_funding',d,key='post-fund')==r
    assert balance(fx,fx.money_account_id)==Decimal('100000')
    assert ledger_effect(fx,fx.offset_account_id)==0
    assert ledger_effect(fx,fx.funding_ledger_id)==Decimal('-100000')
    rev=reverse(fx,'owner_funding',r,key='fund-rev');assert reverse(fx,'owner_funding',r,key='fund-rev')==rev
    assert balance(fx,fx.money_account_id)==0 and ledger_effect(fx,fx.funding_ledger_id)==0


def supplier_advance(fx):
    cmd=SupplierAdvanceCommand(**base(fx,'50000'),money_account_id=fx.money_account_id,counterparty_id=fx.counterparty_id)
    d=fx.service.create_supplier_advance(actor_id=fx.actor_id,idempotency_key='advance',command=cmd)
    assert fx.service.create_supplier_advance(actor_id=fx.actor_id,idempotency_key='advance',command=cmd)==d
    r=fx.service.post_supplier_advance(actor_id=fx.actor_id,idempotency_key='post-adv',payment_id=UUID(d['id']),expected_version=1)
    assert balance(fx,fx.money_account_id)==Decimal('-50000') and ledger_effect(fx,fx.offset_account_id)==0
    with fx.service.uow_factory() as u:
        a=u.finance.get_advance_by_payment(UUID(d['id']));p=u.finance.get_payment(UUID(d['id']))
        assert a.counterparty_id==fx.counterparty_id and a.original_amount==Decimal('50000') and a.direction.value=='paid'
        assert p.source=='owner-test' and p.reason=='cash acceptance'
    positions=fx.service.get_currency_balances(fx.entity_id)['balances']['TRY']
    assert positions['advance_paid']=='50000.00' and positions['payable']=='0.00'
    r2=fx.service.reverse_payment(actor_id=fx.actor_id,idempotency_key='rev-adv',payment_id=UUID(r['id']),command=ReverseCommand(expected_version=r['version'],event_date=DAY,reason='Incorrect advance'))
    assert r2['status']=='reversed' and balance(fx,fx.money_account_id)==0
    assert fx.service.get_currency_balances(fx.entity_id)['balances']['TRY']['advance_paid']=='0.00'


def direct_full(fx):
    cmd=settlement(fx)
    d=create(fx,'direct_settlement',cmd,key='direct');assert create(fx,'direct_settlement',cmd,key='direct')==d
    r=post(fx,'direct_settlement',d,key='post-direct');assert post(fx,'direct_settlement',d,key='post-direct')==r
    assert fx.service.get_obligation(cmd.receivable_id)['remaining_amount']=='10000.00'
    assert fx.service.get_obligation(cmd.payable_id)['remaining_amount']=='0.00'
    assert balance(fx,fx.money_account_id)==0
    positions=fx.service.get_currency_balances(fx.entity_id)['balances']['TRY']
    assert positions['receivable']=='10000.00' and positions['payable']=='0.00'
    with pytest.raises(ValidationError):
        fx.service.reverse_obligation(actor_id=fx.actor_id,idempotency_key=str(uuid4()),obligation_id=cmd.payable_id,command=ReverseCommand(expected_version=2,event_date=DAY,reason='Blocked'))
    rev=reverse(fx,'direct_settlement',r,key='reverse-direct');assert reverse(fx,'direct_settlement',r,key='reverse-direct')==rev
    assert fx.service.get_obligation(cmd.receivable_id)['remaining_amount']=='30000.00'
    assert fx.service.get_obligation(cmd.payable_id)['remaining_amount']=='20000.00'
    assert balance(fx,fx.money_account_id)==0


def direct_partial(fx):
    cmd=settlement(fx,'5000');r=post(fx,'direct_settlement',create(fx,'direct_settlement',cmd))
    assert fx.service.get_obligation(cmd.receivable_id)['remaining_amount']=='25000.00'
    assert fx.service.get_obligation(cmd.payable_id)['remaining_amount']=='15000.00'
    pay=payment(fx,[(str(cmd.payable_id),'15000')],'15000')
    assert fx.service.get_obligation(cmd.payable_id)['remaining_amount']=='0.00'
    reverse(fx,'direct_settlement',r)
    assert fx.service.get_obligation(cmd.payable_id)['remaining_amount']=='5000.00'


CASES=[transfer_no_pnl_retry,transfer_and_fee,owner_funding,supplier_advance,direct_full,direct_partial]
