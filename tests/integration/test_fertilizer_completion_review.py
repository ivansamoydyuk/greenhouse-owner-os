"""Independent adversarial review regressions for completion (synthetic PostgreSQL)."""
import copy,json
from datetime import date
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from greenhouse_owner_os.domain.fertilizer import ActualInput,ResolutionInput
from greenhouse_owner_os.domain.errors import ValidationError
from greenhouse_owner_os.domain.inventory import InventoryInput,InventoryPost,InventoryReverse
from greenhouse_owner_os.services.inventory import InventoryService
from tests.integration.test_postgres_readiness import preview
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_fertilizer import fertilizer,current,source,respond,effects
from tests.integration.test_fertilizer_execution_completion_postgres import make_plan,owner_input,post_actual,movements


def resolve(f,p,report,kind,actual=None):
    d=current(f,p)
    return f['svc'].resolve(f['e'],f['a'],str(uuid4()),UUID(p['id']),ResolutionInput(source=source(),expected_version=d['version'],revision=d['revision'],kind=kind,report_id=report['id'],actual_id=actual and actual['id'],reason='TEST independently reviewed decision',confirmed=True))


def replacement(f,p,actual,post=False):
    svc=InventoryService(f['svc'].factory);original=svc.get(f['e'],UUID(actual['application_id']))
    svc.reverse(f['e'],f['a'],str(uuid4()),UUID(original['id']),InventoryReverse(event_date=date(2026,10,10),reason='TEST independent audited correction'))
    payload=copy.deepcopy(original['payload']);payload['lines'][0]['quantity']='3'
    child=svc.create(f['e'],f['a'],str(uuid4()),InventoryInput(kind='application',event_date=date(2026,10,10),evidence=original['evidence'],replacement_of_id=original['id'],**payload))
    if post:svc.post(f['e'],f['a'],str(uuid4()),UUID(child['id']),InventoryPost(confirmed=True,expected_version=1))
    return child


def test_later_conflict_reopens_report_attention_without_rewriting_decision(fertilizer):
    f=fertilizer;p=make_plan(f);actual,_=post_actual(f,p,owner_input(f,p));report=respond(f,p,p['cards'][0],'PROBLEM',comment='TEST late disagreement')
    before=movements(f,p);first=resolve(f,p,report,'EVIDENCE_ONLY',actual)
    assert report['id'] not in {r['id'] for r in current(f,p)['unresolved_reports']}
    second=resolve(f,p,report,'CONFLICT',actual);d=current(f,p)
    assert report['id'] in {r['id'] for r in d['unresolved_reports']}
    assert d['resolutions']==[first,second] and movements(f,p)==before


def test_pending_inventory_replacement_cannot_be_claimed_resolved(fertilizer):
    f=fertilizer;p=make_plan(f);actual,_=post_actual(f,p,owner_input(f,p));report=respond(f,p,p['cards'][0],'PROBLEM',comment='TEST needs actual correction')
    child=replacement(f,p,actual,post=False);d=current(f,p)
    assert d['actuals'][0]['current_inventory_status']=='draft'
    with pytest.raises(ValidationError):resolve(f,p,report,'CORRECTION',actual)
    assert current(f,p)['resolutions']==[]
    InventoryService(f['svc'].factory).post(f['e'],f['a'],str(uuid4()),UUID(child['id']),InventoryPost(confirmed=True,expected_version=1))
    resolve(f,p,report,'CORRECTION',actual)
    assert report['id'] not in {r['id'] for r in current(f,p)['unresolved_reports']}


def test_one_component_does_not_review_entire_worker_report(fertilizer):
    f=fertilizer;p=make_plan(f);report=respond(f,p,p['cards'][0],'DONE');d=current(f,p);c=d['cards'][0]
    inp=owner_input(f,p).model_dump(mode='json');inp['lines']=inp['lines'][:1]
    inp['lines'][0].update(report_id=report['id'],physical_execution_id=report['id'])
    post_actual(f,p,ActualInput(**inp))
    assert report['id'] in {r['id'] for r in current(f,p)['unresolved_reports']}
    second=owner_input(f,p,additional=True).model_dump(mode='json');second['lines']=second['lines'][1:]
    second['lines'][0].update(report_id=report['id'],physical_execution_id=report['id'])
    post_actual(f,p,ActualInput(**second))
    assert report['id'] not in {r['id'] for r in current(f,p)['unresolved_reports']}


