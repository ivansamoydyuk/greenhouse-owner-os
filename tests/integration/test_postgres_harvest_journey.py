"""Real PostgreSQL owner journey: source, authority, attribution, seasonal drilldown."""
import base64
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from tests.integration.test_postgres_readiness import preview,send
from tests.integration.test_postgres_harvest import prepare,receipt,draft,post,balances
from tests.integration.test_postgres_owner_capture import source,queue,revise,confirm,confirm_body
from greenhouse_owner_os.services.harvest_journey import HarvestJourneyService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.domain.harvest_journey import JourneyLinks,JourneyEstimate


@contextmanager
def guard_failure(message):
    with pytest.raises(DBAPIError) as error:
        yield
    assert error.value.orig.sqlstate=='23514',str(error.value)
    assert message in str(error.value.orig)

def farm(preview):
    c,e,cash,depot,areas=prepare(preview)
    return dict(c=c,e=e,preview=preview,depot=depot,areas=areas,cash=cash)
def field(i,count=131,day='2026-10-05'):
    r=send(i['c'],f"/entities/{i['e']}/harvest/reports",dict(event_date=day,original_greenhouse='İmamlı',cut_count=count,reporter='TEST owner',evidence=dict(source='test:synthetic',raw_text='TEST field original '+str(uuid4()))))
    b=send(i['c'],f"/entities/{i['e']}/harvest/batches",dict(event_date=day,label='TEST explicit load',report_ids=[r['id']],link_evidence='Owner witnessed loading'))
    return r,b

def prepared(i,batches=(),day='2026-10-06'):
    s=source(i,'TEST original document '+str(uuid4()),capture_context='depot_receipt')
    p=next(x for x in queue(i) if x['inbox_id']==s['id'])
    f=p['revision']['payload'];f.update(depot_id=i['depot'],event_date=day,currency='TRY',due_date='2026-10-15',document_number='TEST-'+str(uuid4()),batch_ids=list(batches),link_evidence='Owner confirmed independent source relationship' if batches else None,
        lines=[dict(original_quality_name='Grand',net_weight_kg='600',unit_price='50',count=60,source_line='1',allocations=[]),dict(original_quality_name='Grand',net_weight_kg='300',unit_price='50',count=33,source_line='2',allocations=[])],qualities=['HIGHEST','HIGHEST'],quality_reviewed=True,arithmetic_reviewed=True,stated_total='45000',alacak_value='55000')
    return s,revise(i,p,f)
def active(i,batches=()):
    s,p=prepared(i,batches);r=confirm(i,p);return s,p,r['effect']
def journey(i,sid):
    r=i['c'].get(f"/v1/entities/{i['e']}/harvest/settlements/{sid}/journey");assert r.status_code==200,r.text;return r.json()
def command(i,sid,kind,body,key=None,expected=200):return send(i['c'],f"/entities/{i['e']}/harvest/settlements/{sid}/{kind}",body,key=key,expected=expected)
def estimate_body(i,v=0,total='150'):
    return dict(expected_version=v,confirmed=True,reason='Owner reviewed approximate origins',basis_description='Approximate branch proportions confirmed separately from quality ADET',total_basis_count=total,basis=[dict(work_area_id=str(i['areas']['İmamlı']),basis_count='100'),dict(work_area_id=str(i['areas']['Özvadi']),basis_count='50')])
def monthly(i,metric='branches',gid=None,year=2026):
    r=i['c'].get(f"/v1/entities/{i['e']}/harvest/monthly",params=dict(season_start_year=year,metric=metric,**({'greenhouse_id':str(gid)} if gid else {})));assert r.status_code==200,r.text;return r.json()
def sql_counts(i):
    with i['preview'][0]() as db:
        return {t:db.scalar(text(f'SELECT count(*) FROM {t} WHERE entity_id=:e'),dict(e=i['e'])) for t in ('finance.obligations','finance.payments','finance.postings')}


