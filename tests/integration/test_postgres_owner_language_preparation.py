"""Actual persisted preparation, unchanged source/revision/authority boundary."""
from uuid import UUID
from sqlalchemy import text
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_readiness import preview,send
from tests.integration.test_postgres_owner_capture import source,queue,revise
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork


def test_real_two_page_supplier_invoice_remains_original_nonposting_independent_review(inv):
    import base64
    from tests.ocr_corpus import document
    content,mime=document(28)
    saved=source(inv,None,mode='photo',filename='supplier.pdf',mime_type=mime,file_base64=base64.b64encode(content).decode())
    proposals=[p for p in queue(inv) if p['inbox_id']==saved['id']]
    assert sorted(p['action_type'] for p in proposals)==['INVOICE_MATCH','PAYABLE']
    sf,actor,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    assert svc.original(UUID(inv['e']),actor,UUID(saved['id']))[0]==content
    before_ids={p['id'] for p in proposals}
    svc.process(UUID(saved['id']))
    assert {p['id'] for p in queue(inv) if p['inbox_id']==saved['id']}==before_ids
    payable=next(p for p in proposals if p['action_type']=='PAYABLE')
    assert payable['revision']['payload']['amount']=='5000' and payable['revision']['payload']['currency']=='TRY'
    assert payable['revision']['missing'] and payable['state']=='NEEDS_INPUT'
    assert all(p['revision']['extraction_run_id'] and p['source']['has_file'] for p in proposals)
    other=next(p for p in proposals if p['action_type']=='INVOICE_MATCH')
    send(inv['c'],f"/entities/{inv['e']}/owner/confirmations/{payable['id']}/reject",dict(expected_version=payable['version']),expected=200)
    assert next(p for p in queue(inv) if p['id']==other['id'])['state']==other['state']
    with sf() as db:
        assert db.scalar(text('SELECT count(*) FROM core.extraction_runs WHERE inbox_id=:s'),dict(s=saved['id']))==1
        for table in ('core.confirmation_effects','finance.postings','inventory.movements','harvest.worker_reports'):
            assert db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_language_correction_original_and_text_voice_parity(inv):
    raw='Сегодня привезли не 15 кг, а 20 кг KNO3 на TEST A в İmamlı'
    captures=[source(inv,raw,mode=mode) for mode in ('text','voice_transcript')]
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    payloads=[]
    for capture in captures:
        p=next(p for p in queue(inv) if p['inbox_id']==capture['id'])
        assert p['action_type']=='STOCK_RECEIPT'
        payloads.append(p['revision']['payload'])
        assert svc.original(UUID(inv['e']),a,UUID(capture['id']))[0].decode()==raw
    assert payloads[0]==payloads[1]
    ln=payloads[0]['lines'][0]
    assert ln['quantity']=='20' and ln['product_id']==inv['p']['id'] and ln['location_id']==inv['l']['id'] and ln['greenhouse_id']==inv['g']
    assert ln['unit_cost'] is None
    with sf() as db:
        assert db.scalar(text('SELECT count(*) FROM inventory.events WHERE entity_id=:e'),dict(e=inv['e']))==0
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_multiple_prepared_effects_retry_and_append_only_revision(inv):
    raw='Серкан починил насос. Должен Серкану 5 тысяч TRY. Привезли 20 кг KNO3 на TEST A, счет потом пришлют'
    capture=source(inv,raw);ps=[p for p in queue(inv) if p['inbox_id']==capture['id']]
    assert sorted(p['action_type'] for p in ps)==['PAYABLE','STOCK_RECEIPT','UNRESOLVED','UNRESOLVED']
    payable=next(p for p in ps if p['action_type']=='PAYABLE')
    assert payable['revision']['payload']['amount']=='5000'
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    svc.process(UUID(capture['id']))
    assert {p['id'] for p in queue(inv) if p['inbox_id']==capture['id']}=={p['id'] for p in ps}
    old=payable['revision'];updated=revise(inv,payable,dict(old['payload'],amount='5100'))
    assert updated['version']>payable['version'] and updated['revision']['payload']['amount']=='5100'
    assert svc.original(UUID(inv['e']),a,UUID(capture['id']))[0].decode()==raw
    with sf() as db:
        assert db.scalar(text('SELECT count(*) FROM core.proposal_revisions WHERE proposal_id=:p'),dict(p=payable['id']))==2
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_negation_transfer_and_credit_never_prepare_paid_expense(inv):
    phrases=['Не выдал зарплату, только подготовил ведомость','Перевел деньги между своими счетами','Внес свои 50000 TRY в бизнес','Купил у Серкана KNO3 за 10000 лир в долг']
    for raw in phrases:
        capture=source(inv,raw);ps=[p for p in queue(inv) if p['inbox_id']==capture['id']]
        assert [p['action_type'] for p in ps]==(['PAYABLE'] if 'в долг' in raw else ['UNRESOLVED'])
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_scope_and_uncertainty_regressions_persist_as_nonposting_review(inv):
    # A preposed dative is a known creditor only with explicit reference evidence.
    send(inv['c'],f"/entities/{inv['e']}/counterparties",dict(name='Серкан Ремонт насосов',role='supplier',source='test:known-creditor'))
    cases=[
        ('Купил 10 кг KNO3 за 5000 TRY, деньги отдам завтра','PAYABLE'),
        ('Сегодня внесли 10 кг KNO3, а не 15 кг, из TEST A в Имамлы','STOCK_APPLICATION'),
        ('Сегодня привезли около 100 кг KNO3 на TEST A','STOCK_RECEIPT'),
        ('Серкану заплатил половину долга 10000 TRY наличными','PAYMENT'),
    ]
    results=[]
    for raw,kind in cases:
        saved=source(inv,raw)
        p=next(p for p in queue(inv) if p['inbox_id']==saved['id'])
        assert p['action_type']==kind and p['state'] in ('READY','NEEDS_INPUT')
        results.append(p['revision']['payload'])
    assert results[0]['event_date']=='2026-10-08' and results[0]['due_date']=='2026-10-09'
    assert len(results[1]['lines'])==1 and results[1]['lines'][0]['quantity']=='10'
    assert results[2]['lines'][0]['quantity'] is None and results[3]['amount'] is None
    saved=source(inv,'Заплатил Серкану 5000 TRY, он сказал не переводить с банка')
    ps=[p for p in queue(inv) if p['inbox_id']==saved['id']]
    assert {p['action_type'] for p in ps}=={'PAYMENT','UNRESOLVED'}
    assert next(p for p in ps if p['action_type']=='PAYMENT')['revision']['payload']['money_account_id'] is None
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_depot_plus_payment_keeps_independent_persisted_slots(inv):
    from tests.integration.test_postgres_readiness import send
    send(inv['c'],f"/entities/{inv['e']}/counterparties",dict(name='Görgün Depo',role='depot',source='test:language'))
    raw='Görgün Depo\n08.10.2026\nGrand 100 20 2000\nAlacak 2000\nОплатил 1000 наличными'
    saved=source(inv,raw);ps=[p for p in queue(inv) if p['inbox_id']==saved['id']]
    assert {p['action_type'] for p in ps}=={'DEPOT_RECEIPT','PAYMENT'}
    receipt=next(p for p in ps if p['action_type']=='DEPOT_RECEIPT')['revision']['payload']
    payment=next(p for p in ps if p['action_type']=='PAYMENT')['revision']['payload']
    assert receipt['alacak_value']=='2000' and payment['amount']=='1000' and payment['counterparty_id'] is None
    with inv['preview'][0]() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_reusable_exposed_safety_repairs_persist_without_effects(inv):
    from tests.integration.test_postgres_readiness import send
    send(inv['c'],f"/entities/{inv['e']}/counterparties",dict(name='Görgün Depo',role='depot',source='test:language'))
    cases=[
        ('Привез ли поставщик 24 кг KNO3 на TEST A?','UNRESOLVED'),
        ('Перечислил предоплату 4200 TRY','UNRESOLVED'),
        ('Платеж будет завершен позже: 4200 TRY','UNRESOLVED'),
        ('Для меня, не для работника: завтра отвезти образцы из Имамлы','PERSONAL_TASK'),
        ('Погашение долга проведено 07.10.2026, перечислено 4200 TRY','PAYMENT'),
        ('На TEST A 07.10.2026 приняли поставку: KNO3 в количестве 24 кг. Стоимость пока неизвестна.','STOCK_RECEIPT'),
        ('Görgün Depo выдал приемку 07.10.2026. Birinci: количество 6, вес 72 кг, цена 15 TRY, всего 1080 TRY. Alacak 1080.','DEPOT_RECEIPT'),
    ]
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf));results=[]
    for raw,kind in cases:
        pair=[]
        for mode in ('text','voice_transcript'):
            saved=source(inv,raw,mode=mode);ps=[p for p in queue(inv) if p['inbox_id']==saved['id']]
            assert len(ps)==1 and ps[0]['action_type']==kind
            pair.append(ps[0]['revision']['payload'])
            assert svc.original(UUID(inv['e']),a,UUID(saved['id']))[0].decode()==raw
        assert pair[0]==pair[1]
        results.append(pair[0])
    assert results[3]['worker_id'] is None
    assert results[4]['event_date']=='2026-10-07' and results[4]['amount']=='4200'
    assert results[5]['event_date']=='2026-10-07' and results[5]['lines'][0]['quantity']=='24'
    assert results[5]['lines'][0]['unit_cost'] is None
    assert results[6]['lines'][0]['count']==6 and results[6]['lines'][0]['net_weight_kg']=='72'
    assert not results[6]['arithmetic_reviewed'] and not results[6]['quality_reviewed']
    with sf() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0
        assert db.scalar(text('SELECT count(*) FROM inventory.events WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_scoped_speech_intention_and_explicit_cardinality_persist(inv):
    # A preposed dative is a known creditor only with explicit reference evidence.
    send(inv['c'],f"/entities/{inv['e']}/counterparties",dict(name='Серкан Ремонт насосов',role='supplier',source='test:known-creditor'))
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    cases=[
        ('Серкан сказал: «Я заплатил 700 TRY. Потом перевел 300 TRY».',['UNRESOLVED']),
        ('Планирую погасить долг Серкану 700 TRY завтра',['UNRESOLVED']),
        ('Он вчера купил фильтр за 700 TRY',['UNRESOLVED']),
        ('Мне должен Серкан 700 TRY',['UNRESOLVED']),
        ("Серкан сообщил об оплате: 'я заплатил 700 TRY'",['UNRESOLVED']),
        ('07.10.2026 погасил часть долга Серкану 700 TRY наличными',['PAYMENT']),
        ('07.10.2026 купил фильтр за 700 TRY и оплатил наличными',['DIRECT_EXPENSE']),
        ('Сегодня дважды заплатил Серкану в счет долга: первый платеж 700 TRY, второй платеж тоже 700 TRY',['PAYMENT','PAYMENT']),
        ('Серкан починил насос, остался долг Серкану 700 TRY',['UNRESOLVED','PAYABLE']),
    ]
    for raw,kinds in cases:
        pairs=[]
        for mode in ('text','voice_transcript'):
            saved=source(inv,raw,mode=mode);ps=[p for p in queue(inv) if p['inbox_id']==saved['id']]
            assert sorted(p['action_type'] for p in ps)==sorted(kinds)
            pairs.append(sorted([(p['action_type'],p['revision']['payload']) for p in ps],key=lambda x:(x[0],x[1].get('description') or x[1].get('note') or '')))
            before={p['id'] for p in ps};svc.process(UUID(saved['id']))
            assert {p['id'] for p in queue(inv) if p['inbox_id']==saved['id']}==before
            assert svc.original(UUID(inv['e']),a,UUID(saved['id']))[0].decode()==raw
        assert pairs[0]==pairs[1]
    with sf() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_depot_and_third_party_finance_remain_separate_nonposting_evidence(inv):
    from tests.integration.test_postgres_readiness import send
    for name,role in [('Görgün Depo','depot'),('Серкан','supplier'),('Kemal Bey','supplier')]:
        send(inv['c'],f"/entities/{inv['e']}/counterparties",dict(name=name,role=role,source='test:scope'))
    raw='Görgün Depo выдал приемку 07.10.2026\nGrand 30 кг по 20 TRY сумма 600 TRY\nСеркан должен Kemal Bey 2600 TRY'
    saved=source(inv,raw);ps=[p for p in queue(inv) if p['inbox_id']==saved['id']]
    assert sorted(p['action_type'] for p in ps)==['DEPOT_RECEIPT','UNRESOLVED']
    depot=next(p for p in ps if p['action_type']=='DEPOT_RECEIPT')['revision']['payload']
    unresolved=next(p for p in ps if p['action_type']=='UNRESOLVED')['revision']['payload']
    assert depot['event_date']=='2026-10-07' and depot['lines'][0]['net_weight_kg']=='30'
    assert 'Серкан должен Kemal Bey 2600 TRY' in unresolved['note']
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    assert svc.original(UUID(inv['e']),a,UUID(saved['id']))[0].decode()==raw
    with sf() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_unknown_financial_actor_and_owner_contrasts_persist_without_effects(inv):
    # A preposed dative is a known creditor only with explicit reference evidence.
    send(inv['c'],f"/entities/{inv['e']}/counterparties",dict(name='Серкан Ремонт насосов',role='supplier',source='test:known-creditor'))
    sf,a,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    cases=[
        ('Борис вчера заплатил 1700 TRY за ремонт',['UNRESOLVED']),
        ('Hasan Kaya вчера купил насос за 1700 TRY',['UNRESOLVED']),
        ('حسن должен Серкану 1700 TRY',['UNRESOLVED']),
        ('murat вчера получил invoice на 1700 TRY',['UNRESOLVED']),
        ('Долг Бориса 1700 TRY, еще не оплачено',['UNRESOLVED']),
        ('Борис вчера заплатил 1700 TRY за ремонт и купил насос за 500 TRY',['UNRESOLVED']),
        ('Я вчера заплатил 1700 TRY за ремонт',['PAYMENT']),
        ('Заплатил вчера 1700 TRY за ремонт',['PAYMENT']),
        ('Серкану вчера заплатил 1700 TRY в счет долга',['PAYMENT']),
        ('Долг Серкану 1700 TRY',['PAYABLE']),
    ]
    for raw,kinds in cases:
        pairs=[]
        for mode in ('text','voice_transcript'):
            saved=source(inv,raw,mode=mode)
            ps=[p for p in queue(inv) if p['inbox_id']==saved['id']]
            assert [p['action_type'] for p in ps]==kinds
            pairs.append([p['revision']['payload'] for p in ps])
            assert svc.original(UUID(inv['e']),a,UUID(saved['id']))[0].decode()==raw
            if kinds==['UNRESOLVED']:
                assert ps[0]['revision']['payload']['note']==raw and ps[0]['state']=='NEEDS_INPUT'
        assert pairs[0]==pairs[1]
    with sf() as db:
        assert db.scalar(text('SELECT count(*) FROM core.confirmation_effects WHERE entity_id=:e'),dict(e=inv['e']))==0
        assert db.scalar(text('SELECT count(*) FROM finance.payments WHERE entity_id=:e'),dict(e=inv['e']))==0
        assert db.scalar(text('SELECT count(*) FROM finance.direct_expenses WHERE entity_id=:e'),dict(e=inv['e']))==0


def test_transfer_source_is_unresolved_with_partial_or_no_account_references_in_both_modes(inv):
    # The initial TEST PREVIEW account names do not match either source label.
    from greenhouse_owner_os.domain.owner_capture import ConfirmAction
    from greenhouse_owner_os.domain.errors import ValidationError
    import pytest
    sf,actor,*_=inv['preview'];svc=OwnerCaptureService(lambda:SqlAlchemyUnitOfWork(sf))
    raw='Перевел 20000 TRY из Cash TR в Bank TR'
    for count in range(3):
        if count:
            send(inv['c'],f"/entities/{inv['e']}/money-accounts",dict(name=['Cash TR','Bank TR'][count-1],currency='TRY',account_type=['cash','bank'][count-1],source='test:transfer-scope'))
        for mode in ('text','voice_transcript'):
            saved=source(inv,raw,mode=mode)
            proposals=[p for p in queue(inv) if p['inbox_id']==saved['id']]
            assert len(proposals)==1
            p=proposals[0]
            assert p['action_type']=='UNRESOLVED' and p['revision']['payload']=={'note':raw}
            assert p['state']=='NEEDS_INPUT' and p['revision']['missing']
            assert svc.original(UUID(inv['e']),actor,UUID(saved['id']))[0].decode()==raw
            with pytest.raises(ValidationError):
                svc.confirm(UUID(inv['e']),actor,UUID(p['id']),'test:blocked:'+p['id'],ConfirmAction(expected_version=p['version'],preview_digest=p['revision']['preview_digest'],confirmed=True))
    with sf() as db:
        for table in ('finance.postings','core.confirmation_effects','inventory.movements','workers.assignments'):
            assert db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'),dict(e=inv['e']))==0
