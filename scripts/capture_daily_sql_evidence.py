"""Guarded TEST fixture preparation and independent exact PostgreSQL attestation.

The only write mode is --prepare: an exact, synthetic depot reference through
existing authenticated owner APIs. All observations/verification are READ ONLY.
No browser, OCR call, capture/proposal injection, ledger repair or production use.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import secrets
import traceback
from decimal import Decimal
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.services.reconciliation import reconcile

PARSER=argparse.ArgumentParser()
PARSER.add_argument('--prepare',action='store_true')
PARSER.add_argument('--credentials')
PARSER.add_argument('--before')
PARSER.add_argument('--probe',action='store_true')
PARSER.add_argument('--source')
PARSER.add_argument('--proposal')
PARSER.add_argument('--verify')
PARSER.add_argument('--baseline')
PARSER.add_argument('--output')
ARGS=PARSER.parse_args()
assert sum(bool(x) for x in (ARGS.prepare,ARGS.before,ARGS.probe,ARGS.verify))==1
URL=os.environ['TEST_DATABASE_URL']
require_disposable_database(URL,'test')
assert os.environ.get('APP_ENV')=='test'
assert os.environ.get('R3_DAILY_PILOT')=='1' and os.environ.get('PILOT_LOCAL_TEST')=='1'
SHA=os.environ['OWNER_PREVIEW_FEATURE_SHA']
SF=make_session_factory(URL)
BENCHMARK_PATH=Path('tests/fixtures/capture_daily_benchmark.json')
BENCHMARK=json.loads(BENCHMARK_PATH.read_text())
UNCHANGED=('finance.payments','finance.direct_expenses','finance.advances','finance.owner_fundings','finance.advance_applications','finance.direct_settlements','finance.own_account_transfers','inventory.events','inventory.movements','inventory.stock','inventory.products','core.invoice_receipt_links','harvest.depot_quality_mappings')
ADDITIONS=('finance.obligations','finance.postings','finance.posting_entries','harvest.worker_reports','harvest.depot_settlements')

def normalized(value):
    return json.loads(json.dumps(value,sort_keys=True,default=str))

def write(filename,value):
    path=Path(filename);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(normalized(value),ensure_ascii=False,indent=2)+'\n')

def read_only(db):
    db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
    _require_connected_runtime(db)
    entities=db.execute(text('SELECT id FROM core.entities')).scalars().all()
    assert len(entities)==1
    return entities[0]

def rows(db,sql,**params):
    return [dict(r) for r in db.execute(text(sql),params).mappings()]

def sorted_rows(items):
    return sorted(normalized(items),key=lambda row:json.dumps(row,sort_keys=True))

def snapshot(db,entity):
    values={table:sorted_rows(rows(db,'SELECT * FROM '+table+' WHERE entity_id=:e',e=entity)) for table in UNCHANGED+ADDITIONS if table!='finance.posting_entries'}
    values['finance.posting_entries']=sorted_rows(rows(db,'SELECT en.* FROM finance.posting_entries en JOIN finance.postings p ON p.id=en.posting_id WHERE p.entity_id=:e',e=entity))
    values['cash']=sorted_rows(rows(db,'SELECT b.* FROM finance.money_account_balances b JOIN finance.money_accounts a ON a.id=b.money_account_id WHERE a.entity_id=:e',e=entity))
    return dict(sha=SHA,entity_id=str(entity),tables=values)

def probe(db,entity,source_id,proposal_id):
    s=rows(db,'SELECT * FROM core.inbox_items WHERE entity_id=:e AND id=:s',e=entity,s=source_id)
    assert len(s)==1
    source=s[0]
    actions=rows(db,'SELECT * FROM core.proposed_actions WHERE entity_id=:e AND id=:p',e=entity,p=proposal_id)
    assert len(actions)==1 and actions[0]['inbox_id']==source_id
    effects=rows(db,'SELECT * FROM core.confirmation_effects WHERE entity_id=:e AND proposal_id=:p',e=entity,p=proposal_id)
    settlements=rows(db,'SELECT s.* FROM harvest.depot_settlements s JOIN harvest.evidence ev ON ev.id=s.evidence_id WHERE s.entity_id=:e AND ev.source_reference=:p',e=entity,p=str(proposal_id))
    obligations=rows(db,'SELECT o.* FROM finance.obligations o JOIN harvest.depot_settlements s ON s.obligation_id=o.id JOIN harvest.evidence ev ON ev.id=s.evidence_id WHERE s.entity_id=:e AND ev.source_reference=:p',e=entity,p=str(proposal_id))
    postings=rows(db,'SELECT p.* FROM finance.postings p JOIN finance.obligations o ON o.id=p.source_id JOIN harvest.depot_settlements s ON s.obligation_id=o.id JOIN harvest.evidence ev ON ev.id=s.evidence_id WHERE s.entity_id=:e AND ev.source_reference=:proposal',e=entity,proposal=str(proposal_id))
    return normalized(dict(sha=SHA,entity_id=str(entity),source_id=str(source_id),proposal_id=str(proposal_id),source_sha256=hashlib.sha256(source['file_bytes']).hexdigest(),source_caption=source['raw_text'],effects=effects,settlements=settlements,obligations=obligations,postings=postings,confirm_actions=db.scalar(text("SELECT count(*) FROM core.proposal_actions WHERE entity_id=:e AND proposal_id=:p AND action='CONFIRM'"),dict(e=entity,p=proposal_id))))

if ARGS.prepare:
    from fastapi.testclient import TestClient
    from greenhouse_owner_os.preview import build_preview
    private=Path(ARGS.credentials).resolve()
    assert private.is_file() and 'artifacts' not in private.parts
    assert not private.is_relative_to(Path.cwd().resolve()),'Credentials must remain outside checkout/evidence'
    credentials=json.loads(private.read_text())['owner']
    app=build_preview(URL,os.environ.get('PREVIEW_SECRET') or secrets.token_urlsafe(32),environment='test')
    title=next(c for c in BENCHMARK['cases'] if c['id']=='H01')['input']['document']['title']
    with SF() as db:entity=read_only(db)
    with TestClient(app) as client:
        headers={'Origin':'http://testserver'}
        response=client.post('/pilot/login',json={k:credentials[k] for k in ('username','password')},headers=headers)
        assert response.status_code==200,'Private TEST owner authentication failed'
        headers['X-Pilot-Participant']=credentials['participant_id']
        response=client.post('/v1/entities/'+str(entity)+'/counterparties',json=dict(name=title,role='depot',source='test:capture-daily:frozen-corpus'),headers=headers|{'Idempotency-Key':'test:capture-daily:depot-reference:v1'})
        assert response.status_code in (200,201),response.text
        depot=response.json()
    write(ARGS.output,dict(sha=SHA,entity_id=str(entity),depot_id=depot['id'],depot_name=title,synthetic_only=True,source='frozen H01/H02 document title',financial_effects_created=False))
    print('CAPTURE_DAILY_REFERENCE_READY')
    raise SystemExit

if ARGS.before:
    with SF() as db:
        entity=read_only(db);before=snapshot(db,entity)
    write(ARGS.before,before);print('CAPTURE_DAILY_BEFORE_READY');raise SystemExit

if ARGS.probe:
    with SF() as db:
        entity=read_only(db);observed=probe(db,entity,UUID(ARGS.source),UUID(ARGS.proposal))
    write(ARGS.output,observed);print('CAPTURE_DAILY_READ_ONLY_PROBE_READY');raise SystemExit

result=json.loads(Path(ARGS.verify).read_text())
before=json.loads(Path(ARGS.baseline).read_text())
assert result['sha']==SHA==before['sha']
assert result['benchmark_sha256']==hashlib.sha256(BENCHMARK_PATH.read_bytes()).hexdigest()
mode=result['mode'];expected_status='BASELINE_RECORDED' if mode=='baseline' else 'PASS'
assert result['status']==expected_status and not result['errors']
records=result['records'];assert records and len(records)<=2
assert len({r['engine'] for r in records})==len(records)
expected_engine=os.environ.get('CAPTURE_DAILY_ENGINE')
if expected_engine:assert {r['engine'] for r in records}=={expected_engine}
proof=dict(sha=SHA,mode=mode,status='RUNNING',cases=[],financial_effects='one sale and AR per explicitly confirmed independent browser scenario; no payment, stock or cash effect')
try:
 with SF() as db:
    entity=read_only(db)
    assert str(entity)==before['entity_id']
    settlement_ids=set();obligation_ids=set();posting_ids=set();report_ids=set()
    for record in records:
        assert record['status']==expected_status and record['entity_id']==str(entity)
        pid=UUID(record['proposal_id']);sid=UUID(record['source_id']);settlement_id=UUID(record['settlement_id'])
        case=next(c for c in BENCHMARK['cases'] if c['id']==record['case_id']);doc=case['input']['document']
        file_bytes=Path(record['asset_path']).read_bytes()
        assert hashlib.sha256(file_bytes).hexdigest()==record['source_sha256']==record['clicked_original_sha256']
        assert record['original_route_preserved'] and record['lost_response']
        current_probe=probe(db,entity,sid,pid)
        assert current_probe==record['probes']['response_lost']==record['probes']['after_retry'],'Lost response retry altered committed effect'
        assert not record['probes']['before_confirmation']['effects'] and not record['probes']['ready_to_confirm']['effects']
        assert len(current_probe['effects'])==len(current_probe['settlements'])==len(current_probe['obligations'])==len(current_probe['postings'])==1
        assert current_probe['confirm_actions']==1
        source=rows(db,'SELECT * FROM core.inbox_items WHERE id=:s AND entity_id=:e',s=sid,e=entity)[0]
        assert source['file_bytes']==file_bytes and source['raw_text'] is None
        assert source['metadata']['capture_context']=='depot_receipt'
        proposal=rows(db,'SELECT * FROM core.proposed_actions WHERE id=:p AND entity_id=:e',p=pid,e=entity)[0]
        assert proposal['inbox_id']==sid and proposal['state']=='POSTED'
        initial=rows(db,'SELECT * FROM core.proposal_revisions WHERE proposal_id=:p ORDER BY version LIMIT 1',p=pid)[0]
        final=rows(db,'SELECT * FROM core.proposal_revisions WHERE proposal_id=:p ORDER BY version DESC LIMIT 1',p=pid)[0]
        assert initial['extraction_run_id'] is not None
        runs=rows(db,'SELECT * FROM core.extraction_runs WHERE inbox_id=:s AND entity_id=:e ORDER BY created_at,attempt',s=sid,e=entity)
        assert runs and all(r['input_hash']==source['fingerprint'] for r in runs)
        extraction=next(r for r in runs if r['id']==initial['extraction_run_id']);raw=extraction['raw_output']
        if mode=='candidate':
            assert extraction['provider']=='local' and raw['version']=='local-tesseract-tur-eng-v1'
            assert 'tesseract' in raw['engine'].lower() and raw['text'].strip()
            assert len(raw['pages'])==(2 if record['case_id']=='H02' else 1)
            assert all(p['raw_tsv'].strip() and p['lines'] for p in raw['pages'])
            assert initial['payload']['quality_reviewed'] is False and initial['payload']['arithmetic_reviewed'] is False
            assert initial['payload']['batch_ids']==[] and initial['payload']['document_count']==93
            assert len(initial['payload']['lines'])==3
            assert record['metrics']['repeated_known_facts']==0
        else:
            # An honest measured baseline failure, never a claim that OCR worked.
            assert extraction['provider']=='owner' and raw['engine']=='manual-depot-source'
            assert raw['text']=='' and raw['pages']==[] and initial['payload']['lines']==[]
            assert record['barriers'] and record['metrics']['repeated_known_facts']>0
        assert final['payload']['quality_reviewed'] and final['payload']['arithmetic_reviewed']
        assert final['payload']['remember_quality_mapping'] is False
        assert final['basis']['arithmetic']['field_count']==131
        assert final['payload']['document_number']==doc['document_number']
        if mode=='candidate':assert final['basis']['arithmetic']['document_count']==93 and final['basis']['arithmetic']['depot_count'] is None and final['basis']['arithmetic']['count_discrepancy'] is False
        settlement=current_probe['settlements'][0]
        assert settlement['id']==str(settlement_id) and settlement['status']=='posted'
        assert Decimal(settlement['total_amount'])==Decimal(doc['total_amount']) and settlement['currency']==doc['currency']
        assert settlement['payment_id'] is None and settlement['application_ids']==[]
        assert settlement['obligation_id']==record['obligation_id']
        evidence=rows(db,'SELECT * FROM harvest.evidence WHERE id=:id',id=UUID(settlement['evidence_id']))[0]
        # Harvest retains a source-linked evidence record, not another copy of
        # the potentially large Inbox binary (HarvestService._evidence).
        assert evidence['fingerprint']==source['fingerprint'], 'Harvest/Inbox content identity differs'
        assert evidence['file_bytes'] is None, 'Linked Harvest evidence unexpectedly duplicates original bytes'
        assert evidence['entity_id']==entity and evidence['source']=='owner:r2-action', 'Harvest evidence source scope differs'
        assert evidence['source_reference']==str(pid), 'Harvest evidence does not identify the confirmed proposal'
        assert proposal['inbox_id']==source['id'] and source['source']=='owner:r2', 'Proposal does not identify original owner Inbox'
        assert source['file_bytes']==file_bytes, 'Immutable Inbox bytes differ from frozen uploaded original'
        assert hashlib.sha256(source['file_bytes']).hexdigest()==record['clicked_original_sha256'], 'Downloaded original differs from immutable Inbox bytes'
        assert evidence['raw_text'] is None, 'Linked Harvest evidence unexpectedly substitutes original text'
        lines=rows(db,'SELECT * FROM harvest.settlement_lines WHERE settlement_id=:s',s=settlement_id)
        assert len(lines)==3
        by_label={x['original_quality_name']:x for x in lines}
        assert set(by_label)=={x['label'] for x in doc['lines']}
        for i,expected in enumerate(doc['lines']):
            line=by_label[expected['label']]
            assert line['net_weight_kg']==Decimal(expected['kg']) and line['unit_price']==Decimal(expected['unit_price'])
            assert line['amount']==Decimal(expected['amount'])==line['stated_amount']==line['calculated_amount']
            assert line['normalized_quality']==['HIGHEST','FIRST','SECOND'][i] and line['count'] is None
        assert sum(x['net_weight_kg'] for x in lines)==Decimal(doc['total_kg'])
        assert not rows(db,'SELECT a.* FROM harvest.line_allocations a JOIN harvest.settlement_lines l ON l.id=a.line_id WHERE l.settlement_id=:s',s=settlement_id)
        reviews=rows(db,'SELECT * FROM harvest.receipt_quality_reviews WHERE settlement_id=:s',s=settlement_id)
        assert len(reviews)==3 and all(r['proposal_id']==pid for r in reviews)
        report=rows(db,'SELECT * FROM harvest.worker_reports WHERE id=:r AND entity_id=:e',r=UUID(record['report_id']),e=entity)[0]
        assert report['cut_count']==131 and report['field_weight_kg'] is None
        linked=rows(db,'SELECT DISTINCT r.* FROM harvest.worker_reports r JOIN harvest.batch_reports br ON br.report_id=r.id JOIN harvest.settlement_batches sb ON sb.batch_id=br.batch_id WHERE sb.settlement_id=:s',s=settlement_id)
        assert [r['id'] for r in linked]==[report['id']]
        obligation=current_probe['obligations'][0]
        assert obligation['id']==record['obligation_id'] and Decimal(obligation['original_amount'])==Decimal(doc['total_amount'])
        assert obligation['direction']=='receivable' and obligation['economic_source']=='harvest_depot'
        posting=current_probe['postings'][0]
        assert posting['posting_type']=='obligation_posted' and posting['source_id']==record['obligation_id']
        entries=rows(db,'SELECT side,sum(amount) amount FROM finance.posting_entries WHERE posting_id=:p GROUP BY side',p=UUID(posting['id']))
        assert {r['side']:r['amount'] for r in entries}=={'debit':Decimal(doc['total_amount']),'credit':Decimal(doc['total_amount'])}
        settlement_ids.add(record['settlement_id']);obligation_ids.add(record['obligation_id']);posting_ids.add(posting['id']);report_ids.add(record['report_id'])
        proof['cases'].append(dict(engine=record['engine'],case_id=record['case_id'],source_id=str(sid),source_sha256=record['source_sha256'],proposal_id=str(pid),extraction_run_id=str(extraction['id']),ocr_provider=extraction['provider'],ocr_engine=raw['engine'],ocr_pages=len(raw['pages']),ocr_text=raw['text'],settlement_id=str(settlement_id),obligation_id=record['obligation_id'],posting_id=posting['id'],sale=doc['total_amount'],currency=doc['currency'],field_count=131,document_count=93 if mode=='candidate' else None,per_quality_counts='UNKNOWN',quality_reviews=3,confirmed_effects=1,confirm_actions=1,balanced_postings=1,payment=None,metrics=record['metrics'],barriers=record['barriers']))
    after=snapshot(db,entity)
    for table in UNCHANGED+('cash',):assert after['tables'][table]==before['tables'][table],f'Unrelated state changed: {table}'
    expected_ids={'harvest.depot_settlements':settlement_ids,'finance.obligations':obligation_ids,'finance.postings':posting_ids,'harvest.worker_reports':report_ids}
    for table,expected in expected_ids.items():
        old={r['id']:r for r in before['tables'][table]};new={r['id']:r for r in after['tables'][table]}
        assert all(new.get(k)==v for k,v in old.items()),f'Prior rows modified: {table}'
        assert set(new)-set(old)==expected,f'Unexpected added rows: {table}'
    old_entries=before['tables']['finance.posting_entries'];new_entries=after['tables']['finance.posting_entries']
    assert all(r in new_entries for r in old_entries)
    added_entries=[r for r in new_entries if r not in old_entries]
    assert len(added_entries)==2*len(records) and {r['posting_id'] for r in added_entries}==posting_ids
    proof['unchanged_tables']=list(UNCHANGED)+['cash']
    proof['exact_added_rows']={table:len(ids) for table,ids in expected_ids.items()}|{'finance.posting_entries':len(added_entries)}
    proof['reconciliation']=reconcile(db,include_classification=True,snapshot_established=True,entity_id=entity)
    assert proof['reconciliation']['ok'] and not proof['reconciliation']['findings']
 proof['status']='BASELINE_RECORDED' if mode=='baseline' else 'PASS'
except Exception as error:
 proof['status']='FAIL';proof['failure']={'type':type(error).__name__,'message':str(error),'traceback':traceback.format_exc()};write(ARGS.output,proof);raise
write(ARGS.output,proof)
print('CAPTURE_DAILY_SQL_'+proof['status'])
