"""Unbound counterparts of the explicit-known-creditor preparation fixtures."""
from uuid import UUID
from sqlalchemy import text
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_readiness import preview
from tests.integration.test_postgres_owner_capture import source,queue
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork


def test_unbound_dative_transcripts_remain_nonposting_review(inv):
    # The stock fixture has only TEST PREVIEW Counterparty. Do not infer that a
    # name absent from references is a dative creditor rather than an actor.
    cases=[
        'Серкану заплатил половину долга 10000 TRY наличными',
        'Серкан починил насос, остался долг Серкану 700 TRY',
        'Серкану вчера заплатил 1700 TRY в счет долга',
        'Долг Серкану 1700 TRY',
    ]
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    with sf() as db:
        assert db.scalar(text("SELECT count(*) FROM core.counterparties WHERE entity_id=:e AND name LIKE 'Серкан%'"),dict(e=inv['e']))==0
    for raw in cases:
        for mode in ('text','voice_transcript'):
            saved=source(inv,raw,mode=mode)
            ps=[p for p in queue(inv) if p['inbox_id']==saved['id']]
            assert [p['action_type'] for p in ps]==['UNRESOLVED']
            assert ps[0]['state']=='NEEDS_INPUT'
            assert svc.original(UUID(inv['e']),a,UUID(saved['id']))[0].decode()==raw
    with sf() as db:
        for table in ('core.confirmation_effects','finance.payments','finance.direct_expenses','finance.obligations'):
            assert db.scalar(text(f'SELECT count(*) FROM {table} WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_explicit_creditor_role_and_completed_settlement_persist_without_effects(inv):
    from tests.integration.test_postgres_readiness import send
    creditor=send(inv['c'],f"/entities/{inv['e']}/counterparties",dict(name='Atlas Servis',role='supplier',source='test:creditor-frame'))
    bank=send(inv['c'],f"/entities/{inv['e']}/money-accounts",dict(name='Bank TR',account_type='bank',currency='TRY',source='test:creditor-frame'))
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    cases=[
        ('В учете нужен долг подрядчику Atlas Servis: 6400 TRY за уже выполненную работу.','PAYABLE'),
        ('Погашение части долга Atlas Servis проведено вчера с Bank TR, перечислено 1300 TRY.','PAYMENT'),
        ('Борис долг подрядчику Atlas Servis 6400 TRY','UNRESOLVED'),
        ('Погашение части долга Бориса проведено вчера с Bank TR, перечислено 1300 TRY.','UNRESOLVED'),
    ]
    for raw,kind in cases:
        pairs=[]
        for mode in ('text','voice_transcript'):
            saved=source(inv,raw,mode=mode);ps=[p for p in queue(inv) if p['inbox_id']==saved['id']]
            assert [p['action_type'] for p in ps]==[kind]
            fields=ps[0]['revision']['payload'];pairs.append(fields)
            if kind!='UNRESOLVED':assert fields['counterparty_id']==creditor['id']
            if kind=='PAYABLE':assert fields['amount']=='6400'
            if kind=='PAYMENT':
                assert fields['amount']=='1300' and fields['event_date']=='2026-10-07'
                assert fields['money_account_id']==bank['id'] and fields['payment_purpose']=='debt'
                assert not fields.get('obligation_id')
            assert svc.original(UUID(inv['e']),a,UUID(saved['id']))[0].decode()==raw
        assert pairs[0]==pairs[1]
    with sf() as db:
        for table in ('core.confirmation_effects','finance.payments','finance.direct_expenses','finance.obligations'):
            assert db.scalar(text(f'SELECT count(*) FROM {table} WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_accrued_obligation_keeps_dates_without_authorizing_other_debtors(inv):
    from tests.integration.test_postgres_readiness import send
    creditor=send(inv['c'],f"/entities/{inv['e']}/counterparties",dict(name='Серкан',role='supplier',source='test:accrued-obligation'))
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    cases=[
        ('Вчера возник долг Серкану 4200 TRY за ремонт, оплатить до послезавтра.','PAYABLE'),
        ('Вчера у меня образовался долг Серкану 4200 TRY за ремонт, оплатить до послезавтра.','PAYABLE'),
        ('Вчера у Бориса остался долг Серкану 4200 TRY за ремонт.','UNRESOLVED'),
        ('Вчера возник долг Бориса 4200 TRY за ремонт.','UNRESOLVED'),
    ]
    for raw,kind in cases:
        pairs=[]
        for mode in ('text','voice_transcript'):
            saved=source(inv,raw,mode=mode);ps=[p for p in queue(inv) if p['inbox_id']==saved['id']]
            assert [p['action_type'] for p in ps]==[kind]
            fields=ps[0]['revision']['payload'];pairs.append(fields)
            if kind=='PAYABLE':
                assert fields['counterparty_id']==creditor['id'] and fields['amount']=='4200'
                assert fields['event_date']=='2026-10-07' and fields['due_date']=='2026-10-10'
                assert fields['description']==raw
            assert svc.original(UUID(inv['e']),a,UUID(saved['id']))[0].decode()==raw
        assert pairs[0]==pairs[1]
    with sf() as db:
        for table in ('core.confirmation_effects','finance.payments','finance.direct_expenses','finance.obligations'):
            assert db.scalar(text(f'SELECT count(*) FROM {table} WHERE entity_id=:e'),dict(e=inv['e']))==0