def test_manual_depot_multiline_quality_explicit_cross_date_131_93_and_no_cash(preview,monkeypatch):
    i=farm(preview);report,batch=field(i)
    from greenhouse_owner_os.services.owner_capture import LocalOCR
    monkeypatch.setattr(LocalOCR,'recognize',lambda *args:(_ for _ in ()).throw(AssertionError('Manual document must not depend on OCR')))
    s,p=prepared(i,[batch['id']]);assert p['state']=='READY'
    arithmetic=p['revision']['basis']['arithmetic']
    assert arithmetic['field_count']==131 and arithmetic['depot_count']==93 and arithmetic['count_discrepancy']
    assert Decimal(arithmetic['total_amount'])==45000 and arithmetic['cash_effect']=='0'
    assert balances(i['c'],i['e'])['receivable']=='0.00'
    posted=confirm(i,p);r=posted['effect'];assert r['status']=='posted' and len(r['lines'])==2
    assert r['reconciliation']['field_count']==131 and r['reconciliation']['depot_count']==93
    assert all(x['normalized_quality']=='HIGHEST' and x['original_quality_name']=='Grand' for x in r['lines'])
    assert r['due_date']=='2026-10-15' and r['document_number']==p['revision']['payload']['document_number']
    assert confirm(i,p,key='lost-response-retry')==posted
    assert balances(i['c'],i['e'])['receivable']=='45000.00' and balances(i['c'],i['e'])['cash']=='0.00'
    with preview[0]() as db:
        assert db.scalar(text('SELECT count(*) FROM harvest.depot_quality_mappings WHERE depot_id=:d'),dict(d=i['depot']))==0
        assert db.scalar(text('SELECT raw_text FROM core.inbox_items WHERE id=:s'),dict(s=s['id']))==s['text']
    reread=i['c'].get(f"/v1/entities/{i['e']}/harvest/reports/{report['id']}").json();assert reread['cut_count']==131
    assert preview[3].reconcile(entity_id=UUID(i['e']))['ok']


def test_manual_pdf_original_survives_without_caption_or_ocr(preview,monkeypatch):
    i=farm(preview)
    from greenhouse_owner_os.services.owner_capture import LocalOCR
    monkeypatch.setattr(LocalOCR,'recognize',lambda *args:(_ for _ in ()).throw(AssertionError('OCR forbidden')))
    binary=b'%PDF-1.4\nTEST synthetic unparsed depot\n%%EOF'
    s=source(i,None,capture_context='depot_receipt',mode='file',filename='TEST.pdf',mime_type='application/pdf',file_base64=base64.b64encode(binary).decode())
    p=next(x for x in queue(i) if x['inbox_id']==s['id'])
    assert p['action_type']=='DEPOT_RECEIPT' and p['state']=='NEEDS_INPUT' and p['source']['text'] is None
    assert i['c'].get(f"/v1/entities/{i['e']}/owner/captures/{s['id']}/original").content==binary
    assert balances(i['c'],i['e'])['receivable']=='0.00'


def test_unknown_quality_and_arithmetic_need_explicit_review(preview):
    i=farm(preview);s,p=prepared(i)
    f=p['revision']['payload'];f.update(qualities=[None,'FIRST'],quality_reviewed=False,arithmetic_reviewed=False)
    p=revise(i,p,f);assert p['state']=='NEEDS_INPUT'
    confirm(i,p,expected=422)
    assert balances(i['c'],i['e'])['receivable']=='0.00'


