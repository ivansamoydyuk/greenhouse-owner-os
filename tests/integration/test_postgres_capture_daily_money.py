"""Daily money clarification reuses one immutable source-linked action family."""
import base64
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.errors import ConflictError
from greenhouse_owner_os.domain.owner_capture import MoneyRecoveryAction
from tests.integration.test_postgres_readiness import preview, send
from tests.integration.test_postgres_inventory import inv, event, post, qty, view
from tests.integration.test_postgres_owner_capture import source, queue, revise, confirm
from tests.integration.test_postgres_owner_expense_recovery import service, counts, raw_target, raw_link, recover
from tests.integration.test_postgres_purchase_history import payable, read, finances
from tests.capture_daily_corpus import document


def party(i,name='Серкан'):
    return send(i['c'],f"/entities/{i['e']}/counterparties",dict(name=name,role='supplier',source='TEST synthetic daily money'))['id']


def handoff(i,s,p,intent='operating_payable',expected=200,**changes):
    return send(i['c'],f"/entities/{i['e']}/owner/captures/{s['id']}/money-recovery",
        dict(origin_proposal_id=p['id'],expected_version=p['version'],intent=intent,**changes),expected=expected)


@pytest.mark.parametrize('intent,kind',[('operating_payable','PAYABLE'),('paid_expense','DIRECT_EXPENSE'),('debt_payment','PAYMENT')])
@pytest.mark.parametrize('context',[None,'quick_expense'])
def test_three_explicit_intents_preserve_facts_and_one_effect_family(inv,intent,kind,context):
    cp=party(inv);old_debt=payable(inv,total='5000',party=cp) if intent=='debt_payment' else None
    s=source(inv,'Серкан, ремонт насоса, 5 000 TRY.',**({'capture_context':context} if context else {}));origin=queue(inv)[0]
    assert origin['action_type']=='UNRESOLVED' and origin['revision']['payload']['money_candidates']['amount']=='5000'
    p=handoff(inv,s,origin,intent);f=p['revision']['payload']
    assert p['action_type']==kind and f['amount']=='5000' and f['counterparty_id']==cp
    assert f['currency']=='TRY' and f['description']=='ремонт насоса'
    assert p['recovery']['target_action_type']==kind
    assert counts(inv)['core.confirmation_effects']==0
    assert handoff(inv,s,origin,intent)['id']==p['id']
    handoff(inv,s,origin,'paid_expense' if intent!='paid_expense' else 'operating_payable',expected=409)
    if kind!='DIRECT_EXPENSE':recover(inv,s,origin,expected=409)
    if kind in ('PAYMENT','DIRECT_EXPENSE'):
        assert p['state']=='NEEDS_INPUT'
        p=revise(inv,p,dict(p['revision']['payload'],money_account_id=inv['cash'],**({'obligation_id':old_debt['id']} if old_debt else {})))
    result=confirm(inv,p,key='first');assert confirm(inv,p,key='repeat')==result
    assert handoff(inv,s,origin,intent)['id']==p['id']
    retired=read(inv,'/owner/confirmations/'+origin['id']+'/preview')
    assert retired['state']=='REJECTED' and retired['revision']['payload']==origin['revision']['payload']
    c=counts(inv);assert c['core.owner_recovery_links']==c['core.confirmation_effects']==1
    assert c['finance.direct_expenses']==(kind=='DIRECT_EXPENSE') and c['finance.payments']==(kind=='PAYMENT')
    assert c['finance.obligations']==(kind in ('PAYMENT','PAYABLE'))
    assert inv['preview'][3].reconcile(entity_id=UUID(inv['e']))['ok']


def test_forged_candidates_and_edited_note_do_not_supply_recovery_authority(inv):
    party(inv);s=source(inv,'Завтра нужно заплатить Серкану 5 000 TRY за ремонт.');p=queue(inv)[0]
    assert p['action_type']=='UNRESOLVED'
    p=revise(inv,p,dict(note='Серкан, ремонт насоса, 5 000 TRY.',money_candidates={'amount':'5000','currency':'TRY'}))
    before=counts(inv);handoff(inv,s,p,expected=409);assert counts(inv)==before
    s=source(inv,'Серкан, ремонт насоса, 5 000 TRY.');p=next(p for p in queue(inv) if p['source']['id']==s['id'])
    p=revise(inv,p,dict(money_candidates={'amount':'999999','currency':'USD'}))
    target=handoff(inv,s,p)
    assert target['revision']['payload']['amount']=='5000' and target['revision']['payload']['currency']=='TRY'


def test_source_pair_sibling_and_retryable_job_fences(inv):
    party(inv);s=source(inv,'Серкан, ремонт насоса, 5 000 TRY.');p=queue(inv)[0]
    other=source(inv,'TEST another original');handoff(inv,other,p,expected=404)
    with inv['preview'][0].begin() as db:
        db.execute(text("UPDATE core.processing_jobs SET state='FAILED',attempts=2 WHERE inbox_id=:s"),dict(s=s['id']))
    handoff(inv,s,p,expected=409)
    with inv['preview'][0].begin() as db:
        db.execute(text("UPDATE core.processing_jobs SET state='SUCCEEDED' WHERE inbox_id=:s"),dict(s=s['id']))
        raw_target(db,inv,s,slot='UNRESOLVED:1',kind='UNRESOLVED')
    handoff(inv,s,p,expected=409)


