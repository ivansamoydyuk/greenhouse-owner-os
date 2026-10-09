"""Representative real-PostgreSQL boundary for the language benchmark.

Fast corpus metrics use actual prepare with fixture reads. These tests establish
fixture/query fidelity, retained source, persisted text/voice parity, no posting,
and processing/source retry idempotency. They do not claim acoustic recognition.
"""
from copy import deepcopy
from datetime import date,datetime,timezone
from types import SimpleNamespace
from uuid import UUID,uuid4
import pytest
from sqlalchemy import select,text
from greenhouse_owner_os.domain.owner_capture import CaptureSource
from greenhouse_owner_os.domain.workers import WorkerInput
from greenhouse_owner_os.infrastructure.models import CounterpartyRow,WorkAreaRow,MoneyAccountRow
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from greenhouse_owner_os.services.workers import WorkersService
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_readiness import preview,send
from scripts.owner_language_benchmark import prepare_case


PHRASES=[
    ('irrigation','Умар завтра полить Имамлы 40 минут'),
    ('ventilation','Умар завтра закрыть окна в Имамлы'),
    ('fertigation','Сегодня внесли 15 кг KNO3 из TEST A в Имамлы'),
    ('plant_work','Умар завтра убрать сухие листья в Имамлы'),
    ('repair','Серкан починил насос. Заплатил Серкану 7000 TRY из Cash TR'),
    ('harvest','Сегодня срезали 42 ветки, чек из депо еще не получили'),
    ('inventory','Сегодня привезли 100 кг KNO3 в TEST A, счет придет потом'),
    ('worker','Умар завтра проверить Имамлы и прислать фото'),
    ('personal','Завтра позвонить Серкану'),
    ('lab','Агроном рекомендует Триходерму, пока не применяли'),
    ('payable','Должен Серкану 5000 TRY'),
    ('payment','Заплатил Серкану 5000 TRY из Bank TR'),
    ('salary','Умару зарплату переведу завтра'),
    ('utility','Электричество не оплатил, только проверил счет'),
    ('transfer','Перевел 10000 TRY между своими счетами'),
    ('party','Должен неизвестному мастеру 7000 TRY'),
    ('document','Это повтор старого чека, новой оплаты нет'),
    ('incident','Насос сломался, но расходов еще не было'),
    ('depot_lines','Görgün Depo 09.10.2026\nGrand 100 20 2000\nAlacak 2000'),
]


@pytest.fixture
def benchmark_refs(inv):
    sf,actor,*_=inv['preview'];e=UUID(inv['e']);c=inv['c'];base=f'/entities/{e}'
    send(c,base+'/counterparties',dict(name='Серкан',role='supplier',source='test:benchmark'))
    send(c,base+'/counterparties',dict(name='Görgün Depo',role='depot',source='test:benchmark'))
    for name,kind in [('Cash TR','cash'),('Bank TR','bank')]:
        send(c,base+'/money-accounts',dict(name=name,account_type=kind,currency='TRY',source='test:benchmark'))
    factory=lambda:SqlAlchemyUnitOfWork(sf)
    worker=WorkersService(factory).worker(e,actor,str(uuid4()),WorkerInput(display_name='Умар',native_name='Umar',language='fa',source='test:benchmark'))
    # Same-name foreign-entity decoy proves the fixture cannot just return all rows.
    other=send(c,'/entities',dict(name='TEST Benchmark Foreign '+str(uuid4()),currency='TRY',source='test:benchmark'))
    send(c,f"/entities/{other['id']}/counterparties",dict(name='Серкан',role='supplier',source='test:benchmark'))
    foreignworker=WorkersService(factory).worker(UUID(other['id']),actor,str(uuid4()),WorkerInput(display_name='Умар',language='fa',source='test:benchmark'))
    with sf() as session:
        orm={model:[SimpleNamespace(**{col.name:getattr(row,col.name) for col in model.__table__.columns}) for row in session.scalars(select(model))] for model in (CounterpartyRow,WorkAreaRow,MoneyAccountRow)}
        sql={table:[dict(r) for r in session.execute(text('SELECT * FROM '+table)).mappings()] for table in ('inventory.products','inventory.locations','workers.people','harvest.depot_quality_mappings','finance.obligations')}
    return dict(inv=inv,sf=sf,actor=actor,e=e,service=OwnerCaptureService(factory),fixture=dict(orm=orm,sql=sql),worker=worker,foreignworker=foreignworker)