def test_late_links_evidence_estimate_append_only_financial_effect_unchanged(preview):
    i=farm(preview);s,p,r=active(i);report,batch=field(i)
    before=sql_counts(i)
    with preview[0]() as db:
        original=db.execute(text('SELECT row_to_json(s)::text FROM harvest.depot_settlements s WHERE id=:s'),dict(s=r['id'])).scalar()
    linked=command(i,r['id'],'links',dict(expected_version=0,confirmed=True,batch_ids=[batch['id']],reason='Late independently confirmed truck relationship'))
    assert linked['version']==1 and linked['batch_ids']==[batch['id']]
    estimated=command(i,r['id'],'estimates',estimate_body(i,1))
    assert estimated['version']==2 and estimated['current_estimate']['classification']=='ESTIMATED'
    total_by_area={}
    for x in estimated['current_estimate']['lines']:
        total_by_area.setdefault(x['work_area_id'],[Decimal(0),Decimal(0)])
        total_by_area[x['work_area_id']][0]+=Decimal(x['net_weight_kg']);total_by_area[x['work_area_id']][1]+=Decimal(x['amount'])
    assert total_by_area[str(i['areas']['İmamlı'])]==[600,30000]
    assert total_by_area[str(i['areas']['Özvadi'])]==[300,15000]
    attached=command(i,r['id'],'attachments',dict(expected_version=2,confirmed=True,reason='Additional independent original',evidence=dict(source='test:synthetic',source_reference=str(uuid4()),raw_text='TEST additional depot note')))
    assert attached['version']==3 and attached['current_estimate']['id']==estimated['current_estimate']['id']
    with preview[0]() as db:
        assert db.execute(text('SELECT row_to_json(s)::text FROM harvest.depot_settlements s WHERE id=:s'),dict(s=r['id'])).scalar()==original
        assert db.scalar(text('SELECT count(*) FROM harvest.settlement_batches WHERE settlement_id=:s'),dict(s=r['id']))==0
    assert sql_counts(i)==before
    m=monthly(i,'kg',i['areas']['İmamlı']);assert Decimal(m['total']['value'])==600 and Decimal(m['total']['estimated_value'])==600
    assert estimated['current_estimate']['id'] in m['total']['source_ids']
    all_farm=monthly(i,'kg');assert Decimal(all_farm['total']['value'])==900 and Decimal(all_farm['total']['exact_value'])==900
    assert Decimal(all_farm['total']['estimated_value'])==0
    assert preview[3].reconcile(entity_id=UUID(i['e']))['ok']


def test_estimate_partial_unknown_stale_after_link_revision_and_no_silent_reuse(preview):
    i=farm(preview);s,p,r=active(i)
    b=estimate_body(i,total='300');x=command(i,r['id'],'estimates',b)
    assert Decimal(x['current_estimate']['unknown_basis_count'])==150
    assert sum(Decimal(z['net_weight_kg']) for z in x['current_estimate']['lines'] if z['work_area_id'] is None)==450
    report,batch=field(i)
    j=command(i,r['id'],'links',dict(expected_version=1,confirmed=True,batch_ids=[batch['id']],reason='New origin evidence'))
    assert j['current_estimate'] is None and j['estimates'][0]['stale']
    v=monthly(i,'kg',i['areas']['İmamlı']);assert v['total']['value'] is None and not v['total']['coverage']['complete']
    command(i,r['id'],'estimates',estimate_body(i,0),expected=409)
    assert journey(i,r['id'])['version']==2


def test_new_estimate_refuses_existing_exact_finance_attribution(preview):
    i=farm(preview);r=post(i['c'],i['e'],draft(i['c'],i['e'],receipt(i['depot'],i['areas'])))
    before=sql_counts(i)
    command(i,r['id'],'estimates',estimate_body(i),expected=422)
    assert journey(i,r['id'])['version']==0 and sql_counts(i)==before


@pytest.mark.parametrize('counts,expected,missing',[([],None,0),([None],None,1),([7,None],'7',1),([7],'7',0)])
def test_monthly_unknown_coverage_source_drilldown(preview,counts,expected,missing):
    i=farm(preview)
    for n in counts:field(i,n)
    m=monthly(i);assert m['total']['value']==expected and m['total']['coverage']['missing']==missing
    assert m['months'][0]['value']==expected and m['months'][1]['value'] is None
    assert m['filters']['season_start']=='2026-10-01' and m['filters']['season_end']=='2027-10-01'
    assert len(m['total']['events'])==len(counts)


def test_season_boundaries_and_more_than_100_events(preview):
    i=farm(preview)
    field(i,999,'2026-09-30');field(i,88,'2027-10-01');field(i,3,'2027-09-30')
    for _ in range(101):field(i,1,'2026-10-01')
    m=monthly(i);assert Decimal(m['total']['value'])==104 and len(m['total']['events'])==102
    assert m['months'][0]['value']=='101' and m['months'][-1]['value']=='3'
    assert monthly(i,year=2025)['total']['value']=='999'


