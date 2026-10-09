"""Benchmark integrity tests: assertions are not parser-derived labels."""
from copy import deepcopy
from functools import lru_cache
from datetime import date,timedelta
import hashlib,json,re
from types import SimpleNamespace
from uuid import UUID
import pytest
from sqlalchemy import select,text
from scripts.owner_language_benchmark import (
    CORPUS,ReferenceSession,ident,load,prepare_case,reference_fixture,score,validate_case,safety_reasons,permitted_scoped_quality_candidate,
)
from scripts.generate_owner_language_variants import AREAS,TIME,WORKERS,replace_literal,semantic_variants,noise_variants,exclusion_reasons

@lru_cache(maxsize=1)
def generated():
    train=load(CORPUS/'train.jsonl')
    return semantic_variants(train,5200),noise_variants(train,5200)


def case(expected,**kw):
    return dict(id='scoring-test',family='test',tier='gold',text='test',expected=expected,**kw)


def test_missing_slot_keeps_every_assertion_denominator_and_is_not_unresolved():
    c=case([dict(kind='WORKER_TASK',fields={'worker_id':{'ref':'umar'}},unknown=['greenhouse_id'],preserve=['do this'])])
    r=score(c,[])
    assert r['field_total']==3 and r['field_correct']==0
    assert not r['safely_unresolved'] and not r['ambiguity_safe']


def test_wrong_present_worker_amount_and_currency_are_factual_safety_failures():
    c=case([dict(kind='PAYMENT',fields={'amount':'5000','currency':'TRY'},unknown=['counterparty_id'])],ambiguity='party')
    r=score(c,[('PAYMENT',{'amount':'7000','currency':'USD','counterparty_id':ident('serkan')},{})])
    assert len(r['invented'])==3 and not r['ambiguity_safe']


def test_unassessed_emitted_critical_fact_is_explicitly_not_credited_as_safe():
    c=case([dict(kind='PERSONAL_TASK',fields={'planned_date':'2026-10-10'})],ambiguity='geography')
    r=score(c,[('PERSONAL_TASK',{'planned_date':'2026-10-10','greenhouse_id':ident('imamli')},{})])
    assert r['unassessed_critical_facts']==[dict(kind='PERSONAL_TASK',field='greenhouse_id')]
    assert not r['ambiguity_safe']


def test_duplicate_authoritative_slot_is_unsafe_even_when_one_matched():
    c=case([dict(kind='PERSONAL_TASK',fields={})])
    r=score(c,[('PERSONAL_TASK',{},{}),('PERSONAL_TASK',{},{})])
    assert not r['route_ok'] and r['unsafe']==['PERSONAL_TASK:UNMATCHED_SLOT']


def test_partial_unknown_remainder_is_not_credited_by_primary_description():
    c=case([dict(kind='PAYMENT',fields={})],remainder=['насос сломался'])
    r=score(c,[('PAYMENT',{'description':'насос сломался; заплатил'}, {})])
    assert not r['remainder_ok']


def test_fixture_filters_entity_active_id_and_mapping_predicates_fail_closed():
    from greenhouse_owner_os.infrastructure.models import CounterpartyRow
    refs=reference_fixture();good=refs['orm'][CounterpartyRow][0]
    foreign=deepcopy(good);foreign.entity_id=UUID(ident('other-entity'));foreign.id=UUID(ident('foreign-serkan'))
    inactive=deepcopy(good);inactive.active=False;inactive.id=UUID(ident('inactive-serkan'))
    refs['orm'][CounterpartyRow]+=[foreign,inactive]
    session=ReferenceSession(refs)
    q=select(CounterpartyRow).where(CounterpartyRow.entity_id==UUID(ident('entity')),CounterpartyRow.active.is_(True),CounterpartyRow.id==good.id)
    assert session.scalars(q)==[good]
    refs['sql']['harvest.depot_quality_mappings']=[dict(depot_id='a',original_label='grand',version=1),dict(depot_id='a',original_label='grand',version=2),dict(depot_id='b',original_label='grand',version=9)]
    result=session.execute(text('SELECT * FROM harvest.depot_quality_mappings WHERE depot_id=:d AND original_label=:l ORDER BY version DESC LIMIT 1'),dict(d='a',l='grand')).mappings()
    assert len(result)==1 and result[0]['version']==2
    with pytest.raises(AssertionError):session.execute(text('DELETE FROM workers.people'))
    with pytest.raises(AssertionError):session.execute(text('SELECT * FROM workers.people WHERE entity_id=:e OR active=false'),dict(e=ident('entity')))