def test_replacement_cannot_guess_same_product_separate_step_distribution(fertilizer):
    f=fertilizer;p=make_plan(f,separate=True);actual,_=post_actual(f,p,owner_input(f,p,(0,2)))
    old=copy.deepcopy(actual['input']);replacement(f,p,actual,post=True);d=current(f,p)
    scopes=[s for s in d['execution_summary'] if s['product_id']==f['i']['p']['id'] and s['card_id'] in (p['cards'][0]['card_id'],p['cards'][2]['card_id'])]
    assert len(scopes)==2 and all(s['correction_scope_unresolved'] and s['confirmed_quantity'] is None for s in scopes)
    assert d['actuals'][0]['input']==old
    check=f['svc'].actual_preview(f['e'],f['a'],UUID(p['id']),owner_input(f,p,(2,),additional=True))
    assert not check['can_confirm'] and 'CORRECTION_SCOPE_REQUIRES_REVIEW' in check['issues']


@pytest.mark.parametrize('defect',['date','execution_kind_null','remainder_null','physical_null'])
def test_raw_zero_invalid_source_fails_without_any_approval_or_stock(fertilizer,defect):
    f=fertilizer;p=make_plan(f);body=owner_input(f,p,zero=True);raw=body.model_dump(mode='json')
    if defect=='date':raw['event_date']='not-a-date'
    elif defect=='execution_kind_null':raw['execution_kind']=None
    elif defect=='remainder_null':raw['lines'][0]['remainder_decision']=None
    elif defect=='physical_null':raw['lines'][0]['physical_execution_id']=None
    before=effects(f)
    with pytest.raises(DBAPIError) as exc:
        with f['sf'].begin() as db:
            revision=db.scalar(text('SELECT id FROM inventory.fertilizer_revisions WHERE plan_id=:p ORDER BY revision DESC LIMIT 1'),dict(p=p['id']))
            db.execute(text('INSERT INTO inventory.fertilizer_actuals(id,entity_id,plan_id,revision_id,source_id,application_id,input,source_snapshot,preview_digest,created_by) VALUES(:id,:e,:p,:r,:s,NULL,CAST(:b AS jsonb),CAST(:original AS jsonb),:d,:a)'),dict(id=uuid4(),e=f['e'],p=p['id'],r=revision,s=body.source.form_id,b=json.dumps(raw),original=json.dumps(dict(input=raw,actual_preview_digest='a'*64,confirmed=True)),d='a'*64,a=f['a']))
    assert exc.value.orig.sqlstate in ('23514','22007','22008')
    assert effects(f)==before and current(f,p)['actuals']==[]


def test_normalized_execution_line_cannot_be_forged_for_existing_approval(fertilizer):
    f=fertilizer;p=make_plan(f);actual,_=post_actual(f,p,owner_input(f,p));before=movements(f,p)
    with f['sf']() as db:line=dict(db.execute(text('SELECT * FROM inventory.fertilizer_actual_lines WHERE actual_id=:a LIMIT 1'),dict(a=actual['id'])).mappings().one())
    with pytest.raises(DBAPIError) as exc:
        with f['sf'].begin() as db:
            db.execute(text('INSERT INTO inventory.fertilizer_actual_lines(id,actual_id,entity_id,plan_id,revision_id,physical_execution_id,card_id,component_id,report_id,product_id,location_id,work_area_id,unit,quantity,remainder_decision,inventory_line_index) VALUES(:id,:actual_id,:entity_id,:plan_id,:revision_id,:physical_execution_id,:card_id,:component_id,:report_id,:product_id,:location_id,:work_area_id,:unit,:quantity,:remainder_decision,:inventory_line_index)'),dict(line,id=uuid4(),physical_execution_id=uuid4()))
    assert exc.value.orig.sqlstate=='23514' and movements(f,p)==before