def test_journey_concurrency_retry_and_cross_path_batch_ownership(preview):
    i=farm(preview);s,p,r=active(i);report,batch=field(i)
    svc=HarvestJourneyService(lambda:SqlAlchemyUnitOfWork(preview[0]));body=JourneyLinks(expected_version=0,confirmed=True,batch_ids=[batch['id']],reason='Concurrent owner confirmation')
    with ThreadPoolExecutor(2) as pool:
        result=list(pool.map(lambda _:svc.links(UUID(i['e']),preview[1],UUID(r['id']),'same-lost-response',body),range(2)))
    assert result[0]==result[1] and result[0]['version']==1
    other=receipt(i['depot'],i['areas'],batch_ids=[batch['id']],link_evidence='Must not steal late-linked load')
    send(i['c'],f"/entities/{i['e']}/harvest/settlements",other,expected=409)
    j=journey(i,r['id']);assert j['batch_ids']==[batch['id']]


def test_foreign_entity_and_immutable_revision_children_denied(preview):
    i=farm(preview);s,p,r=active(i);j=command(i,r['id'],'estimates',estimate_body(i))
    rid=j['current_estimate']['id']
    with guard_failure('append-only'),preview[0].begin() as db:db.execute(text('UPDATE harvest.journey_revisions SET reason=:r WHERE id=:id'),dict(r='rewrite',id=rid))
    with guard_failure('append-only'),preview[0].begin() as db:db.execute(text('DELETE FROM harvest.estimate_lines WHERE revision_id=:id'),dict(id=rid))
    with guard_failure('Completed revision children immutable'),preview[0].begin() as db:
        db.execute(text('INSERT INTO harvest.estimate_basis(revision_id,work_area_id,basis_count) VALUES(:r,:a,1)'),dict(r=rid,a=i['areas']['Yeşilöz']))
    other=send(i['c'],'/entities',dict(name='TEST other scope',currency='TRY',source='test:synthetic'))['id']
    assert i['c'].get(f"/v1/entities/{other}/harvest/settlements/{r['id']}/journey").status_code==404
    assert i['c'].get(f"/v1/entities/{other}/harvest/evidence/{r['evidence_id']}/metadata").status_code==404
    assert i['c'].get(f"/v1/entities/{i['e']}/harvest/monthly",params=dict(season_start_year=2026,greenhouse_id=str(uuid4()))).status_code==422


def test_estimate_preview_readonly_exact_and_original_field_attachment_rejected(preview):
    i=farm(preview);s,p,r=active(i);b=estimate_body(i);b.pop('confirmed')
    before=sql_counts(i)
    view=send(i['c'],f"/entities/{i['e']}/harvest/settlements/{r['id']}/estimates/preview",b,expected=200)
    assert Decimal(view['total_amount'])==45000 and sum(Decimal(x['net_weight_kg']) for x in view['lines'])==900
    assert journey(i,r['id'])['version']==0 and sql_counts(i)==before
    report,batch=field(i)
    command(i,r['id'],'attachments',dict(expected_version=0,confirmed=True,reason='Forbidden field substitution',evidence=dict(source='test:synthetic',existing_id=report['evidence_id'])),expected=422)
    assert journey(i,r['id'])['version']==0