def test_training_gold_labels_are_frozen_and_history_provenance_is_separate():
    train=load(CORPUS/'train.jsonl');manifest=json.loads((CORPUS/'manifest.json').read_text())
    assert hashlib.sha256((CORPUS/'train.jsonl').read_bytes()).hexdigest()==manifest['train_sha256']
    assert len(train)==manifest['train_gold']
    assert all(c['split']=='train' and c['tier']=='gold' for c in train)
    assert len({c['id'] for c in train})==len(train)
    assert len({c['text'] for c in train})==len(train)
    assert all(c['expected'] for c in train)
    assert manifest['holdout_gold']>=manifest['gold_total']*.20
    assert re.fullmatch('[0-9a-f]{64}',manifest['holdout_sha256'])
    forbidden={'source_message_url','source_seed_id','source_seed_ids','source_created_at','owner_authorship','source_template_group'}
    for c in train:
        assert c['origin']=='curated_test' and c['test_only'] is True
        assert re.fullmatch(r'GOLD-\d{4}',c['id'])
        assert re.fullmatch(r'GROUP-\d{4}',c['semantic_group'])
        assert not(forbidden&set(c))
        assert not re.search(r'https://(?:chatgpt\.com/c/|docs\.google\.com/document/d/)',json.dumps(c))
    # Holdout bytes/text/labels are deliberately absent during implementation.
    if manifest.get('holdout_custody') in {'EMBARGOED_OUTSIDE_REPOSITORY','FIRST_EVALUATED_RELEASED_LOCALLY_PUBLICATION_PENDING'}:
        assert not (CORPUS/'holdout.jsonl').exists()
        if manifest['holdout_custody']=='FIRST_EVALUATED_RELEASED_LOCALLY_PUBLICATION_PENDING':
            assert manifest['holdout_run'] is True
            assert manifest['subsequent_same_cases_status']=='EXPOSED_REGRESSION_ONLY'
    else:
        assert manifest.get('holdout_custody')=='RELEASED_AFTER_INDEPENDENT_EVALUATION'
        assert manifest['holdout_run'] is True
        assert hashlib.sha256((CORPUS/'holdout.jsonl').read_bytes()).hexdigest()==manifest['holdout_sha256']


def test_report_cannot_replace_first_unseen_outcome_with_exposed_regression():
    from scripts.report_owner_language_benchmark import validate_first_holdout
    manifest={'first_holdout_evaluation':{'report_sha256':'sealed-original'}}
    validate_first_holdout(manifest,'sealed-original')
    with pytest.raises(ValueError,match='exposed regression'):
        validate_first_holdout(manifest,'improved-rerun')


def test_semantic_variants_reverse_exactly_and_change_only_declared_expected_facts():
    train={c['id']:c for c in load(CORPUS/'train.jsonl')}
    variants=generated()[0]
    assert len(variants)>=5000
    assert len({c['text'] for c in variants})==len(variants)
    for c in variants:
        base=train[c['parent_id']];rebuilt=deepcopy(base);changes=[]
        expected=deepcopy(base['expected'])
        assert c['split']=='train' and c['tier']=='semantic_variant'
        assert len(expected)==len(c['expected'])==1
        for tr in c['transforms']:
            old,new=tr['old'],tr['new']
            assert tr['method']=='exact_single_bounded_token'
            assert replace_literal(rebuilt,old,new,changes)
            fields=expected[0]['fields']
            changed=False
            for k,v in list(fields.items()):
                if k in ('amount','lines.0.quantity') and isinstance(v,str) and v==old.replace(',','.'):
                    fields[k]=new;changed=True
                elif k=='worker_id' and v.get('ref') in WORKERS and WORKERS[v['ref']].casefold()==old.casefold():
                    fields[k]={'ref':next(key for key,value in WORKERS.items() if value==new)};changed=True
                elif k=='greenhouse_id' and v.get('ref') in AREAS and AREAS[v['ref']].casefold()==old.casefold():
                    fields[k]={'ref':next(key for key,value in AREAS.items() if value==new)};changed=True
            if old in TIME:
                for key in ('planned_date','event_date'):
                    if key in fields:fields[key]=(date.fromisoformat(base['capture_day'])+timedelta(days=TIME[new])).isoformat();changed=True
            assert changed,(c['id'],tr)
        expected[0]['preserve']=rebuilt['expected'][0].get('preserve',[]) if 'preserve' in expected[0] else None
        if expected[0]['preserve'] is None:expected[0].pop('preserve')
        assert rebuilt['text']==c['text']
        assert expected==c['expected'],c['id']
        assert base.get('forbid_kinds')==c.get('forbid_kinds')


