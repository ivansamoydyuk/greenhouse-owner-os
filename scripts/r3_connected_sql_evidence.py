"""Read-only PostgreSQL attestation for IDs produced by real browser actions."""
import json
import os
import sys
from pathlib import Path
from uuid import UUID
from sqlalchemy import text
from greenhouse_owner_os.infrastructure.database import make_session_factory
from greenhouse_owner_os.preview import _require_connected_runtime
from greenhouse_owner_os.services.finance import FinanceService
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork


def main():
    result=json.loads(Path(sys.argv[1]).read_text())
    assert result['status']=='PASS'
    assert result['sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA']
    factory=make_session_factory(os.environ['TEST_DATABASE_URL'])
    proof={'feature_sha':result['sha'],'browser_journeys':result['journeys'],'expenses':[],'payments':[],'receipts':[],'reports':[],'settlements':[]}
    with factory() as session:
        _require_connected_runtime(session)
        def one(sql,**params):
            return dict(session.execute(text(sql),params).mappings().one())
        def scalar(sql,**params):
            return session.scalar(text(sql),params)
        for item in result['records']['expenses']:
            source=one('SELECT raw_text,file_bytes FROM core.inbox_items WHERE id=:id',id=UUID(item['source_id']))
            assert source['raw_text']==item['original']
            row=one('SELECT d.id,d.amount,d.posting_id,d.status,count(c.proposal_id) effects FROM finance.direct_expenses d JOIN core.confirmation_effects c ON c.direct_expense_id=d.id WHERE d.proposal_id=:id GROUP BY d.id',id=UUID(item['proposal_id']))
            assert str(row['amount'])==item['amount'] and row['effects']==1 and row['status']=='posted'
            assert scalar('SELECT count(*) FROM finance.postings WHERE id=:id',id=row['posting_id'])==1
            proof['expenses'].append({**row,'source_retained':True})
        for payment in result['records']['payments']:
            row=one('SELECT id,entity_id,status,amount,direction,currency,posting_id FROM finance.payments WHERE id=:id',id=UUID(payment))
            assert row['status']=='posted'
            assert scalar('SELECT count(*) FROM finance.postings WHERE id=:id',id=row['posting_id'])==1
            assert scalar('SELECT count(*) FROM finance.advances WHERE source_payment_id=:id',id=row['id'])==0
            allocations=[dict(x) for x in session.execute(text('SELECT obligation_id,amount FROM finance.payment_allocations WHERE payment_id=:id'),{'id':row['id']}).mappings()]
            assert sum(x['amount'] for x in allocations)==row['amount']
            for allocation in allocations:
                obligation=one('SELECT counterparty_id,direction,currency FROM finance.obligations WHERE id=:id',id=allocation['obligation_id'])
                assert obligation['currency']==row['currency'] and obligation['direction']==('receivable' if row['direction']=='in' else 'payable')
            proof['payments'].append({**row,'allocations':allocations})
        for receipt in result['records']['receipts']:
            row=one('SELECT id,entity_id,status,kind,evidence FROM inventory.events WHERE id=:id',id=UUID(receipt['id']))
            assert row['status']=='posted' and row['kind']=='receipt'
            movements=[dict(x) for x in session.execute(text('SELECT event_id,product_id,location_id,quantity,uncosted_quantity,known_cost FROM inventory.movements WHERE event_id=:id'),{'id':row['id']}).mappings()]
            assert len(movements)==1
            from decimal import Decimal
            assert movements[0]['quantity']==Decimal(receipt['quantity'])
            assert str(movements[0]['product_id'])==receipt['product_id'] and str(movements[0]['location_id'])==receipt['location_id']
            assert movements[0]['quantity']==movements[0]['uncosted_quantity'] and movements[0]['known_cost']==0
            proof['receipts'].append({'id':row['id'],'status':row['status'],'original':row['evidence']['raw_text'],'movements':movements,'opening':'UNINITIALIZED_VERIFY'})
        for report in result['records']['reports']:
            row=one('SELECT r.id,r.cut_count,r.field_weight_kg,e.raw_text FROM harvest.worker_reports r JOIN harvest.evidence e ON e.id=r.evidence_id WHERE r.id=:id',id=UUID(report))
            assert row['cut_count']==7 and row['field_weight_kg']==70 and 'field original seven cuts' in row['raw_text']
            proof['reports'].append(row)
        for settlement in result['records']['settlements']:
            row=one('SELECT s.id,s.status,s.obligation_id,s.total_amount,e.raw_text,octet_length(e.file_bytes) original_bytes FROM harvest.depot_settlements s JOIN harvest.evidence e ON e.id=s.evidence_id WHERE s.id=:id',id=UUID(settlement))
            assert row['status']=='posted' and row['original_bytes']>0 and 'separate owner caption' in row['raw_text']
            assert scalar('SELECT count(*) FROM finance.obligations WHERE id=:id',id=row['obligation_id'])==1
            proof['settlements'].append(row)
        for task in set(result['records']['tasks']):
            row=one('SELECT id,state,version FROM core.owner_tasks WHERE id=:id',id=UUID(task))
            assert row['state']=='TO_DO' and row['version']>=3
            assert scalar('SELECT count(*) FROM core.owner_task_actions WHERE task_id=:id',id=row['id'])>=2
        for task in set(result['records']['workerTasks']):
            assignments=[dict(x) for x in session.execute(text('SELECT * FROM workers.assignments WHERE task_id=:id'),{'id':UUID(task)}).mappings()]
            assert len(assignments)==1
            assert all(x['rendering_reviewed'] is True and x['rendered_text'] and x['language'] for x in assignments)
            for assignment in assignments:
                worker=one('SELECT contact,telegram_ref FROM workers.people WHERE id=:id',id=assignment['worker_id'])
                assert worker['contact'] is None and worker['telegram_ref'] is None
            task_row=one('SELECT status,original_instruction FROM workers.tasks WHERE id=:id',id=UUID(task))
            assert task_row['status']=='assigned' and task_row['original_instruction']
            assert scalar('SELECT count(*) FROM workers.actions WHERE task_id=:id',id=UUID(task))>=1
        proof['personal_and_worker_state']='PASS; original tasks retained, state transitions durable; one reviewed internal assignment; worker contact/Telegram absent. No external dispatch is an architecture/runtime boundary, not a nonexistent sent_at assertion.'
        proof['role']=one('SELECT current_user role,current_database() database,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        entity=proof['payments'][0]['entity_id']
        assert scalar('SELECT count(*) FROM finance.obligations WHERE entity_id=:e',e=entity)==2+len(result['records']['settlements'])
        assert scalar('SELECT count(*) FROM finance.payments WHERE entity_id=:e',e=entity)==len(result['records']['payments'])
        assert scalar('SELECT count(*) FROM finance.advances WHERE entity_id=:e',e=entity)==0
        remaining=[dict(x) for x in session.execute(text("SELECT o.direction,b.remaining_amount FROM finance.obligations o JOIN finance.obligation_balances b ON b.obligation_id=o.id WHERE o.entity_id=:e AND o.economic_source!='harvest_depot'"),{'e':entity}).mappings()]
        assert len(remaining)==2 and all(x['remaining_amount']==0 for x in remaining)
        proof['settled_obligations']=remaining
        proof['journal_balanced']=scalar("SELECT count(*)=0 FROM (SELECT posting_id,sum(CASE WHEN side='debit' THEN amount ELSE -amount END) delta FROM finance.posting_entries pe JOIN finance.postings pp ON pp.id=pe.posting_id WHERE pp.entity_id=:e GROUP BY posting_id HAVING sum(CASE WHEN side='debit' THEN amount ELSE -amount END)<>0) x",e=entity)
        assert proof['journal_balanced']
    from greenhouse_owner_os.services.inventory import InventoryService
    inventory=InventoryService(lambda:SqlAlchemyUnitOfWork(factory)).overview(entity)
    assert inventory['opening_state']=='UNINITIALIZED_VERIFY'
    assert inventory['invariants']['ok']
    proof['inventory_opening_and_invariants']={'opening_state':inventory['opening_state'],'invariants':inventory['invariants']}
    service=FinanceService(lambda:SqlAlchemyUnitOfWork(factory))
    proof['reconciliation']=service.reconcile(include_classification=True,entity_id=entity)
    assert proof['reconciliation']['ok']
    proof['status']='PASS'
    Path(sys.argv[2]).write_text(json.dumps(proof,ensure_ascii=False,indent=2,default=str))
    print(json.dumps({'status':'PASS','expenses':len(proof['expenses']),'payments':len(proof['payments']),'receipts':len(proof['receipts']),'reports':len(proof['reports']),'settlements':len(proof['settlements'])}))


if __name__=='__main__':main()