def test_database_null_basis_and_conserved_but_wrong_allocation_rejected(preview):
    i=farm(preview);s,p,r=active(i)
    with guard_failure('check constraint'),preview[0].begin() as db:
        db.execute(text("INSERT INTO harvest.journey_revisions(id,entity_id,settlement_id,version,kind,reason,created_by) VALUES(:id,:e,:s,1,'ESTIMATE','TEST invalid NULL',:a)"),dict(id=uuid4(),e=i['e'],s=r['id'],a=preview[1]))
    with guard_failure('deterministic per-line largest remainder'),preview[0].begin() as db:
        rid=uuid4();db.execute(text("INSERT INTO harvest.journey_revisions(id,entity_id,settlement_id,version,kind,reason,created_by,total_basis_count,basis_description,algorithm) VALUES(:id,:e,:s,1,'ESTIMATE','TEST wrong conserved algorithm',:a,150,'100:50','largest-remainder-per-line-v1')"),dict(id=rid,e=i['e'],s=r['id'],a=preview[1]))
        for aid,count in ((i['areas']['İmamlı'],100),(i['areas']['Özvadi'],50)):
            db.execute(text('INSERT INTO harvest.estimate_basis(revision_id,work_area_id,basis_count) VALUES(:r,:a,:n)'),dict(r=rid,a=aid,n=count))
        for line in r['lines']:
            for aid,kg,amount in ((i['areas']['İmamlı'],Decimal(line['net_weight_kg'])-1,Decimal(line['amount'])-1),(i['areas']['Özvadi'],Decimal(1),Decimal(1))):
                db.execute(text('INSERT INTO harvest.estimate_lines(id,revision_id,line_id,work_area_id,net_weight_kg,amount) VALUES(:id,:r,:l,:w,:kg,:amount)'),dict(id=uuid4(),r=rid,l=line['id'],w=aid,kg=kg,amount=amount))
    assert journey(i,r['id'])['version']==0


def test_concurrent_late_link_against_legacy_creation_has_one_owner(preview):
    from threading import Barrier
    from greenhouse_owner_os.services.harvest import HarvestService
    from greenhouse_owner_os.domain.harvest import SettlementInput
    from greenhouse_owner_os.domain.errors import ConflictError,ValidationError
    i=farm(preview);s,p,r=active(i);report,batch=field(i);barrier=Barrier(2)
    factory=lambda:SqlAlchemyUnitOfWork(preview[0]);svc=HarvestJourneyService(factory)
    links=JourneyLinks(expected_version=0,confirmed=True,batch_ids=[batch['id']],reason='Concurrent new explicit link')
    body=SettlementInput(**receipt(i['depot'],i['areas'],batch_ids=[batch['id']],link_evidence='Concurrent independent proposed receipt'))
    def run(kind):
        barrier.wait()
        try:
            result=svc.links(UUID(i['e']),preview[1],UUID(r['id']),'late-race',links) if kind==0 else HarvestService(factory).settlement(UUID(i['e']),preview[1],'new-race',body)
            return 'ok',result['id']
        except (ConflictError,ValidationError):return 'conflict',None
        except DBAPIError as exc:
            assert exc.orig.sqlstate in ('23514','P0001'),str(exc)
            assert 'Batch already' in str(exc.orig) or 'batch already' in str(exc.orig)
            return 'conflict',None
    with ThreadPoolExecutor(2) as pool:result=list(pool.map(run,(0,1)))
    assert sorted(x[0] for x in result)==['conflict','ok']
    with preview[0]() as db:
        owners=list(db.scalars(text("SELECT s.id FROM harvest.depot_settlements s CROSS JOIN LATERAL harvest.current_batch_links(s.id) b WHERE b.batch_id=:b AND s.status<>'reversed'"),dict(b=batch['id'])))
        assert len(owners)==1
    assert balances(i['c'],i['e'])['receivable']=='45000.00' and balances(i['c'],i['e'])['cash']=='0.00'


def test_link_revision_releases_original_batch_without_rewriting_old_link(preview):
    i=farm(preview);report,batch=field(i);s,p,r=active(i,[batch['id']])
    command(i,r['id'],'links',dict(expected_version=0,confirmed=True,batch_ids=[],reason='Owner corrects mistaken association'))
    new=draft(i['c'],i['e'],receipt(i['depot'],i['areas'],batch_ids=[batch['id']],link_evidence='Correct explicit replacement relationship'))
    assert new['batch_ids']==[batch['id']]
    with preview[0]() as db:assert db.scalar(text('SELECT count(*) FROM harvest.settlement_batches WHERE settlement_id=:s AND batch_id=:b'),dict(s=r['id'],b=batch['id']))==1
    assert journey(i,r['id'])['batch_ids']==[]