def test_noise_keeps_negation_numbers_and_source_order_and_expected_labels():
    train={c['id']:c for c in load(CORPUS/'train.jsonl')};variants=generated()[1]
    assert len(variants)>=5000
    for c in variants:
        base=train[c['parent_id']]
        mode=c['transforms'][1]['type'];prefix=c['transforms'][0]['value']
        text=prefix+base['text']
        if mode=='double_spaces':text=text.replace(' ','  ')
        elif mode=='lowercase':text=text.casefold()
        assert text==c['text']
        assert re.findall(r'\d+',base['text'])==re.findall(r'\d+',text)
        assert re.findall(r'(?i)\b(?:не|нет|никогда|пока)\b',base['text'].casefold())==re.findall(r'(?i)\b(?:не|нет|никогда|пока)\b',text.casefold())
        expected=deepcopy(base['expected'])
        for e in expected:
            if mode=='double_spaces' and e.get('preserve'):e['preserve']=[s.replace(' ','  ') for s in e['preserve']]
            if mode=='lowercase':
                for key,value in list(e.get('fields',{}).items()):
                    if key.endswith('original_quality_name') and isinstance(value,str):e['fields'][key]=value.casefold()
        assert expected==c['expected']


def test_fast_harness_invokes_real_preparation_and_text_voice_are_equivalent(monkeypatch):
    from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
    called=[];original=OwnerCaptureService.prepare
    def traced(self,*args,**kwargs):called.append(True);return original(self,*args,**kwargs)
    monkeypatch.setattr(OwnerCaptureService,'prepare',traced)
    c=next(c for c in load(CORPUS/'train.jsonl') if c['id']=='GOLD-0373')
    assert prepare_case(c)==prepare_case(c,'voice_transcript')
    assert len(called)>=2


def test_label_paths_cannot_pass_vacuously_on_nonexistent_dto_fields():
    bad=case([dict(kind='PAYABLE',fields={},unknown=['due'])])
    with pytest.raises(AssertionError,match='Invalid label path'):validate_case(bad)
    for c in load(CORPUS/'train.jsonl'):validate_case(c)


def test_generated_temporal_changes_never_turn_actual_events_into_plans():
    for c in generated()[0]:
        kind=c['expected'][0]['kind']
        if kind not in ('PERSONAL_TASK','WORKER_TASK'):
            for change in c['transforms']:
                if change['old'] in TIME:
                    assert change['new'] in ('сегодня','вчера')


def test_semantic_transform_scope_preserves_units_currencies_and_slot_count():
    parents={c['id']:c for c in load(CORPUS/'train.jsonl')}
    for c in generated()[0]:
        parent=parents[c['parent_id']]
        assert not exclusion_reasons(parent)
        assert len(c['expected'])==len(parent['expected'])==1
        assert [e['kind'] for e in c['expected']]==[e['kind'] for e in parent['expected']]
        for key,val in parent['expected'][0].get('fields',{}).items():
            if key.endswith('.unit') or key in ('currency','money_account_id'):
                assert c['expected'][0]['fields'][key]==val
        validate_case(c)

def test_corrections_and_multi_events_only_get_semantics_preserving_noise():
    parents={c['id']:c for c in load(CORPUS/'train.jsonl')}
    semantic_parents={c['parent_id'] for c in generated()[0]}
    for parent in parents.values():
        if len(parent['expected'])>1 or re.search(r'(?i)\b(?:не|нет)\b',parent['text']):
            assert parent['id'] not in semantic_parents
    for c in generated()[1]:
        parent=parents[c['parent_id']]
        assert len(c['expected'])==len(parent['expected'])
        assert [e['kind'] for e in c['expected']]==[e['kind'] for e in parent['expected']]
        # Removing only the declared prefix and whitespace/case noise leaves
        # original correction order and every clause unchanged.
        assert c['text'].casefold().split()[len(c['transforms'][0]['value'].split()):]==parent['text'].casefold().split()
        validate_case(c)


