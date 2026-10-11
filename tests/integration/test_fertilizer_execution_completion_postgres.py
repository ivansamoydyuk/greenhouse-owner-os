"""Independent completion business assertions; real PostgreSQL, synthetic data.

Owner authority is per physical execution/application/part/component. No tank
resource is required and shared physical equipment never implies shared mixture.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from greenhouse_owner_os.domain.fertilizer import PlanInput,ActualInput,ActualConfirm,ResolutionInput
from greenhouse_owner_os.domain.errors import ConflictError,ValidationError
from greenhouse_owner_os.services.inventory import InventoryService
from greenhouse_owner_os.domain.inventory import InventoryReverse
from tests.integration.test_postgres_readiness import preview
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_fertilizer import fertilizer,save,dispatch,respond,current,source,STAMP,effects


def make_plan(f,separate=False):
    b=f['body'].model_dump(mode='json');application=str(uuid4())
    for c in b['cards']:
        c.update(application_id=application,application_order=1,mixing_intent='TOGETHER')
    if separate:
        c=copy.deepcopy(b['cards'][0]);c.update(card_id=str(uuid4()),application_id=str(uuid4()),application_order=2,mixing_intent='SEPARATE',mix_label='Внесение 2')
        c['components']=c['components'][:1];c['components'][0]['component_id']=str(uuid4());b['cards'].append(c)
    p=save(f,PlanInput(**b));dispatch(f,p);return current(f,p)


def owner_input(f,p,card_indices=(0,),quantities=None,physical=None,additional=False,zero=False,close=False):
    d=current(f,p);lines=[];physical=physical or uuid4()
    for index in card_indices:
        c=d['cards'][index]
        for n,ln in enumerate(c['components']):
            quantity='0' if zero else ((quantities or {}).get((index,n),ln['proposed_quantity']))
            lines.append(dict(card_id=c['card_id'],component_id=ln['component_id'],physical_execution_id=str(physical),quantity=quantity,disposition='NOT_APPLIED' if Decimal(quantity)==0 else 'APPLIED',reason='TEST explicit independent owner fact',remainder_decision='CLOSE' if close else 'KEEP_OPEN'))
    return ActualInput(source=source(),expected_version=d['version'],revision=d['revision'],event_date=date(2026,10,10),execution_kind='ADDITIONAL' if additional else 'FIRST',duplicate_reviewed=additional,lines=lines)


def post_actual(f,p,b,key=None):
    v=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),b)
    assert v['can_confirm'],v
    command=ActualConfirm(input=b,actual_preview_digest=v['actual_preview_digest'],confirmed=True)
    return f['svc'].actual(f['e'],f['a'],key or str(uuid4()),UUID(p['id']),command),command


def movements(f,p):
    with f['sf']() as db:
        return [dict(r) for r in db.execute(text('SELECT m.* FROM inventory.movements m JOIN inventory.fertilizer_actuals a ON a.application_id=m.event_id WHERE a.plan_id=:p ORDER BY to_jsonb(m)::text'),dict(p=p['id'])).mappings()]


def totals(f,p):
    result={}
    for m in movements(f,p):result[str(m['product_id'])]=result.get(str(m['product_id']),Decimal(0))+m['quantity']
    return result


def test_first_and_later_part_no_tank_retry_and_independent_money_harvest(fertilizer):
    f=fertilizer;before=effects(f);p=make_plan(f)
    for card in p['cards']:respond(f,p,card)
    assert effects(f)==before
    one,command=post_actual(f,p,owner_input(f,p),key='TEST first owner confirmation')
    after=copy.deepcopy(movements(f,p))
    # Response loss: exact request identity returns exact response, no new event.
    assert f['svc'].actual(f['e'],f['a'],'TEST first owner confirmation',UUID(p['id']),command)==one
    assert movements(f,p)==after
    assert totals(f,p)=={f['i']['p']['id']:Decimal('-10'),f['liquid']['id']:Decimal('-7.5')}
    post_actual(f,p,owner_input(f,p,(1,),additional=True))
    assert totals(f,p)=={f['i']['p']['id']:Decimal('-20'),f['liquid']['id']:Decimal('-15')}
    assert len(current(f,p)['actuals'])==2
    now=effects(f);assert now['finance.postings']==before['finance.postings'] and now['harvest.worker_reports']==before['harvest.worker_reports']
    assert all('tank' not in key for card in p['input']['cards'] for key in card)


def test_same_product_separate_applications_keep_distinct_physical_identities(fertilizer):
    f=fertilizer;p=make_plan(f,separate=True)
    assert [c['application_order'] for c in p['cards']]==[1,1,2]
    assert p['cards'][0]['work_area_id']==p['cards'][2]['work_area_id']
    assert p['cards'][0]['components'][0]['product_id']==p['cards'][2]['components'][0]['product_id']
    post_actual(f,p,owner_input(f,p,(0,)))
    d=current(f,p);assert len(d['actuals'])==1
    assert any(x['card_id']==p['cards'][2]['card_id'] for x in d['remainder'])
    post_actual(f,p,owner_input(f,p,(2,),additional=True))
    assert totals(f,p)=={f['i']['p']['id']:Decimal('-20'),f['liquid']['id']:Decimal('-7.5')}
    with f['sf']() as db:
        rows=db.execute(text('SELECT * FROM inventory.fertilizer_actual_lines WHERE plan_id=:p'),dict(p=p['id'])).mappings().all()
    assert len(rows)==3 and len({str(x['physical_execution_id']) for x in rows})==2


def test_partial_component_then_additional_quantity_not_cumulative(fertilizer):
    f=fertilizer;p=make_plan(f)
    b=owner_input(f,p,quantities={(0,0):'4'});b=b.model_copy(update={'lines':b.lines[:1]})
    post_actual(f,p,b)
    second=owner_input(f,p,quantities={(0,0):'6'},additional=True);second=second.model_copy(update={'lines':second.lines[:1]})
    post_actual(f,p,second)
    assert totals(f,p)=={f['i']['p']['id']:Decimal('-10')}
    d=current(f,p);assert len(d['actuals'])==2
    assert any(x['card_id']==p['cards'][1]['card_id'] for x in d['remainder'])


def test_explicit_zero_keeps_unknown_separate_and_has_no_stock_effect(fertilizer):
    f=fertilizer;p=make_plan(f);before=effects(f)
    result,_=post_actual(f,p,owner_input(f,p,zero=True))
    assert result['application_id'] is None and movements(f,p)==[] and effects(f)==before
    d=current(f,p);assert len(d['actuals'])==1 and d['actuals'][0]['input']['lines'][0]['quantity']=='0'
    assert any(x['card_id']==p['cards'][1]['card_id'] for x in d['remainder'])
    post_actual(f,p,owner_input(f,p,additional=True))
    assert totals(f,p)=={f['i']['p']['id']:Decimal('-10'),f['liquid']['id']:Decimal('-7.5')}


def test_concurrent_confirmation_changed_payload_and_stale_scope(fertilizer):
    f=fertilizer;p=make_plan(f);b=owner_input(f,p);view=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),b)
    cmd=ActualConfirm(input=b,actual_preview_digest=view['actual_preview_digest'],confirmed=True)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda n:f['svc'].actual(f['e'],f['a'],'TEST concurrency '+str(n),UUID(p['id']),cmd),range(2)))
    assert results[0]==results[1] and len(current(f,p)['actuals'])==1 and len(movements(f,p))==2
    changed=cmd.model_dump(mode='json');changed['input']['lines'][0]['quantity']='9'
    with pytest.raises(ConflictError):f['svc'].actual(f['e'],f['a'],'TEST concurrency 0',UUID(p['id']),ActualConfirm(**changed))
    stale=b.model_dump(mode='json');stale['source']=source();stale['execution_kind']='ADDITIONAL'
    for ln in stale['lines']:ln['physical_execution_id']=uuid4()
    with pytest.raises(ConflictError):f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),ActualInput(**stale))


def test_insufficient_one_component_rolls_back_whole_new_execution(fertilizer):
    f=fertilizer;p=make_plan(f);before=effects(f)
    b=owner_input(f,p,quantities={(0,0):'1',(0,1):'9999'})
    view=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),b)
    if view['can_confirm']:
        with pytest.raises((ValidationError,ConflictError)):
            f['svc'].actual(f['e'],f['a'],'TEST insufficient atomic',UUID(p['id']),ActualConfirm(input=b,actual_preview_digest=view['actual_preview_digest'],confirmed=True))
    else:assert view['issues']
    assert effects(f)==before and movements(f,p)==[] and not current(f,p)['actuals']


def test_new_key_new_form_cannot_repost_same_physical_execution(fertilizer):
    f=fertilizer;p=make_plan(f);execution=uuid4();b=owner_input(f,p,physical=execution);post_actual(f,p,b)
    d=current(f,p);repeat=owner_input(f,p,physical=execution,additional=True)
    view=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),repeat)
    assert not view['can_confirm'] and view['issues']
    assert len(current(f,p)['actuals'])==1 and len(movements(f,p))==2


def test_multiple_names_without_explicit_mixing_remain_reviewable(fertilizer):
    f=fertilizer;b=f['body'].model_dump(mode='json');b['source']['original_text']='KNO3 Multilex';application=str(uuid4())
    for c in b['cards']:c.update(application_id=application,application_order=1,mixing_intent='UNRESOLVED')
    p=save(f,PlanInput(**b));assert p['issues'] and not p['can_dispatch']
    assert p['source_snapshot']['original_text']=='KNO3 Multilex'
    assert movements(f,p)==[]


def test_closed_zero_requires_explicit_reopening_and_audit(fertilizer):
    f=fertilizer;p=make_plan(f);post_actual(f,p,owner_input(f,p,zero=True,close=True))
    blocked=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),owner_input(f,p,additional=True))
    assert not blocked['can_confirm'] and 'REOPEN_SCOPE_REQUIRED' in blocked['issues']
    for component in p['cards'][0]['components']:
        d=current(f,p)
        cmd=ResolutionInput(source=source(),expected_version=d['version'],revision=d['revision'],kind='REOPEN_SCOPE',card_id=p['cards'][0]['card_id'],component_id=component['component_id'],reason='TEST owner explicitly requests later completion',confirmed=True)
        f['svc'].resolve(f['e'],f['a'],str(uuid4()),UUID(p['id']),cmd)
    post_actual(f,p,owner_input(f,p,additional=True))
    d=current(f,p);assert len(d['resolutions'])==2 and len(d['actuals'])==2
    assert totals(f,p)=={f['i']['p']['id']:Decimal('-10'),f['liquid']['id']:Decimal('-7.5')}


def test_late_evidence_resolution_is_history_and_never_new_consumption(fertilizer):
    f=fertilizer;p=make_plan(f);post_actual(f,p,owner_input(f,p));before=movements(f,p)
    report=respond(f,p,p['cards'][0],'PROBLEM',comment='TEST late conflicting quantity, preserve original')
    d=current(f,p);assert any(x['id']==report['id'] for x in d['unresolved_reports'])
    cmd=ResolutionInput(source=source(),expected_version=d['version'],revision=d['revision'],kind='EVIDENCE_ONLY',report_id=report['id'],reason='TEST evidence describes already recorded execution, no new fact',confirmed=True)
    result=f['svc'].resolve(f['e'],f['a'],'TEST resolve same event',UUID(p['id']),cmd)
    assert f['svc'].resolve(f['e'],f['a'],'TEST resolve same event',UUID(p['id']),cmd)==result
    d=current(f,p);assert len(d['resolutions'])==1 and d['reports'][-1]['source_snapshot']['comment']=='TEST late conflicting quantity, preserve original'
    assert movements(f,p)==before


def test_audited_reversal_updates_current_total_without_rewriting_fact(fertilizer):
    f=fertilizer;p=make_plan(f);result,_=post_actual(f,p,owner_input(f,p));original=copy.deepcopy(current(f,p)['actuals'][0]['input'])
    InventoryService(f['svc'].factory).reverse(f['e'],f['a'],'TEST reverse historical actual',UUID(result['application_id']),InventoryReverse(event_date=date(2026,10,10),reason='TEST approved audited correction'))
    d=current(f,p);assert d['actuals'][0]['input']==original and d['actuals'][0]['inventory_status']=='reversed'
    applied=[x for x in d['execution_summary'] if x['card_id']==p['cards'][0]['card_id']]
    assert all(Decimal(x['confirmed_quantity'])==0 for x in applied)
    assert all(Decimal(x['remaining_quantity'])==Decimal(x['planned_quantity']) for x in applied)


def test_edit_cannot_disguise_consumed_scope_by_rotating_identifiers(fertilizer):
    f=fertilizer;p=make_plan(f);post_actual(f,p,owner_input(f,p));before=movements(f,p);d=current(f,p)
    payload=copy.deepcopy(d['input']);payload['source']=source()
    payload['cards'][0]['card_id']=str(uuid4())
    for component in payload['cards'][0]['components']:component['component_id']=str(uuid4())
    with pytest.raises((ValidationError,ConflictError)):
        f['svc'].save(f['e'],f['a'],'TEST disguised consumed scope',PlanInput(**payload),UUID(p['id']),d['version'])
    assert movements(f,p)==before and len(current(f,p)['revisions'])==1


def test_owner_four_parts_three_units_fixture_full_remainder(fertilizer):
    from greenhouse_owner_os.domain.fertilizer import ReferenceInput
    from tests.integration.test_postgres_readiness import send
    from tests.integration.test_postgres_inventory import event,post,line
    f=fertilizer;i=f['i'];payload=f['body'].model_dump(mode='json');app=str(uuid4())
    third=send(i['c'],i['root']+'/products',dict(name='TEST Atocrop arithmetic only',base_unit='kg',source='test:synthetic'))
    for product,unit in ((i['p']['id'],'kg'),(f['liquid']['id'],'L'),(third['id'],'kg')):
        post(i,event(i,lines=[dict(line(i,'500'),product_id=product,original_product_name='TEST calculation fixture',unit=unit)]))
    for n in range(2):
        part=uuid4()
        with f['sf'].begin() as db:db.execute(text("INSERT INTO core.work_areas(id,entity_id,parent_id,name,kind,source) VALUES(:p,:e,:g,:name,'work_area','test:explicit synthetic topology')"),dict(p=part,e=f['e'],g=i['g'],name='TEST arithmetic Part '+str(n+3)))
        ref=f['svc'].reference(f['e'],f['a'],str(uuid4()),ReferenceInput(client_reference_id=uuid4(),captured_at=STAMP,work_area_id=part,area_da='5',verification='TEST owner explicit5da',effective_from=date(2026,10,1),confirmed=True))
        card=copy.deepcopy(payload['cards'][0]);card.update(card_id=str(uuid4()),work_area_id=str(part),area_reference_id=ref['id']);payload['cards'].append(card)
    for card in payload['cards']:
        card.update(application_id=app,application_order=1,mixing_intent='TOGETHER')
        card['components']=[dict(component_id=str(uuid4()),product_id=product,location_id=i['l']['id'],dose_per_da=dose,unit=unit) for product,dose,unit in ((i['p']['id'],'10','kg'),(f['liquid']['id'],'5','L'),(third['id'],'2','kg'))]
    p=save(f,PlanInput(**payload));dispatch(f,p);p=current(f,p)
    assert {t['product_id']:Decimal(t['quantity']) for t in p['totals']}=={i['p']['id']:Decimal(200),f['liquid']['id']:Decimal(100),third['id']:Decimal(40)}
    for card in p['cards']:respond(f,p,card)
    assert not movements(f,p)
    post_actual(f,p,owner_input(f,p,(0,)))
    assert totals(f,p)=={i['p']['id']:Decimal(-50),f['liquid']['id']:Decimal(-25),third['id']:Decimal(-10)}
    unknown=[x for x in current(f,p)['execution_summary'] if x['card_id']!=p['cards'][0]['card_id']]
    assert all(x['unknown'] and x['confirmed_quantity'] is None for x in unknown)
    post_actual(f,p,owner_input(f,p,(1,),additional=True))
    assert totals(f,p)=={i['p']['id']:Decimal(-100),f['liquid']['id']:Decimal(-50),third['id']:Decimal(-20)}
    post_actual(f,p,owner_input(f,p,(2,3),additional=True))
    assert totals(f,p)=={i['p']['id']:Decimal(-200),f['liquid']['id']:Decimal(-100),third['id']:Decimal(-40)}
    assert len(current(f,p)['actuals'])==3 and not current(f,p)['remainder']


def test_latest_problem_blocks_older_done_even_with_current_plan_version(fertilizer):
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.api.app import create_app
    f=fertilizer;p=make_plan(f);old=respond(f,p,p['cards'][0],'DONE')
    latest=respond(f,p,p['cards'][0],'PROBLEM',comment='TEST subsequent evidence: nothing applied')
    d=current(f,p);c=d['cards'][0]
    body=ActualInput(source=source(),expected_version=d['version'],revision=d['revision'],event_date=date(2026,10,10),lines=[dict(card_id=c['card_id'],component_id=x['component_id'],report_id=old['id'],quantity=x['proposed_quantity'],disposition='APPLIED') for x in c['components']])
    with pytest.raises(ConflictError):f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),body)
    client=TestClient(create_app(finance_service=f['i']['preview'][3],actor_provider=lambda:f['a']))
    response=client.post(f"/v1/entities/{f['e']}/fertilizer/plans/{p['id']}/actual-preview",json=body.model_dump(mode='json'))
    assert response.status_code==409,response.text
    assert not movements(f,p)
    assert current(f,p)['cards'][0]['current_report']['id']==latest['id']


def test_database_rejects_superseded_report_even_if_service_is_bypassed(fertilizer):
    import json
    from sqlalchemy.exc import DBAPIError
    from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
    from greenhouse_owner_os.services.capture import BorrowedUow
    from greenhouse_owner_os.domain.inventory import InventoryInput,InventoryPost
    f=fertilizer;p=make_plan(f);old=respond(f,p,p['cards'][0],'DONE');latest=respond(f,p,p['cards'][0],'PROBLEM',comment='TEST later report must control review');d=current(f,p);c=d['cards'][0]
    good=ActualInput(source=source(),expected_version=d['version'],revision=d['revision'],event_date=date(2026,10,10),lines=[dict(card_id=c['card_id'],component_id=x['component_id'],report_id=latest['id'],quantity=x['proposed_quantity'],disposition='APPLIED',reason='TEST explicit owner review of current problem') for x in c['components']])
    preview=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),good);assert preview['can_confirm']
    bad=good.model_dump(mode='json')
    for ln in bad['lines']:ln.update(report_id=old['id'],reason=None)
    before=effects(f)
    with pytest.raises(DBAPIError) as exc:
        with SqlAlchemyUnitOfWork(f['sf']) as u:
            inventory=InventoryService(lambda:BorrowedUow(u));form=good.source.form_id
            event=inventory.create(f['e'],f['a'],'TEST bypass draft',InventoryInput(kind='application',event_date=good.event_date,lines=preview['inventory_effect']['lines'],evidence=dict(source='fertilizer:actual',locator=str(form),source_document='fertilizer-plan:'+p['id'],verification='VERIFIED')))
            inventory.post(f['e'],f['a'],'TEST bypass post',UUID(event['id']),InventoryPost(confirmed=True,expected_version=1))
            revision=u.session.scalar(text('SELECT id FROM inventory.fertilizer_revisions WHERE plan_id=:p ORDER BY revision DESC LIMIT 1'),dict(p=p['id']))
            u.session.execute(text('INSERT INTO inventory.fertilizer_actuals(id,entity_id,plan_id,revision_id,source_id,application_id,input,source_snapshot,preview_digest,created_by) VALUES(:id,:e,:p,:r,:s,:v,CAST(:b AS jsonb),CAST(:original AS jsonb),:d,:a)'),dict(id=uuid4(),e=f['e'],p=p['id'],r=revision,s=form,v=event['id'],b=json.dumps(bad),original=json.dumps(dict(input=bad,actual_preview_digest=preview['actual_preview_digest'],confirmed=True)),d=preview['actual_preview_digest'],a=f['a']))
            u.commit()
    assert exc.value.orig.sqlstate=='23514'
    assert effects(f)==before and not current(f,p)['actuals']
