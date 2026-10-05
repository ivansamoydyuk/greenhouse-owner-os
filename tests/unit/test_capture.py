from datetime import date
from types import SimpleNamespace as Ref
from uuid import uuid4
import pytest
from greenhouse_owner_os.services.capture import extract

CP=Ref(id=uuid4(),name='Серкан Ремонт насосов')
AHMET=Ref(id=uuid4(),name='Ахмет')
AREA=Ref(id=uuid4(),name='İmamlı')
CASH=Ref(id=uuid4(),name='Cash TR')

def parse(text):
    return extract(text,counterparties=[CP,AHMET],areas=[AREA],accounts=[CASH],today=date(2026,10,5))

def test_operational_repair_is_reviewable_not_posted_and_no_date_guess():
    route,f,gaps=parse('Серкану должен 8000 лир за ремонт насоса в Имамлы')
    assert route=='FINANCE'
    assert (f['amount'],f['currency'],f['obligation_class'])==('8000','TRY','OPERATIONAL')
    assert f['attributions']==[{'work_area_id':str(AREA.id),'amount':'8000'}]
    assert 'event_date' not in f and any('дату события' in g for g in gaps)

def test_strategic_received_principal_preserves_meaning_and_requires_account_date():
    route,f,gaps=parse('Взял у Ахмета 300000 долларов на запуск бизнеса, это стратегический долг')
    assert route=='FINANCE' and f['obligation_class']=='STRATEGIC' and f['principal_received']
    assert f['amount']=='300000' and f['currency']=='USD' and f['counterparty_id']==str(AHMET.id)
    assert 'event_date' not in f and 'money_account_id' not in f
    assert len(gaps)>=2

def test_depot_receivable_is_retained_for_settlement_not_duplicate_manual_sale():
    route,f,gaps=parse('Горгун депо должен мне 65000 лир, обещали заплатить 15 октября')
    assert route=='HARVEST_DEPOT' and f['direction']=='receivable'
    assert any('расчёта депо' in g for g in gaps)
    assert 'due_date' not in f and any('год' in g for g in gaps)

def test_customer_advance_is_finance_input_not_sale():
    route,f,gaps=parse('Получил от депо аванс 100000 лир под будущий урожай')
    assert route=='FINANCE' and f['kind']=='payment' and f['direction']=='in'
    assert f['allocations']==[] and 'event_date' not in f

def test_payment_extracts_explicit_today_and_cash_but_requires_allocation_review():
    _,f,gaps=parse('Заплатил Серкану 2600 лир сегодня наличными из Cash TR')
    assert f['event_date']=='2026-10-05' and f['money_account_id']==str(CASH.id)
    assert any('Выберите долг' in g for g in gaps)

@pytest.mark.parametrize('text',['Серкану должен за ремонт','Должен Серкану 8000','Серкану должен 8000 лир и 500 долларов','Ничего не произошло'])
def test_missing_ambiguous_and_unsupported_input_never_guesses(text):
    _,f,gaps=parse(text)
    assert gaps and 'event_date' not in f and 'amount' not in f

def test_explicit_due_year_and_vague_due_are_different():
    _,f,gaps=parse('Серкану должен 8000 лир сегодня заплачу 15 октября 2026')
    assert f['event_date']=='2026-10-05' and f['due_date']=='2026-10-15'
    _,f,gaps=parse('Серкану должен 8000 лир заплачу на следующей неделе')
    assert 'due_date' not in f and any('неоднозначно' in g for g in gaps)

def test_multiple_areas_require_explicit_split():
    route,f,gaps=extract('Серкану должен 8000 лир Имамлы Озвади',counterparties=[CP],areas=[AREA,Ref(id=uuid4(),name='Özvadi')])
    assert f['attributions']==[] and any('Несколько теплиц' in g for g in gaps)
