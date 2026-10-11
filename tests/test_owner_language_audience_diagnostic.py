import pytest

from scripts import diagnose_owner_language_audience as diagnostic
overlay={'diagnostic_task_kinds':['PERSONAL_TASK','WORKER_TASK'],'required_unknown_fields':['worker_id','greenhouse_id','planned_date']}
case={'id':'diagnostic-test','family':'test','text':'Проверить фитинги.'}

def result(kind='PERSONAL_TASK',**payload):
    return diagnostic.assess_ambiguous_task(case,[(kind,{'original_instruction':case['text'],**payload},{})],overlay)

@pytest.mark.parametrize('kind',['PERSONAL_TASK','WORKER_TASK'])
def test_safe_task_family_is_retained_without_crediting_audience(kind):
    r=result(kind)
    assert r['audience_classification']=='NOT_DETERMINABLE'
    assert r['task_family_retained'] and not r['safety_violations'] and r['field_correct']==4

@pytest.mark.parametrize('field,value',[('worker_id','invented'),('greenhouse_id','invented'),('planned_date','2026-10-10'),('assignment_id','invented'),('assigned_at','now'),('language','fa'),('rendered_text','invented translation'),('translation_reviewed',True),('rendering_reviewed',True),('status','ASSIGNED')])
def test_ambiguity_never_excuses_fabricated_facts_or_authority(field,value):
    assert result(**{field:value})['safety_violations']

def test_wrong_effect_duplicate_empty_and_source_loss_remain_unsafe():
    assert result('PAYMENT')['safety_violations']
    slot=('PERSONAL_TASK',{'original_instruction':case['text']},{})
    assert 'duplicate_task_proposals' in diagnostic.assess_ambiguous_task(case,[slot,slot],overlay)['safety_violations']
    assert diagnostic.assess_ambiguous_task(case,[],overlay)['safety_violations']
    assert result(original_instruction='different')['safety_violations']

def test_unresolved_is_coverage_gap_not_successful_understanding():
    r=result('UNRESOLVED')
    assert r['safely_unresolved'] and not r['task_family_retained'] and r['coverage_gap']
    assert r['field_correct']==0 and not r['safety_violations']

def test_source_in_note_does_not_excuse_different_action_instruction():
    r=result(original_instruction='Удалить данные.',note=case['text'],title=case['text'])
    assert 'source_instruction_lost' in r['safety_violations']
    assert r['coverage_gap']

def test_strict_never_passes_when_safety_integrity_or_parity_failed():
    report={'diagnostic_coverage_gate':{'passed':True},'diagnostic_safety_gate':{'passed':False}}
    assert diagnostic.exit_code(report,strict=True)==1
    assert diagnostic.exit_code(report,require_safety=True)==1