def test_owner_and_entity_isolation_monthly_attachment_original(preview):
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.api.app import create_app
    from greenhouse_owner_os.services.references import provision_authenticated_owner
    i=farm(preview);s,p,r=active(i)
    outsider=provision_authenticated_owner(preview[0],external_ref='test:other:'+str(uuid4()),display_name='TEST other owner',environment='TEST_PREVIEW')
    c=TestClient(create_app(finance_service=preview[3],reference_service=preview[2],actor_provider=lambda:outsider))
    for suffix in ('/harvest/monthly?season_start_year=2026','/harvest/settlements/'+r['id']+'/journey','/harvest/evidence/'+r['evidence_id']+'/metadata','/owner/captures/'+s['id']+'/original'):
        assert c.get('/v1/entities/'+i['e']+suffix).status_code==403


def test_due_date_review_and_adapter_snapshot_tampering_rolls_back(preview,monkeypatch):
    from greenhouse_owner_os.services.harvest import HarvestService
    i=farm(preview);s,p=prepared(i)
    f=p['revision']['payload'];f['due_date']='2026-10-01';p=revise(i,p,f)
    assert p['state']=='NEEDS_INPUT'
    f['due_date']='2026-10-15';p=revise(i,p,f)
    original=HarvestService.settlement
    def tamper(self,e,a,key,body,**kw):
        return original(self,e,a,key,body.model_copy(update={'document_number':'TEST UNREVIEWED REPLACEMENT'}),**kw)
    monkeypatch.setattr(HarvestService,'settlement',tamper)
    confirm(i,p,expected=422)
    assert balances(i['c'],i['e'])['receivable']=='0.00'
    with preview[0]() as db:assert db.scalar(text('SELECT count(*) FROM harvest.depot_settlements WHERE entity_id=:e'),dict(e=i['e']))==0


def test_monthly_exact_estimated_filters_keep_one_source_population(preview):
    i=farm(preview);s,p,r=active(i);command(i,r['id'],'estimates',estimate_body(i))
    path=f"/v1/entities/{i['e']}/harvest/monthly"
    base=dict(season_start_year=2026,metric='revenue',greenhouse_id=str(i['areas']['İmamlı']),currency='TRY')
    estimated=i['c'].get(path,params=dict(base,allocation='estimated')).json()
    exact=i['c'].get(path,params=dict(base,allocation='exact')).json()
    assert Decimal(estimated['total']['value'])==30000 and Decimal(estimated['months'][0]['value'])==30000
    assert estimated['filters']['allocation']=='estimated' and exact['total']['value'] is None
    assert estimated['total']['events'][0]['id']==r['id']
    assert i['c'].get(path,params=dict(base,allocation='invented')).status_code==422
    farm_exact=i['c'].get(path,params=dict(season_start_year=2026,metric='revenue',allocation='exact')).json()
    assert Decimal(farm_exact['total']['value'])==45000


def test_estimate_cannot_freeze_mutable_draft_then_double_count_exact_children(preview):
    i=farm(preview);body=receipt(i['depot'],i['areas']);body['lines'][0]['allocations']=[]
    r=draft(i['c'],i['e'],body);b=estimate_body(i)
    command(i,r['id'],'estimates',b,expected=422)
    preview_body=dict(b);preview_body.pop('confirmed')
    send(i['c'],f"/entities/{i['e']}/harvest/settlements/{r['id']}/estimates/preview",preview_body,expected=422)
    assert journey(i,r['id'])['version']==0
    with guard_failure('Estimate requires immutable posted receipt lines'),preview[0].begin() as db:
        db.execute(text("INSERT INTO harvest.journey_revisions(id,entity_id,settlement_id,version,kind,reason,created_by,total_basis_count,basis_description,algorithm) VALUES(:id,:e,:s,1,'ESTIMATE','TEST draft is mutable',:a,150,'100:50','largest-remainder-per-line-v1')"),dict(id=uuid4(),e=i['e'],s=r['id'],a=preview[1]))


