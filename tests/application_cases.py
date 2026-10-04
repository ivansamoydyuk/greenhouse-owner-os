from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from greenhouse_owner_os.domain.cash import AdvanceApplicationCommand
from greenhouse_owner_os.domain.commands import ReverseCommand
from greenhouse_owner_os.domain.errors import ValidationError, ConflictError, IdempotencyConflictError
from tests.finance_integrity_cases import DAY, obligation, payment, reverse


def setup(fx,direction='paid',advance_amount='50000',ob_amount='30000'):
    pay=payment(fx,amount=advance_amount,direction='out' if direction=='paid' else 'in')
    ob=obligation(fx,ob_amount,direction='payable' if direction=='paid' else 'receivable')
    return pay,ob


def command(fx,pay,ob,amount='30000',advance_direction='paid',**changes):
    values=dict(entity_id=fx.entity_id,counterparty_id=fx.counterparty_id,advance_id=UUID(pay['advance_id']),
        obligation_id=UUID(ob['id']),amount=Decimal(amount),currency='TRY',direction=advance_direction,event_date=DAY,source='test-receipt',reason='apply prepayment')
    values.update(changes)
    return AdvanceApplicationCommand(**values)


def apply(fx,cmd,key=None):
    return fx.service.apply_advance(actor_id=fx.actor_id,idempotency_key=key or str(uuid4()),command=cmd)


def undo(fx,r,key=None,**changes):
    values=dict(expected_version=r['version'],event_date=DAY,reason='correct application');values.update(changes)
    return fx.service.reverse_cash(kind='advance_application',resource_id=UUID(r['id']),actor_id=fx.actor_id,
        idempotency_key=key or str(uuid4()),command=ReverseCommand(**values))


def remaining(fx,pay,ob):
    return (fx.service.get_advance(UUID(pay['advance_id']))['remaining_amount'],fx.service.get_obligation(UUID(ob['id']))['remaining_amount'])


def lifecycle(fx,direction):
    pay,ob=setup(fx,direction,'50000' if direction=='paid' else '20000','30000' if direction=='paid' else '15000')
    amount='30000' if direction=='paid' else '15000'
    before=fx.service.get_currency_balances(fx.entity_id)['balances']['TRY']['cash']
    with fx.service.uow_factory() as u: original_ob=u.finance.get_posting(UUID(ob['posting_id']))
    r=apply(fx,command(fx,pay,ob,amount,direction))
    assert remaining(fx,pay,ob)==('20000.00' if direction=='paid' else '5000.00','0.00')
    assert fx.service.get_currency_balances(fx.entity_id)['balances']['TRY']['cash']==before
    with fx.service.uow_factory() as u:
        assert u.finance.get_posting(UUID(ob['posting_id']))==original_ob
        journal=u.finance.get_posting(UUID(r['posting_id']))
        assert len(journal.lines)==2
    pos=fx.service.get_counterparty_position(fx.entity_id,fx.counterparty_id,'TRY')
    assert pos[('supplier' if direction=='paid' else 'customer')+'_advance_remaining']==remaining(fx,pay,ob)[0]
    with pytest.raises(ValidationError): reverse(fx,'payment',pay)
    with pytest.raises(ValidationError): reverse(fx,'obligation',ob)
    inv=undo(fx,r,key='undo')
    assert undo(fx,r,key='undo')==inv
    assert remaining(fx,pay,ob)==(f'{Decimal(pay["amount"]):.2f}',f'{Decimal(ob["original_amount"]):.2f}')
    assert fx.service.get_currency_balances(fx.entity_id)['balances']['TRY']['cash']==before
    with pytest.raises(ConflictError): undo(fx,r)
    assert fx.service.get_advance(UUID(pay['advance_id']))['applications'][0]['reversal_posting_id']==inv['reversal_posting_id']
    reverse(fx,'payment',pay);reverse(fx,'obligation',ob)


def many(fx,direction):
    pay,a=setup(fx,direction,'50000','30000')
    b=obligation(fx,'30000',direction='payable' if direction=='paid' else 'receivable')
    p2=payment(fx,amount='10000',direction='out' if direction=='paid' else 'in')
    x=apply(fx,command(fx,pay,a,'10000',direction))
    apply(fx,command(fx,pay,b,'30000',direction))
    apply(fx,command(fx,p2,a,'10000',direction))
    assert remaining(fx,pay,a)==('10000.00','10000.00')
    apply(fx,command(fx,pay,a,'10000',direction))
    assert remaining(fx,pay,a)==('0.00','0.00')
    undo(fx,x)
    assert remaining(fx,pay,a)==('10000.00','10000.00')


def rejects(fx,direction):
    pay,ob=setup(fx,direction,'100','100')
    for changes in [dict(counterparty_id=fx.other_counterparty_id),dict(entity_id=uuid4()),dict(currency='USD'),
                    dict(direction='received' if direction=='paid' else 'paid'),dict(event_date=DAY-timedelta(days=1))]:
        with pytest.raises(ValidationError):apply(fx,command(fx,pay,ob,'10',direction,**changes))
    with pytest.raises(ValidationError):apply(fx,command(fx,pay,ob,'101',direction))
    assert remaining(fx,pay,ob)==('100.00','100.00')
    r=apply(fx,command(fx,pay,ob,'60',direction),key='same')
    assert r==apply(fx,command(fx,pay,ob,'60.00',direction),key='same')
    with pytest.raises(IdempotencyConflictError):apply(fx,command(fx,pay,ob,'30',direction),key='same')
    with pytest.raises(ValidationError):apply(fx,command(fx,pay,ob,'50',direction))
    with pytest.raises(ConflictError):undo(fx,r,expected_version=1)
    with pytest.raises(ValidationError):undo(fx,r,event_date=DAY-timedelta(days=1))
    assert remaining(fx,pay,ob)==('40.00','40.00')
    undo(fx,r);reverse(fx,'payment',pay)
    with pytest.raises(ValidationError):apply(fx,command(fx,pay,ob,'10',direction))

CASES=[lifecycle,many,rejects]
