"""Source account-to-account structure is independent of reference completeness."""
from datetime import date
import pytest
from greenhouse_owner_os.services.owner_preparation import prepare_owner_source

ACCOUNTS=[dict(id='cash',name='Cash TR',currency='TRY',account_type='cash'),dict(id='bank',name='Bank TR',currency='TRY',account_type='bank')]
TRANSFERS=[
    'Перевел 20000 TRY из Cash TR в Bank TR',
    'Перевела 20000 TRY из Bank TR в Cash TR',
    'Перечислил 20000 TRY с расчетного счета на другой счет',
    'Перевел 20000 TRY из кассы на банковский счет',
    'Перевел 20000 TRY со счета Резерв на счет Операционный',
]

@pytest.mark.parametrize('count',[0,1,2])
@pytest.mark.parametrize('raw',TRANSFERS)
def test_account_transfer_never_becomes_payment_when_references_are_incomplete(raw,count):
    slots=prepare_owner_source(raw,today=date(2026,10,9),refs={'accounts':ACCOUNTS[:count]})
    assert [kind for kind,_,_ in slots]==['UNRESOLVED']
    assert slots[0][1]=={'note':raw}

@pytest.mark.parametrize('count',[0,1,2])
@pytest.mark.parametrize('raw',[
    'Перевел Серкану 2500 TRY из Cash TR в счет старого долга',
    'Перевел Серкану 2500 TRY из Cash TR на его банковский счет',
    'Заплатил Серкану 2500 TRY из Cash TR',
])
def test_explicit_recipient_payment_retains_its_route(raw,count):
    refs={'accounts':ACCOUNTS[:count],'counterparties':[dict(id='serkan',name='Серкан')]}
    slots=prepare_owner_source(raw,today=date(2026,10,9),refs=refs)
    assert [kind for kind,_,_ in slots]==['PAYMENT']
    assert slots[0][1]['amount']=='2500' and slots[0][1]['counterparty_id']=='serkan'

@pytest.mark.parametrize('raw',[
    'Напомни перевести 20000 TRY из Cash TR в Bank TR',
    'Умар сказал: «Перевел 20000 TRY из Cash TR в Bank TR»',
    'Перевел 20000 TRY из Cash TR в Bank TR?',
    'Не перевел 20000 TRY из Cash TR в Bank TR',
])
def test_transfer_questions_reports_and_reminders_never_gain_financial_authority(raw):
    assert all(kind not in ('PAYMENT','PAYABLE','DIRECT_EXPENSE') for kind,_,_ in prepare_owner_source(raw,today=date(2026,10,9),refs={}))
