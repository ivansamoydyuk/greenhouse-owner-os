"""Friction reversal contract and complete immutable ledger evidence."""
from tests.integration.test_postgres_readiness import preview,send,setup
from scripts.owner_friction_posting_evidence import read_posting


def test_finance_reversal_projection_preserves_original_posting_and_entries(preview):
    c,e,cash,bank,cp,ids=setup(preview)
    common=dict(entity_id=e,counterparty_id=cp,currency='TRY',event_date='2026-10-04')
    obligation=send(c,'/obligations',dict(common,direction='payable',original_amount='100.00',offset_ledger_account_id=ids['GENERAL_EXPENSE']))
    obligation=send(c,f"/obligations/{obligation['id']}/post?expected_version=1",expected=200)
    payment=send(c,'/payments',dict(common,direction='out',money_account_id=cash,amount='40.00',allocations=[dict(obligation_id=obligation['id'],amount='40.00')]))
    payment=send(c,f"/payments/{payment['id']}/post?expected_version=1",expected=200)
    for path,record in [('payments',payment),('obligations',obligation)]:
        before=read_posting(record['posting_id'])
        result=send(c,f"/{path}/{record['id']}/reverse",dict(expected_version=record['version'],event_date='2026-10-04',reason='TEST immutable ledger evidence'),expected=200)
        assert 'posting_id' not in result
        assert result['original_posting_id']==record['posting_id']
        assert result['reversal_posting_id']!=record['posting_id']
        assert read_posting(record['posting_id'])==before
        reversal=read_posting(result['reversal_posting_id'])
        assert reversal['posting']['reversal_of_id']==record['posting_id']
        assert reversal['posting']['source_id']==before['posting']['source_id']==record['id']
        assert reversal['posting']['entity_id']==before['posting']['entity_id']==e
        def entries(value,reverse=False):
            return sorted((r['ledger_account_id'],r['amount'],('credit' if r['side']=='debit' else 'debit') if reverse else r['side']) for r in value['entries'])
        assert entries(reversal)==entries(before,True)
    assert c.get(f'/v1/finance/reconciliation?entity_id={e}').json()['ok']
    assert set(c.get(f'/v1/entities/{e}/balances').json()['balances']['TRY'].values())=={'0.00'}
