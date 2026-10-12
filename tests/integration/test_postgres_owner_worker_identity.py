"""Persisted text/voice identity guards without worker assignment or posting."""
from uuid import UUID, uuid4
from sqlalchemy import text
from tests.integration.test_postgres_inventory import inv
from tests.integration.test_postgres_readiness import preview
from tests.integration.test_postgres_owner_capture import source, queue
from greenhouse_owner_os.domain.workers import WorkerInput
from greenhouse_owner_os.repositories.sqlalchemy import SqlAlchemyUnitOfWork
from greenhouse_owner_os.services.workers import WorkersService
from greenhouse_owner_os.services.owner_capture import OwnerCaptureService


def test_worker_identity_requires_explicit_registered_evidence_in_both_capture_modes(inv):
    sf, actor, *_ = inv['preview']
    factory = lambda: SqlAlchemyUnitOfWork(sf)
    service = WorkersService(factory)
    names = [('TEST Muhammed', 'TEST محمد'), ('Team Hasan', None), ('Асыф Газипаша', None), ('Асыф Алания', None), ('Умар', 'Umar')]
    workers = {name: service.worker(UUID(inv['e']), actor, str(uuid4()), WorkerInput(display_name=name, native_name=native, language='fa', source='test:identity')) for name, native in names}
    cases = [
        ('TEST · Проверить давление', None, 'UNRESOLVED'),
        ('Team завтра проверить давление', None, 'UNRESOLVED'),
        ('Асыф завтра проверить давление', None, 'WORKER_TASK'),
        ('Асыф и Умар завтра проверить давление', None, 'WORKER_TASK'),
        ('TEST и Умар завтра проверить давление', None, 'WORKER_TASK'),
        ('TEST Muhammed завтра проверить давление', workers['TEST Muhammed']['id'], 'WORKER_TASK'),
        ('TEST محمد завтра проверить давление', workers['TEST Muhammed']['id'], 'WORKER_TASK'),
        ('Асыф Газипаша завтра проверить давление', workers['Асыф Газипаша']['id'], 'WORKER_TASK'),
        ('Завтра Асыф Газипаша починит плёнку в Özvadi.', workers['Асыф Газипаша']['id'], 'WORKER_TASK'),
        ('Умар завтра проверить давление', workers['Умар']['id'], 'WORKER_TASK'),
    ]
    capture_service = OwnerCaptureService(factory)
    for raw, expected_worker, expected_kind in cases:
        by_mode = []
        for mode in ('text', 'voice_transcript'):
            saved = source(inv, raw, mode=mode)
            proposals = [p for p in queue(inv) if p['inbox_id'] == saved['id']]
            assert len(proposals) == 1
            assert proposals[0]['action_type'] == expected_kind, raw
            payload = proposals[0]['revision']['payload']
            if expected_kind == 'UNRESOLVED':
                assert proposals[0]['state'] == 'NEEDS_INPUT'
                expected_missing = ['Для себя или работнику?']
                if mode == 'voice_transcript':
                    expected_missing.append('Возможный повтор источника: проверьте оригинал')
                assert proposals[0]['revision']['missing'] == expected_missing
                assert proposals[0]['revision']['provenance']['task_candidates']['basis'] == 'task_audience_requires_owner_choice'
                task = payload['task_candidates']
            else:
                task = payload
            assert task.get('worker_id') == expected_worker
            assert task.get('language') == ('fa' if expected_worker else None)
            assert task['original_instruction'] == raw
            assert task['title'] == raw
            assert capture_service.original(UUID(inv['e']), actor, UUID(saved['id']))[0].decode() == raw
            by_mode.append((proposals[0]['action_type'], payload))
        assert by_mode[0] == by_mode[1]
    with sf() as db:
        for table in ('core.confirmation_effects', 'finance.postings', 'inventory.movements', 'workers.assignments'):
            assert db.scalar(text('SELECT count(*) FROM '+table+' WHERE entity_id=:e'), dict(e=inv['e'])) == 0