def test_concurrent_same_and_competing_intents_reserve_one_target(inv):
    party(inv);s=source(inv,'Серкан, ремонт насоса, 5 000 TRY.');p=queue(inv)[0];svc,e,a=service(inv)
    def call(intent):
        try:return svc.recover_money(e,a,UUID(s['id']),str(uuid4()),MoneyRecoveryAction(origin_proposal_id=p['id'],expected_version=p['version'],intent=intent))
        except ConflictError:return None
    with ThreadPoolExecutor(3) as pool:results=list(pool.map(call,['operating_payable','operating_payable','paid_expense']))
    accepted=[r for r in results if r];assert accepted and len({r['id'] for r in accepted})==1
    assert counts(inv)['core.owner_recovery_links']==1 and counts(inv)['core.confirmation_effects']==0


@pytest.mark.parametrize('case',['orphan','wrong_slot','wrong_kind','legacy_slot_payable','null_origin'])
def test_money_recovery_sql_guards_fail_closed(inv,case):
    party(inv);s=source(inv,'Серкан, ремонт насоса, 5 000 TRY.');origin=queue(inv)[0]
    svc,e,a=service(inv);before=counts(inv)
    with pytest.raises(DBAPIError):
        with inv['preview'][0].begin() as db:
            pid=uuid4();kind='UNRESOLVED' if case=='wrong_kind' else 'PAYABLE'
            slot=('manual:direct-expense:' if case=='legacy_slot_payable' else 'manual:money:')+str(uuid4() if case=='wrong_slot' else origin['id'])
            db.execute(text("""INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by)
              VALUES(:id,:e,:s,:slot,:kind,'PREPARING',1,0,:a)"""),dict(id=pid,e=e,s=s['id'],slot=slot,kind=kind,a=a))
            u=type('Uow',(),{'session':db})();svc._revision(u,svc._proposal(u,e,pid),{'note':'TEST'} if kind=='UNRESOLVED' else {'currency':None},{},a,'PREPARE')
            if case!='orphan':raw_link(db,inv,s,None if case=='null_origin' else origin,pid)
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    assert counts(inv)==before


def test_real_invoice_intent_free_intake_requires_choice_and_preserves_stock(inv):
    cp=party(inv,'SYN Gübre Tedarik');party(inv,'SYN Another Supplier')
    receipt=post(inv,event(inv,supplier_id=cp));content,mime=document('P02')
    saved=source(inv,None,mode='file',mime_type=mime,filename='TEST P02.pdf',file_base64=base64.b64encode(content).decode(),capture_context='supplier_invoice',invoice_receipt_id=receipt['id'])
    ps={p['action_type']:p for p in queue(inv)};assert set(ps)=={'PAYABLE','INVOICE_MATCH'}
    p=ps['PAYABLE'];f=p['revision']['payload']
    assert f['amount']=='500.00' and f['counterparty_id']==cp and 'KNO3' in f['description']
    assert p['state']=='NEEDS_INPUT' and qty(inv)==100 and finances(inv)['payable']=='0.00'
    confirm(inv,p,expected=422)
    p=revise(inv,p,dict(f,invoice_intent='operating_expense',document_reviewed=True));result=confirm(inv,p)
    match=revise(inv,ps['INVOICE_MATCH'],dict(ps['INVOICE_MATCH']['revision']['payload'],finance_obligation_id=result['effect']['id']))
    linked=confirm(inv,match);assert confirm(inv,match,key='replay-link')==linked
    assert qty(inv)==100 and finances(inv)['payable']=='500.00' and finances(inv)['cash']=='0.00'
    detail=read(inv,'/purchases/'+receipt['id'])
    assert detail['invoice_links'][0]['valuation_state']=='UNKNOWN_UNCHANGED'
    assert detail['lines'][0]['unit_cost'] is None
    assert counts(inv)['finance.payments']==0 and counts(inv)['core.confirmation_effects']==2


@pytest.mark.parametrize('kind',['PAYABLE','PAYMENT'])
def test_exhausted_no_origin_cannot_use_new_money_type(inv,kind):
    from tests.integration.test_postgres_owner_expense_recovery import failed_source
    s=failed_source(inv);svc,e,a=service(inv)
    with inv['preview'][0]() as db:
        job=db.scalar(text('SELECT id FROM core.processing_jobs WHERE inbox_id=:s'),dict(s=s['id']))
    before=counts(inv)
    with pytest.raises(DBAPIError) as exc:
        with inv['preview'][0].begin() as db:
            pid=uuid4()
            db.execute(text("""INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by)
              VALUES(:id,:e,:s,:slot,:kind,'PREPARING',1,0,:a)"""),dict(id=pid,e=e,s=s['id'],slot='manual:money:'+s['id'],kind=kind,a=a))
            u=type('Uow',(),{'session':db})();svc._revision(u,svc._proposal(u,e,pid),{'currency':None},{},a,'PREPARE')
            raw_link(db,inv,s,None,pid,job=job)
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
    assert 'Recovery source/target/owner mismatch' in str(exc.value)
    assert counts(inv)==before
    assert recover(inv,s)['action_type']=='DIRECT_EXPENSE'