def test_resolution_cannot_link_report_to_unrelated_same_plan_actual(fertilizer):
    f=fertilizer;p=make_plan(f);actual,_=post_actual(f,p,owner_input(f,p,(0,)))
    report=respond(f,p,p['cards'][1],'PROBLEM',comment='TEST different part needs independent evidence')
    with pytest.raises(ValidationError):resolve(f,p,report,'EVIDENCE_ONLY',actual)
    d=current(f,p);body=ResolutionInput(source=source(),expected_version=d['version'],revision=d['revision'],kind='EVIDENCE_ONLY',report_id=report['id'],actual_id=actual['id'],reason='TEST invalid unrelated linkage',confirmed=True).model_dump(mode='json')
    with pytest.raises(DBAPIError) as exc:
        with f['sf'].begin() as db:
            db.execute(text("INSERT INTO inventory.fertilizer_resolutions(id,entity_id,plan_id,source_id,kind,report_id,actual_id,source_snapshot,created_by) VALUES(:i,:e,:p,:s,'EVIDENCE_ONLY',:r,:a,CAST(:body AS jsonb),:actor)"),dict(i=uuid4(),e=f['e'],p=p['id'],s=body['source']['form_id'],r=report['id'],a=actual['id'],body=json.dumps(body),actor=f['a']))
    assert exc.value.orig.sqlstate=='23514' and current(f,p)['resolutions']==[]


def test_explicit_application_order_controls_instructions_not_original_snapshot(fertilizer):
    from greenhouse_owner_os.domain.fertilizer import PlanInput
    from tests.integration.test_postgres_fertilizer import save,dispatch
    f=fertilizer;raw=f['body'].model_dump(mode='json')
    first=copy.deepcopy(raw['cards'][0]);second=copy.deepcopy(first)
    first.update(application_id=str(uuid4()),application_order=1,mixing_intent='TOGETHER')
    second.update(card_id=str(uuid4()),application_id=str(uuid4()),application_order=2,mixing_intent='TOGETHER')
    for component in second['components']:component['component_id']=str(uuid4())
    raw['cards']=[second,first];body=PlanInput(**raw);p=save(f,body)
    assert p['input']==body.model_dump(mode='json')
    assert [c['application_order'] for c in p['input']['cards']]==[2,1]
    assert [c['application_order'] for c in p['cards']]==[1,2]
    assert [c['card_id'] for c in p['cards']]==[first['card_id'],second['card_id']]
    dispatch(f,p)
    worker=f['svc'].get(f['e'],UUID(f['w']['actor_id']),UUID(p['id']),UUID(f['w']['id']))
    assert [c['application_order'] for c in worker['cards']]==[1,2]
    assert current(f,p)['input']==body.model_dump(mode='json') and not current(f,p)['actuals']


def test_raw_revision_cannot_store_derived_steps_against_explicit_order(fertilizer):
    f=fertilizer;p=make_plan(f,separate=True)
    with f['sf']() as db:old=dict(db.execute(text('SELECT * FROM inventory.fertilizer_revisions WHERE plan_id=:p'),dict(p=p['id'])).mappings().one())
    inp=copy.deepcopy(old['input']);preview=copy.deepcopy(old['preview']);pid,rid,sid=uuid4(),uuid4(),uuid4()
    inp['source']['form_id']=str(sid);preview['input']=inp;preview['cards']=list(reversed(preview['cards']))
    with pytest.raises(DBAPIError) as exc:
        with f['sf'].begin() as db:
            db.execute(text('INSERT INTO inventory.fertilizer_plans(id,entity_id,created_by) VALUES(:p,:e,:a)'),dict(p=pid,e=f['e'],a=f['a']))
            db.execute(text('INSERT INTO inventory.fertilizer_revisions(id,entity_id,plan_id,revision,source_id,input,preview,preview_digest,created_by) VALUES(:r,:e,:p,1,:s,CAST(:b AS jsonb),CAST(:v AS jsonb),:d,:a)'),dict(r=rid,e=f['e'],p=pid,s=sid,b=json.dumps(inp),v=json.dumps(preview),d=old['preview_digest'],a=f['a']))
    assert exc.value.orig.sqlstate=='23514'
    with f['sf']() as db:assert db.scalar(text('SELECT count(*) FROM inventory.fertilizer_plans WHERE id=:p'),dict(p=pid))==0
