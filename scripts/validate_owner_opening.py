"""Owner-confirmed input in disposable validation only. No production execution.

Reference facts/opening are authoritative owner input; actor authentication is a
test adapter. This script invents no actual payment, funding or opening cash.
"""
import json
import hashlib
import os
from pathlib import Path
from uuid import UUID
from fastapi.testclient import TestClient
from greenhouse_owner_os.api.app import create_app
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.infrastructure.test_safety import require_disposable_database
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.finance import FinanceService
from greenhouse_owner_os.services.references import ReferenceService, provision_authenticated_owner

ROOT = Path(__file__).resolve().parents[1]


def setup_confirmed_profile(client, manifest):
    if manifest['purpose'] != 'EXPLICIT_OWNER_VERIFIED_INPUT_NOT_SYSTEM_SEED':
        raise ValueError('explicit owner input required, never an automatic system fixture')
    if manifest['cutover_date'] != '2026-10-04' or any(a['opening_balance'] != '0.00' for a in manifest['money_accounts']):
        raise ValueError('this owner manifest permits zero money openings only')
    def send(path, body, key, expected=201):
        r = client.post('/v1'+path, json=body, headers={'Idempotency-Key':key})
        if r.status_code != expected:
            raise AssertionError(f'{path}: {r.status_code}: {r.text}')
        return r.json()
    entity = send('/entities',dict(name=manifest['entity'],currency=manifest['money_accounts'][0]['currency'],
        source=manifest['authority']),'confirmed-entity')
    eid = entity['id']
    for currency in sorted({a['currency'] for a in manifest['money_accounts']}):
        send('/entities/'+eid+'/reference-bootstrap',{'currency':currency},'confirmed-system-'+currency,200)
    accounts = [send('/entities/'+eid+'/money-accounts',
        {**{k:v for k,v in a.items() if k!='opening_balance'},'source':manifest['authority']},
        'confirmed-money-'+hashlib.sha256(a['name'].encode('utf-8')).hexdigest()) for a in manifest['money_accounts']]
    cp = send('/entities/'+eid+'/counterparties',dict(manifest['counterparty'],source=manifest['authority']),'confirmed-counterparty')
    opening = send('/entities/'+eid+'/opening-payables',
        dict(manifest['opening_payable'],counterparty_id=cp['id']),'confirmed-opening-payable')
    return dict(entity=entity, money_accounts=accounts, counterparty=cp, opening=opening)


def main():
    url = os.environ['TEST_DATABASE_URL']
    require_disposable_database(url, os.getenv('APP_ENV'))
    sf = make_session_factory(url)
    actor = provision_authenticated_owner(sf,external_ref='test:owner-opening-validation',
        display_name='TEST VALIDATION Owner adapter',environment='OWNER_VERIFIED')
    factory = lambda: SqlAlchemyUnitOfWork(sf)
    finance = FinanceService(factory)
    references = ReferenceService(factory,environment='OWNER_VERIFIED')
    client = TestClient(create_app(finance_service=finance,reference_service=references,actor_provider=lambda:actor))
    manifest = json.loads((ROOT/'config/owner_finance_opening.json').read_text())
    profile = setup_confirmed_profile(client,manifest)
    again = setup_confirmed_profile(client,manifest)
    assert profile == again
    eid = UUID(profile['entity']['id'])
    balances = finance.get_currency_balances(eid)['balances']
    assert balances['TRY']['payable']=='2600.00'
    assert all(p['cash']=='0.00' and p['receivable']=='0.00' and p['advance_paid']=='0.00' and p['advance_received']=='0.00' for p in balances.values())
    assert references.readiness(eid)['ok'] and finance.reconcile(entity_id=eid)['ok']
    assert finance.get_obligation(UUID(profile['opening']['id']))['event_date']=='2026-10-02'
    print('OWNER VERIFIED INPUT / DISPOSABLE VALIDATION ONLY: five accounts zero; opening AP 2600 TRY; original date 2026-10-02; recognition 2026-10-04; reconciliation PASS; retry PASS')


if __name__ == '__main__':
    main()