@pytest.mark.parametrize('family,phrase',PHRASES,ids=[x[0] for x in PHRASES])
def test_prepare_reference_fixture_matches_real_postgres(benchmark_refs,family,phrase):
    b=benchmark_refs;s=dict(id=uuid4(),e=b['e'],a=b['actor'],binary=None,day=date(2026,10,9),raw=phrase,duplicate=False)
    with SqlAlchemyUnitOfWork(b['sf']) as u:
        actual=b['service'].prepare(u,s,dict(text=phrase,pages=[],warnings=[]))
    mocked=prepare_case(dict(id='pg-'+family,text=phrase,entity_id=str(b['e'])),fixture=b['fixture'])
    assert mocked==actual
    assert str(b['foreignworker']['id']) not in str(actual)


def test_persisted_text_voice_source_retry_parity_and_no_business_effects(benchmark_refs):
    b=benchmark_refs;svc=b['service'];e=b['e'];actor=b['actor'];sf=b['sf']
    for family,phrase in PHRASES:
        mode_payloads=[]
        for mode in ('text','voice_transcript'):
            body=CaptureSource(client_capture_id=uuid4(),captured_at=datetime(2026,10,9,9,tzinfo=timezone.utc),mode=mode,text=phrase)
            source=svc.capture(e,actor,str(uuid4()),body)
            assert source['state']=='SERVER_SAVED' and source['proposals']==[]
            assert svc.original(e,actor,UUID(source['id']))[0]==phrase.encode()
            # Same source identity with different transport key remains one source.
            assert svc.capture(e,actor,str(uuid4()),body)['id']==source['id']
            svc.process(UUID(source['id']));svc.process(UUID(source['id']))
            with sf() as db:
                assert db.scalar(text("SELECT count(*) FROM core.extraction_runs WHERE inbox_id=:i"),dict(i=source['id']))==1
                job=db.scalar(text('SELECT state FROM core.processing_jobs WHERE inbox_id=:i'),dict(i=source['id']))
                assert job=='SUCCEEDED',(family,mode,job)
                records=list(db.execute(text('SELECT p.action_type,p.slot,r.payload FROM core.proposed_actions p JOIN core.proposal_revisions r ON r.proposal_id=p.id AND r.version=p.version WHERE p.inbox_id=:i ORDER BY p.slot'),dict(i=source['id'])).mappings())
                assert records
                assert len({r['slot'] for r in records})==len(records)
                mode_payloads.append([(r['action_type'],r['payload']) for r in records])
        assert mode_payloads[0]==mode_payloads[1],family
    with sf() as db:
        for table in ('core.confirmation_effects','finance.postings','inventory.movements','harvest.worker_reports'):
            assert db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),dict(e=e))==0


def test_scoped_depot_candidates_still_require_owner_quality_review(benchmark_refs):
    b=benchmark_refs;svc=b['service'];phrase=next(p for family,p in PHRASES if family=='depot_lines')
    body=CaptureSource(client_capture_id=uuid4(),captured_at=datetime(2026,10,9,9,tzinfo=timezone.utc),text=phrase)
    source=svc.capture(b['e'],b['actor'],str(uuid4()),body);svc.process(UUID(source['id']))
    with b['sf']() as db:
        records=list(db.execute(text('SELECT p.state,r.payload,r.missing FROM core.proposed_actions p JOIN core.proposal_revisions r ON r.proposal_id=p.id AND r.version=p.version WHERE p.inbox_id=:i AND p.action_type=\'DEPOT_RECEIPT\''),dict(i=source['id'])).mappings())
    assert len(records)==1
    row=records[0]
    assert row['payload']['lines'][0]['original_quality_name']=='Grand'
    assert row['payload']['quality_reviewed'] is False
    assert row['state']=='NEEDS_INPUT'
    assert any('качеств' in message.lower() for message in row['missing'])