def test_append_failure_rolls_back_revision_children_and_evidence(preview,monkeypatch):
    from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyAuditRepository
    i=farm(preview);s,p,r=active(i);before=sql_counts(i)
    with preview[0]() as db:original_count=db.scalar(text('SELECT count(*) FROM harvest.evidence WHERE entity_id=:e'),dict(e=i['e']))
    def fail(*args,**kw):raise RuntimeError('TEST audit failure')
    monkeypatch.setattr(SqlAlchemyAuditRepository,'append',fail)
    with pytest.raises(RuntimeError):
        command(i,r['id'],'attachments',dict(expected_version=0,confirmed=True,reason='TEST rollback',evidence=dict(source='test:synthetic',raw_text='TEST uncommitted attachment '+str(uuid4()))))
    assert journey(i,r['id'])['version']==0 and sql_counts(i)==before
    with preview[0]() as db:assert db.scalar(text('SELECT count(*) FROM harvest.evidence WHERE entity_id=:e'),dict(e=i['e']))==original_count


def test_partial_basis_stays_incomplete_when_unknown_rounds_to_zero(preview):
    i=farm(preview);s,p=prepared(i)
    f=p['revision']['payload'];f.update(lines=[dict(original_quality_name='Grand',net_weight_kg='0.001',unit_price='10',source_line='1',allocations=[])],qualities=['HIGHEST'],stated_total='0.01')
    p=revise(i,p,f);r=confirm(i,p)['effect']
    b=dict(expected_version=0,confirmed=True,reason='Known incomplete origin',basis_description='999 known, 1 origin unknown',total_basis_count='1000',basis=[dict(work_area_id=str(i['areas']['İmamlı']),basis_count='999')])
    j=command(i,r['id'],'estimates',b)
    unknown=[x for x in j['current_estimate']['lines'] if x['work_area_id'] is None]
    assert len(unknown)==1 and Decimal(unknown[0]['net_weight_kg'])==0 and Decimal(unknown[0]['amount'])==0
    data=i['c'].get(f"/v1/entities/{i['e']}/harvest/monthly",params=dict(season_start_year=2026,metric='kg',allocation='estimated')).json()
    assert Decimal(data['total']['value'])==Decimal('.001') and Decimal(data['total']['unallocated_value'])==0
    assert not data['total']['coverage']['complete'] and Decimal(data['total']['events'][0]['unknown_basis_count'])==1


def test_monthly_currencies_sources_and_legacy_quality_are_not_reinterpreted(preview):
    i=farm(preview)
    send(i['c'],f"/entities/{i['e']}/reference-bootstrap",dict(currency='USD'),expected=200)
    try_doc=post(i['c'],i['e'],draft(i['c'],i['e'],receipt(i['depot'],i['areas'])))
    usd_body=receipt(i['depot'],i['areas']);usd_body['currency']='USD'
    usd_body['lines'][0].update(net_weight_kg='7',unit_price='10',count=None,allocations=[])
    usd_doc=post(i['c'],i['e'],draft(i['c'],i['e'],usd_body))
    path=f"/v1/entities/{i['e']}/harvest/monthly"
    for currency,expected,document in [('TRY',Decimal('30000'),try_doc),('USD',Decimal('70'),usd_doc)]:
        result=i['c'].get(path,params=dict(season_start_year=2026,metric='revenue',currency=currency)).json()
        assert Decimal(result['total']['value'])==expected and Decimal(result['months'][0]['value'])==expected
        assert [x['id'] for x in result['total']['events']]==[document['id']]
        assert result['total']['source_ids']==[document['evidence_id']]
        assert result['total']['events'][0]['qualities'][0]['quality']=='FIRST'
        assert result['total']['events'][0]['qualities'][0]['provenance']=='LEGACY_UNVERIFIED'
    kg=monthly(i,'kg');assert Decimal(kg['total']['value'])==1007
    assert set(kg['total']['source_ids'])=={try_doc['evidence_id'],usd_doc['evidence_id']}
    assert kg['filters']['currency'] is None and kg['filters']['unit']=='кг'
