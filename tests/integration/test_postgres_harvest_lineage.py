"""Receipt creation stays idempotent across reversal and explicit correction chains."""
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID,uuid4
import pytest
from sqlalchemy import select,text,func
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.harvest import SettlementInput
from greenhouse_owner_os.infrastructure.harvest_models import SettlementRow
from greenhouse_owner_os.infrastructure.models import ObligationRow,PostingRow
from greenhouse_owner_os.services.harvest import HarvestService
from tests.integration.test_postgres_harvest import prepare,receipt,draft,post,reverse,balances
from tests.integration.test_postgres_readiness import preview,send


def reversed_receipt(preview):
    c,e,cash,depot,areas=prepare(preview)
    body=receipt(depot,areas);body['evidence']['source_reference']='receipt:original'
    original=post(c,e,draft(c,e,body));reverse(c,e,original)
    return c,e,depot,areas,body,original


def rows(preview,e,model):
    with preview[0]() as db:
        return db.scalar(select(func.count()).select_from(model).where(model.entity_id==UUID(e)))


def test_exact_stale_create_after_reversal_returns_reversed_no_new_effect(preview):
    c,e,depot,areas,body,original=reversed_receipt(preview)
    before=[rows(preview,e,m) for m in (SettlementRow,ObligationRow,PostingRow)]
    result=send(c,'/entities/'+e+'/harvest/settlements',body,key=str(uuid4()))
    assert result['id']==original['id'] and result['status']=='reversed'
    assert result['obligation_id']==original['obligation_id']
    assert [rows(preview,e,m) for m in (SettlementRow,ObligationRow,PostingRow)]==before
    assert balances(c,e)['cash']==balances(c,e)['receivable']=='0.00'


def test_changed_stale_request_requires_explicit_lineage(preview):
    c,e,depot,areas,body,original=reversed_receipt(preview)
    body['lines'][0]['unit_price']='31'
    send(c,'/entities/'+e+'/harvest/settlements',body,expected=409)
    assert rows(preview,e,SettlementRow)==rows(preview,e,ObligationRow)==1
    assert balances(c,e)['receivable']=='0.00'


def test_explicit_same_evidence_replacement_one_draft_and_retry(preview):
    c,e,depot,areas,body,original=reversed_receipt(preview)
    body['replacement_of_id']=original['id'];body['lines'][0]['unit_price']='31'
    child=draft(c,e,body);retry=draft(c,e,body)
    assert child['id']==retry['id'] and child['status']=='draft'
    assert child['replacement_of_id']==original['id']
    assert child['evidence_id']==original['evidence_id']
    assert rows(preview,e,SettlementRow)==2 and rows(preview,e,ObligationRow)==1


def sql_clone(db,original,**overrides):
    changes=dict(id=str(uuid4()),status='draft',version=1,obligation_id=None,payment_id=None,
                 application_ids=[],reviewed_issues=[],review_reason=None,posted_at=None,
                 reversed_at=None,reversal_reason=None,replacement_of_id=None)
    changes.update(overrides)
    db.execute(text('INSERT INTO harvest.depot_settlements SELECT (jsonb_populate_record(NULL::harvest.depot_settlements,to_jsonb(s)||CAST(:changes AS jsonb))).* FROM harvest.depot_settlements s WHERE id=:id'),
               dict(changes=json.dumps(changes),id=UUID(original['id'])))


def test_sql_unlinked_historical_evidence_is_rejected(preview):
    c,e,depot,areas,body,original=reversed_receipt(preview)
    with pytest.raises(DBAPIError),preview[0].begin() as db:sql_clone(db,original)
    assert rows(preview,e,SettlementRow)==1


@pytest.mark.parametrize('wrong',['evidence','entity','depot','currency'])
def test_sql_wrong_replacement_scope_rejected(preview,wrong):
    c,e,depot,areas,body,original=reversed_receipt(preview)
    second=post(c,e,draft(c,e,receipt(depot,areas)));reverse(c,e,second)
    e2=send(c,'/entities',dict(name='TEST Other Entity '+str(uuid4()),currency='TRY',source='test:synthetic'))['id']
    changes={'replacement_of_id':original['id']}
    if wrong=='evidence':changes['evidence_id']=second['evidence_id']
    elif wrong=='entity':changes.update(entity_id=e2,greenhouse_id=None)
    elif wrong=='depot':
        changes['depot_id']=send(c,'/entities/'+e+'/counterparties',dict(name='TEST Other Depot',role='depot',source='test:synthetic'))['id']
    else:changes['currency']='USD'
    with pytest.raises(DBAPIError) as rejected,preview[0].begin() as db:sql_clone(db,original,**changes)
    if wrong=='evidence':assert rejected.value.orig.sqlstate=='23514'
    assert rows(preview,e,SettlementRow)==2


def test_reversed_child_can_be_explicitly_replaced_complete_chain(preview):
    c,e,depot,areas,body,original=reversed_receipt(preview)
    body['replacement_of_id']=original['id'];child=post(c,e,draft(c,e,body));reverse(c,e,child)
    child_retry=draft(c,e,body)
    assert child_retry['id']==child['id'] and child_retry['status']=='reversed'
    body['replacement_of_id']=child['id'];grandchild=post(c,e,draft(c,e,body))
    assert grandchild['evidence_id']==child['evidence_id']==original['evidence_id']
    assert grandchild['replacement_of_id']==child['id']
    assert rows(preview,e,SettlementRow)==rows(preview,e,ObligationRow)==3
    assert balances(c,e)['receivable']=='30000.00'
    reverse(c,e,grandchild)
    assert balances(c,e)['receivable']=='0.00' and preview[3].reconcile(entity_id=UUID(e))['ok']


def test_concurrent_stale_retry_and_explicit_correction_no_unlinked_effect(preview):
    c,e,depot,areas,body,original=reversed_receipt(preview)
    service=HarvestService(preview[3].uow_factory)
    stale=SettlementInput(**body);corrected=SettlementInput(**dict(body,replacement_of_id=original['id']))
    with ThreadPoolExecutor(4) as pool:
        result=list(pool.map(lambda command:service.settlement(UUID(e),preview[1],str(uuid4()),command),[stale,corrected,stale,corrected]))
    assert all(r['id']==original['id'] and r['status']=='reversed' for r in (result[0],result[2]))
    assert result[1]['id']==result[3]['id'] and result[1]['replacement_of_id']==original['id']
    assert rows(preview,e,SettlementRow)==2 and rows(preview,e,ObligationRow)==1
    child=post(c,e,result[1]);assert balances(c,e)['receivable']=='30000.00'
    reverse(c,e,child);assert balances(c,e)['receivable']=='0.00'