def test_generic_invoice_undecided_guard_survives_null_and_omission(inv):
    party(inv,'SYN Gübre Tedarik');party(inv,'SYN Another Supplier')
    raw='FATURA\nTarih:12.10.2026 Fatura No:SYN-P-001\nSatıcı: SYN Gübre Tedarik\nKNO3 100 kg\nTutar 500 TRY'
    s=source(inv,raw);p=next(p for p in queue(inv) if p['action_type']=='PAYABLE')
    assert p['revision']['payload']['invoice_intent']=='undecided' and p['state']=='NEEDS_INPUT'
    for removal in ('null','missing'):
        fields=dict(p['revision']['payload']);fields.pop('invoice_intent',None)
        if removal=='null':fields['invoice_intent']=None
        p=revise(inv,p,fields);assert p['state']=='NEEDS_INPUT';confirm(inv,p,expected=422)
    assert counts(inv)['core.confirmation_effects']==0
    p=revise(inv,p,dict(p['revision']['payload'],invoice_intent='operating_expense'))
    assert p['state']=='READY';confirm(inv,p);assert counts(inv)['finance.obligations']==1


@pytest.mark.parametrize('order',['effect_first','link_first'])
def test_money_origin_effect_and_link_are_rejected_in_both_orders(inv,order):
    party(inv);s=source(inv,'Серкан, ремонт насоса, 5 000 TRY.');origin=queue(inv)[0];svc,e,a=service(inv);before=counts(inv)
    expected='Selected origin is not eligible' if order=='effect_first' else 'Recovery origin cannot create an effect'
    with pytest.raises(DBAPIError,match=expected):
        with inv['preview'][0].begin() as db:
            pid=uuid4()
            db.execute(text("""INSERT INTO core.proposed_actions(id,entity_id,inbox_id,slot,action_type,state,version,priority,created_by)
              VALUES(:id,:e,:s,:slot,'PAYABLE','PREPARING',1,0,:a)"""),dict(id=pid,e=e,s=s['id'],slot='manual:money:'+origin['id'],a=a))
            u=type('Uow',(),{'session':db})();svc._revision(u,svc._proposal(u,e,pid),{'currency':None},{},a,'PREPARE')
            def effect():
                db.execute(text("""INSERT INTO core.confirmation_effects(proposal_id,entity_id,version,actor_id,preview_digest,response)
                  VALUES(:p,:e,:v,:a,:digest,'{}')"""),dict(p=origin['id'],e=e,v=origin['version'],a=a,digest=origin['revision']['preview_digest']))
            if order=='effect_first':effect()
            raw_link(db,inv,s,origin,pid)
            if order=='link_first':effect()
    assert counts(inv)==before


def test_existing_invoice_choice_remains_safe_after_payable_reopen(inv):
    cp=party(inv,'SYN Gübre Tedarik');receipt=post(inv,event(inv,supplier_id=cp));old=payable(inv,total='500',party=cp)
    raw='FATURA\n12.10.2026\nSatıcı: SYN Gübre Tedarik\nKNO3 100 kg\nTutar 500 TRY'
    s=source(inv,raw,capture_context='supplier_invoice',invoice_receipt_id=receipt['id']);ps={p['action_type']:p for p in queue(inv)}
    rejected=send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{ps['PAYABLE']['id']}/reject",dict(expected_version=ps['PAYABLE']['version']),expected=200)
    match=revise(inv,ps['INVOICE_MATCH'],dict(ps['INVOICE_MATCH']['revision']['payload'],finance_obligation_id=old['id']));confirm(inv,match)
    reopened=send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{rejected['id']}/reopen",dict(expected_version=rejected['version']),expected=200)
    attempted=revise(inv,reopened,dict(reopened['revision']['payload'],invoice_intent='operating_expense'))
    assert attempted['state']=='NEEDS_INPUT';confirm(inv,attempted,expected=422)
    assert counts(inv)['finance.obligations']==1 and counts(inv)['finance.payments']==0 and qty(inv)==100


def test_unlinked_rejected_invoice_can_reopen_for_explicit_operating_choice(inv):
    party(inv,'SYN Gübre Tedarik')
    s=source(inv,'FATURA\n12.10.2026\nSatıcı: SYN Gübre Tedarik\nKNO3 100 kg\nTutar 500 TRY')
    p=next(p for p in queue(inv) if p['action_type']=='PAYABLE')
    rejected=send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{p['id']}/reject",dict(expected_version=p['version']),expected=200)
    p=send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{p['id']}/reopen",dict(expected_version=rejected['version']),expected=200)
    p=revise(inv,p,dict(p['revision']['payload'],invoice_intent='operating_expense'))
    assert p['state']=='READY';confirm(inv,p);assert counts(inv)['finance.obligations']==1
