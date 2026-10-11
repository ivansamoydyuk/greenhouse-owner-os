"""Multi-token identity prefixes never authorize a worker prefill."""
from datetime import date
from uuid import UUID
import pytest

from greenhouse_owner_os.services.owner_preparation import prepare_owner_source
from greenhouse_owner_os.services.preparation_language import entity_matches

DAY = date(2026, 10, 9)


def worker(number, name, native=None, aliases=()):
    return dict(id=str(UUID(int=number)), display_name=name, native_name=native,
                language='fa', aliases=list(aliases), active=True)


@pytest.mark.parametrize('name,prefix', [('TEST Muhammed', 'TEST'), ('Team Hasan', 'Team'), ('North Ali', 'North'), ('Асыф Газипаша', 'Асыф')])
def test_unique_partial_name_is_not_an_explicit_worker_identity(name, prefix):
    refs = [worker(1, name)]
    raw = prefix + ' · Проверить давление'
    assert entity_matches(raw, refs, 'worker', inflect=True, require_explicit=True) == []
    slots = prepare_owner_source(raw, today=DAY, refs=dict(workers=refs))
    assert slots and all(p.get('worker_id') is None and p.get('language') is None for _, p, _ in slots)
    assert any(p.get('original_instruction') == raw for _, p, _ in slots)


@pytest.mark.parametrize('raw', ['Асыф завтра проверить давление', 'Асыф и Умар завтра проверить давление', 'TEST и Умар завтра проверить давление'])
def test_ambiguous_or_generic_prefix_cannot_select_another_known_worker(raw):
    refs = [worker(1, 'Асыф Газипаша'), worker(2, 'Асыф Алания'), worker(3, 'Умар'), worker(4, 'TEST Muhammed')]
    slots = prepare_owner_source(raw, today=DAY, refs=dict(workers=refs))
    assert slots and all(p.get('worker_id') is None and p.get('language') is None for _, p, _ in slots)
    if 'Умар' in raw:
        assert all(kind == 'WORKER_TASK' for kind, _, _ in slots), 'Explicit named worker preserves audience while assignee stays unknown'


@pytest.mark.parametrize('mention', ['TEST Muhammed', 'TEST محمد', 'Мухаммед'])
def test_complete_registered_names_and_explicit_aliases_remain_eligible(mention):
    refs = [worker(1, 'TEST Muhammed', 'TEST محمد', aliases=['Мухаммед'])]
    raw = mention + ', завтра проверить давление'
    assert entity_matches(raw, refs, 'worker', inflect=True, require_explicit=True) == refs
    slots = prepare_owner_source(raw, today=DAY, refs=dict(workers=refs))
    assert len(slots) == 1 and slots[0][0] == 'WORKER_TASK'
    assert slots[0][1]['worker_id'] == refs[0]['id'] and slots[0][1]['language'] == 'fa'


def test_qualified_name_suppresses_only_competing_prefix_at_the_same_mention():
    refs = [worker(1, 'Асыф Газипаша'), worker(2, 'Асыф Алания')]
    assert entity_matches('Асыф Газипаша проверить давление', refs, 'worker', inflect=True, require_explicit=True) == refs[:1]
    assert entity_matches('Асыф Газипаша и Асыф проверить давление', refs, 'worker', inflect=True, require_explicit=True) == []


@pytest.mark.parametrize('mention', ['Умар', 'Umar'])
def test_exact_single_word_registered_name_remains_valid(mention):
    refs = [worker(1, 'Умар', 'Umar')]
    slots = prepare_owner_source(mention + ' завтра проверить давление', today=DAY, refs=dict(workers=refs))
    assert slots[0][0] == 'WORKER_TASK' and slots[0][1]['worker_id'] == refs[0]['id']


def test_shared_explicit_alias_and_inactive_identity_remain_unbound():
    refs = [worker(1, 'Hasan Kaya', aliases=['Хасан']), worker(2, 'Hasan Demir', aliases=['Хасан'])]
    slots = prepare_owner_source('Хасан завтра проверить давление', today=DAY, refs=dict(workers=refs))
    assert slots[0][1]['worker_id'] is None and not slots[0][1].get('language')
    refs[0]['active'] = refs[1]['active'] = False
    assert entity_matches('Hasan Kaya', refs, 'worker', inflect=True, require_explicit=True) == []