def test_safety_gate_does_not_hide_coverage_gaps_or_allow_unsafe_fields():
    c=case([dict(kind='PERSONAL_TASK',fields={'planned_date':'2026-10-10'})])
    safe=score(c,[('UNRESOLVED',{'note':'source retained'}, {})])
    assert not safe['route_ok'] and not safe['key_fields_ok']
    assert safety_reasons(safe)==[]
    wrong=score(c,[('PERSONAL_TASK',{'planned_date':'2026-10-12'}, {})])
    assert 'contradictory_or_filled_unknown_fact' in safety_reasons(wrong)
    assert 'empty_preparation_output' in safety_reasons(score(c,[]))


COMMON_TRAIN_IDS=[
 'GOLD-0403','GOLD-0461',
 'GOLD-0473','GOLD-0479',
 'GOLD-0373','GOLD-0087',
 'GOLD-0160','GOLD-0168',
 'GOLD-0033','GOLD-0387',
]

@pytest.mark.parametrize('case_id',COMMON_TRAIN_IDS)
def test_common_clear_training_scenarios_remain_explicit_regressions(case_id):
    c=next(c for c in load(CORPUS/'train.jsonl') if c['id']==case_id)
    result=score(c,prepare_case(c))
    assert result['route_ok'] and result['key_fields_ok'],result
    assert not result['unsafe'] and not result['invented'],result


def test_equal_facts_in_two_labelled_source_events_are_not_replay_duplicates():
    event=dict(kind='PAYMENT',fields={'amount':'5000','currency':'TRY'},unknown=['counterparty_id'])
    c=case([deepcopy(event),deepcopy(event)])
    result=score(c,[('PAYMENT',{'amount':'5000','currency':'TRY'},{}),('PAYMENT',{'amount':'5000','currency':'TRY'},{})])
    assert result['route_ok'] and result['slots_covered']==2
    assert not result['unsafe'] and not result['invented']

def test_depot_quality_candidates_and_inventory_cost_cannot_escape_assessment():
    depot=case([dict(kind='DEPOT_RECEIPT',fields={'lines.0.original_quality_name':'Grand'},unknown=[])])
    result=score(depot,[('DEPOT_RECEIPT',{'lines':[{'original_quality_name':'Grand'}],'qualities':['SECOND']},{})])
    assert {'kind':'DEPOT_RECEIPT','field':'qualities.0'} in result['unassessed_critical_facts']
    assert safety_reasons(result)
    stock=case([dict(kind='STOCK_RECEIPT',fields={},unknown=[])])
    result=score(stock,[('STOCK_RECEIPT',{'lines':[{'unit_cost':'0'}]},{})])
    assert {'kind':'STOCK_RECEIPT','field':'lines.0.unit_cost'} in result['unassessed_critical_facts']


def test_scoped_quality_oracle_requires_verified_depot_original_and_inference():
    expected={'kind':'DEPOT_RECEIPT','fields':{'depot_id':{'ref':'gorgun'},'lines.0.original_quality_name':'Grand'}}
    payload={'depot_id':ident('gorgun'),'lines':[{'original_quality_name':'Grand'}],'qualities':['HIGHEST'],'quality_reviewed':False}
    provenance={'category':'INFERENCE'}
    assert permitted_scoped_quality_candidate(expected,payload,provenance,'qualities.0','HIGHEST')
    assert payload['lines'][0]['original_quality_name']=='Grand'
    assert not permitted_scoped_quality_candidate(expected,payload,provenance,'qualities.0','SECOND')
    assert not permitted_scoped_quality_candidate(expected,payload,{'category':'SOURCE FACT'},'qualities.0','HIGHEST')
    assert not permitted_scoped_quality_candidate(expected,payload,{'category':'LEARNED DEFAULT'},'qualities.0','HIGHEST')
    assert not permitted_scoped_quality_candidate(expected,dict(payload,depot_id=ident('other-depot')),provenance,'qualities.0','HIGHEST')
    assert not permitted_scoped_quality_candidate(expected,dict(payload,quality_reviewed=True),provenance,'qualities.0','HIGHEST')
    assert not permitted_scoped_quality_candidate(expected,dict(payload,lines=[{'original_quality_name':'Yerli'}]),provenance,'qualities.0','HIGHEST')
