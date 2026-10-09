"""Owner-language benchmark against the real R2 preparation service.

This module supplies read-only reference rows, not a replacement parser. The
same OwnerCaptureService.prepare used after source persistence is called. The
PostgreSQL integration companion verifies that boundary with real source jobs.
Gold expectations are authored before extraction, never inferred from outputs.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import tempfile
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5, NAMESPACE_URL

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / 'tests/benchmarks/owner-language'
DAY = date(2026, 10, 9)


def ident(key):
    return str(uuid5(NAMESPACE_URL, 'TEST:owner-language-benchmark:' + key))


def reference_fixture():
    """Entirely disposable TEST references; history never imports production state."""
    from greenhouse_owner_os.infrastructure.models import CounterpartyRow, WorkAreaRow, MoneyAccountRow
    def ref(key, **kw):
        return SimpleNamespace(id=UUID(ident(key)), entity_id=UUID(ident('entity')), active=True, **kw)
    parties = [ref(k, name=n, counterparty_type=r) for k,n,r in [
        ('serkan','Серкан','supplier'), ('kemal','Kemal Bey','supplier'),
        ('asat','ASAT','supplier'), ('ck','CK Akdeniz Elektrik','supplier'),
        ('telekom','Türk Telekom','supplier'), ('laben','LABEN','supplier'),
        ('simsek','Şimşek Makina','supplier'), ('gorgun','Görgün Depo','depot'),
        ('umar_party','Умар','supplier'), ('daylabor','Дневные рабочие','supplier'),
        ('ahmet','Ахмет','supplier')]]
    areas = [ref(k, name=n, kind='greenhouse') for k,n in [('imamli','İmamlı'),('ozvadi','Özvadi'),('yesiloz','Yeşilöz')]]
    accounts = [ref(k,name=n,currency=c,account_type=t) for k,n,c,t in [
        ('cash','Cash TR','TRY','cash'),('bank','Bank TR','TRY','bank'),('cash_usd','Cash USD','USD','cash')]]
    workers = [dict(id=ident(k),entity_id=ident('entity'),display_name=n,native_name=nn,language=lang,active=True,default_greenhouse_id=ident(g)) for k,n,nn,lang,g in [
        ('umar','Умар','Umar','fa','yesiloz'),('muhammad','Мухаммед','Muhammad','fa','imamli'),
        ('inayat','Инаят','Inayat','fa','imamli'),('asif_g','Асыф Газипаша','Asif Gazipasa','fa','ozvadi'),
        ('asif_a','Асыф Алания','Asif Alanya','fa','yesiloz')]]
    products = [dict(id=ident(k),entity_id=ident('entity'),name=n,aliases=aliases,base_unit=unit,active=True) for k,n,aliases,unit in [
        ('kno3','KNO3',['калийная селитра','калийной селитры'],'kg'),
        ('multilex','Multilex',['Мультилекс','Мульти лекс'],'L'),('atocrop','Atocrop',['Атокроп'],'L'),
        ('potassium_sulfate','Сульфат калия',['сернокислый калий','potassium sulfate'],'kg'),
        ('trichoderma','Trichoderma',['Триходерма'],'kg'),('organic','Органик',['органическое удобрение'],'L')]]
    locations = [dict(id=ident(k),entity_id=ident('entity'),name=n,active=True) for k,n in [('store_a','TEST A'),('store_b','TEST B')]]
    return dict(orm={CounterpartyRow:parties,WorkAreaRow:areas,MoneyAccountRow:accounts}, sql={
        'inventory.products':products,'inventory.locations':locations,'workers.people':workers,
        'harvest.depot_quality_mappings':[], 'finance.obligations':[]})


class ReferenceSession:
    """Read-only DB boundary double, raising on any unsupported or write query."""
    def __init__(self, fixture): self.fixture=fixture
    def scalars(self, query):
        from sqlalchemy.sql import operators
        model=query.column_descriptions[0]['entity']
        if model not in self.fixture['orm']: raise AssertionError(f'Unsupported reference model: {model}')
        def matches(value,expression):
            if hasattr(expression,'clauses'):
                if expression.operator is not operators.and_:raise AssertionError('Unsupported ORM boolean reference predicate')
                return all(matches(value,x) for x in expression.clauses)
            key=getattr(expression.left,'key',None)
            right=expression.right
            if hasattr(right,'value'):want=right.value
            elif str(right)=='true':want=True
            elif str(right)=='false':want=False
            elif str(right)=='NULL':want=None
            else:raise AssertionError('Unsupported ORM reference value')
            got=getattr(value,key)
            if expression.operator in (operators.eq,operators.is_):return str(got)==str(want)
            if expression.operator in (operators.ne,operators.is_not):return str(got)!=str(want)
            raise AssertionError('Unsupported ORM reference operator')
        values=[v for v in self.fixture['orm'][model] if all(matches(v,c) for c in query._where_criteria)]
        if query._order_by_clauses or query._limit_clause is not None:raise AssertionError('Unsupported ORM reference ordering/limit')
        return values
    def execute(self, query, params=None):
        sql=' '.join(str(query).strip().split());params=params or {}
        if not sql.upper().startswith('SELECT '): raise AssertionError('Preparation tried to write through read-only fixture')
        tablematch=re.fullmatch(r'SELECT \* FROM ([\w.]+) WHERE (.+?)(?: ORDER BY (\w+) (ASC|DESC))?(?: LIMIT (\d+))?',sql,re.I)
        if not tablematch:raise AssertionError(f'Unsupported preparation SQL shape: {sql}')
        table,where,order,direction,limit=tablematch.groups()
        if table not in self.fixture['sql']:raise AssertionError(f'Unknown reference table: {table}')
        result=list(self.fixture['sql'][table])
        for clause in re.split(r'\s+AND\s+',where,flags=re.I):
            match=re.fullmatch(r'(\w+)\s*=\s*:(\w+)',clause)
            if match:
                key,param=match.groups()
                if param not in params:raise AssertionError('Missing bound reference parameter')
                result=[r for r in result if str(r.get(key))==str(params[param])]
            elif re.fullmatch(r'active\s+(?:IS|=)\s+true',clause,re.I):result=[r for r in result if r.get('active',True)]
            else:raise AssertionError(f'Unsupported reference predicate: {clause}')
        if order:result.sort(key=lambda r:r[order],reverse=direction.upper()=='DESC')
        if limit:result=result[:int(limit)]
        return SimpleNamespace(mappings=lambda:result)



def prepare_case(case, mode='text', fixture=None):
    from greenhouse_owner_os.services.owner_capture import OwnerCaptureService
    refs=deepcopy(fixture) if fixture is not None else reference_fixture()
    for key in case.get('omit_references', []):
        target=ident(key)
        for model, values in refs['orm'].items(): refs['orm'][model]=[v for v in values if str(v.id)!=target]
        for table, values in refs['sql'].items(): refs['sql'][table]=[v for v in values if str(v.get('id'))!=target]
    for key in case.get('inactive_references', []):
        target=ident(key)
        for values in refs['orm'].values():
            for v in values:
                if str(v.id)==target:v.active=False
        for values in refs['sql'].values():
            for v in values:
                if str(v.get('id'))==target:v['active']=False
    source=dict(id=UUID(ident(case['id'])),e=UUID(case.get('entity_id',ident('entity'))),a=UUID(ident('owner')),
                binary=None,day=date.fromisoformat(case.get('capture_day',DAY.isoformat())),mode=mode,
                raw=case['text'],duplicate=False)
    out=dict(text=case['text'],pages=[],warnings=[],provider='test-source-text')
    service=OwnerCaptureService(lambda:None)
    return service.prepare(SimpleNamespace(session=ReferenceSession(refs)),source,out)


def load(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def write_json(path, value):
    atomic_write(path,json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def atomic_write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,prefix=path.name+'.',delete=False) as output:
        output.write(data);name=output.name
    os.replace(name,path)


def dig(value, path):
    for key in path.split('.'):
        if isinstance(value,list):
            try:value=value[int(key)]
            except (ValueError,IndexError):return None
        elif isinstance(value,dict):value=value.get(key)
        else:return None
    return value


def equivalent(actual, expected):
    if isinstance(expected,dict):
        if 'ref' in expected:return str(actual)==ident(expected['ref'])
        if 'contains' in expected:return all(s.casefold() in str(actual).casefold() for s in expected['contains'])
        if 'one_of' in expected:return any(equivalent(actual,x) for x in expected['one_of'])
    if actual is None or expected is None:return actual is expected
    if isinstance(expected,(int,float,str)):
        try:return Decimal(str(actual))==Decimal(str(expected))
        except InvalidOperation:pass
    return actual==expected


def validate_case(case):
    """Fail closed on misspelled/nonexistent label paths, independent of output."""
    from greenhouse_owner_os.domain.owner_capture import DTO
    from pydantic import BaseModel
    from typing import get_origin,get_args
    def valid_path(model,path):
        bits=path.split('.')
        current=model
        for idx,part in enumerate(bits):
            if isinstance(current,type) and issubclass(current,BaseModel):
                if part not in current.model_fields:return False
                current=current.model_fields[part].annotation
            elif get_origin(current) is list:
                if not part.isdigit():return False
                current=get_args(current)[0]
            else:return False
            if idx<len(bits)-1 and get_args(current) and get_origin(current) is not list:
                choices=[x for x in get_args(current) if x is not type(None)]
                if len(choices)==1:current=choices[0]
        return True
    for expected in case['expected']:
        if expected['kind'] not in DTO:raise AssertionError('Unknown expected route')
        model=DTO[expected['kind']]
        for path in set(expected.get('fields',{}))|set(expected.get('unknown',[]))|set(expected.get('require_inference_only',[]))|set(expected.get('require_unreviewed',[])):
            if not valid_path(model,path):raise AssertionError(f"Invalid label path: {case['id']} {expected['kind']} {path}")
    overlap=set(case.get('forbid_kinds',[]))&{e['kind'] for e in case['expected']}
    if overlap:raise AssertionError(f"Expected and forbidden route overlap: {case['id']} {overlap}")


# Independent CURRENT03 oracle, not copied from preparation outputs. These are
# unconfirmed Görgün sample candidates only; no global quality classification.
SCOPED_QUALITY_CANDIDATES={'grand':'HIGHEST','birinci':'FIRST','ikinci':'SECOND','i̇kinci':'SECOND'}

def permitted_scoped_quality_candidate(expected,payload,provenance,key,value):
    if not re.fullmatch(r'qualities\.\d+',key):return False
    idx=key.split('.')[1];fields=expected.get('fields',{})
    if fields.get('depot_id')!={'ref':'gorgun'}:return False
    if str(payload.get('depot_id'))!=ident('gorgun'):return False
    label=fields.get(f'lines.{idx}.original_quality_name')
    if not isinstance(label,str) or dig(payload,f'lines.{idx}.original_quality_name')!=label:return False
    if SCOPED_QUALITY_CANDIDATES.get(label.casefold())!=value:return False
    if provenance.get('category')!='INFERENCE':return False
    if payload.get('quality_reviewed') is True:return False
    return True


def score(case, slots):
    expected=case['expected']; actual_kinds=[s[0] for s in slots]
    route_ok=Counter(actual_kinds)==Counter(e['kind'] for e in expected)
    used=set();checks=[];invented=[];unsafe=[];missing=[];covered=0;necessary=0;unassessed=[];assessed=0
    for ex in expected:
        matches=[i for i,s in enumerate(slots) if i not in used and s[0]==ex['kind']]
        # Match repeated same-kind slots by independently labeled field agreement.
        best=max(matches,key=lambda i:sum(equivalent(dig(slots[i][1],k),v) for k,v in ex.get('fields',{}).items()),default=None)
        if best is None:
            for key,val in ex.get('fields',{}).items():checks.append(False);missing.append(key)
            checks.extend(False for _ in ex.get('unknown',[]))
            checks.extend(False for _ in ex.get('preserve',[]))
            checks.extend(False for _ in ex.get('require_inference_only',[]))
            checks.extend(False for _ in ex.get('require_unreviewed',[]))
            continue
        used.add(best);covered+=1;payload=slots[best][1]
        critical={'worker_id','greenhouse_id','planned_date'} if ex['kind'] in ('WORKER_TASK','PERSONAL_TASK') else {'amount','currency','event_date','counterparty_id','money_account_id','obligation_id','payment_purpose'} if ex['kind'] in ('PAYABLE','PAYMENT','DIRECT_EXPENSE') else {'depot_id','event_date','stated_total','alacak_value'} if ex['kind']=='DEPOT_RECEIPT' else set()
        if ex['kind'] in ('STOCK_RECEIPT','STOCK_APPLICATION'):
            critical.add('event_date')
            for idx in range(len(payload.get('lines',[]))):
                critical.update(f'lines.{idx}.{k}' for k in ('product_id','location_id','quantity','unit','greenhouse_id','destination_id','unit_cost','currency'))
        elif ex['kind']=='DEPOT_RECEIPT':
            for idx in range(len(payload.get('lines',[]))):
                critical.update(f'lines.{idx}.{k}' for k in ('original_quality_name','net_weight_kg','unit_price','stated_amount','count'))
                critical.add(f'qualities.{idx}')
        if ex['kind'] in ('PAYABLE','PAYMENT','DIRECT_EXPENSE'):
            for idx in range(len(payload.get('attributions',[]))):
                critical.update(f'attributions.{idx}.{k}' for k in ('work_area_id','amount'))
        declared=set(ex.get('fields',{}))|set(ex.get('unknown',[]))
        for key in critical:
            if key in declared:assessed+=1;continue
            value=dig(payload,key)
            provenance=slots[best][2].get(key,slots[best][2].get('qualities',{}) if key.startswith('qualities.') else {})
            permitted_default=((key=='event_date' and value==case.get('capture_day',DAY.isoformat()) or key=='currency' and value=='TRY') and provenance.get('category')=='INFERENCE') or permitted_scoped_quality_candidate(ex,payload,provenance,key,value)
            if permitted_default:assessed+=1
            if value is not None and value!=[] and not permitted_default:unassessed.append(dict(kind=ex['kind'],field=key))
        for key,val in ex.get('fields',{}).items():
            ok=equivalent(dig(payload,key),val);checks.append(ok)
            if not ok:
                missing.append(key)
                actual=dig(payload,key)
                if actual is not None and not (isinstance(val,dict) and 'contains' in val):
                    invented.append(dict(kind=ex['kind'],field=key,actual=actual,expected=val,reason='contradicts_independent_label'))
        for key in ex.get('unknown',[]):
            value=dig(payload,key);ok=value is None or value==[]
            checks.append(ok)
            if not ok:invented.append(dict(kind=ex['kind'],field=key,actual=value))
            required={'WORKER_TASK':{'worker_id','greenhouse_id','planned_date'},'PERSONAL_TASK':{'title'},'PAYABLE':{'amount','counterparty_id','event_date','currency'},'PAYMENT':{'amount','counterparty_id','event_date','currency','money_account_id','obligation_id','payment_purpose'},'DIRECT_EXPENSE':{'amount','event_date','currency','money_account_id'}}.get(ex['kind'],{'lines.0.product_id','lines.0.quantity','lines.0.location_id'} if ex['kind'] in ('STOCK_RECEIPT','STOCK_APPLICATION') else set())
            if ok and key in required:necessary+=1
        for key in ex.get('require_inference_only',[]):
            category=slots[best][2].get(key,{}).get('category')
            checks.append(category=='INFERENCE')
            if category is None:unassessed.append(dict(kind=ex['kind'],field=key,reason='missing_candidate_provenance'))
            elif category!='INFERENCE':invented.append(dict(kind=ex['kind'],field=key,reason='review_candidate_claimed_as_fact',actual_provenance=category))
        for key in ex.get('require_unreviewed',[]):
            ok=dig(payload,key) is not True;checks.append(ok)
            if not ok:invented.append(dict(kind=ex['kind'],field=key,reason='owner_review_fabricated',actual=True))
        for key in ex.get('preserve',[]):
            checks.append(key.casefold() in json.dumps(payload,ensure_ascii=False).casefold())
    forbidden=set(case.get('forbid_kinds',[]))
    unsafe=[kind for kind in actual_kinds if kind in forbidden]
    expected_kinds={x['kind'] for x in expected}
    # A different authoritative kind is unsafe even if no effect has posted yet.
    unsafe.extend(kind for kind in actual_kinds if kind!='UNRESOLVED' and kind not in expected_kinds)
    unsafe.extend(slots[i][0]+':UNMATCHED_SLOT' for i in range(len(slots)) if i not in used and slots[i][0]!='UNRESOLVED')
    # Remainder is not credited merely because the original source still exists.
    remainder=case.get('remainder',[])
    review_text=' '.join(json.dumps(s[1],ensure_ascii=False) for s in slots if s[0]=='UNRESOLVED')
    remainder_ok=all(piece.casefold() in review_text.casefold() for piece in remainder)
    prepared_text=' '.join(json.dumps(slot[1],ensure_ascii=False) for slot in slots)
    source_preservation_ok=all(piece.casefold() in prepared_text.casefold() for ex in expected for piece in ex.get('preserve',[]))
    requested=case.get('requested_prefill',{})
    requested_correct=sum(any(equivalent(dig(payload,key),value) for _,payload,_ in slots) for key,value in requested.items())
    supported=case.get('support')!='unsupported'
    unresolved_safe=(bool(actual_kinds) and not unsafe and not invented and all(k=='UNRESOLVED' for k in actual_kinds) and remainder_ok)
    return dict(id=case['id'],family=case['family'],tier=case['tier'],route_ok=route_ok,
                supported=supported,requested_prefill_correct=requested_correct,requested_prefill_total=len(requested),requested_prefill_gap=len(requested)-requested_correct,field_correct=sum(checks),field_total=len(checks),
                key_fields_ok=all(checks),invented=invented,unsafe=unsafe,
                ambiguity_case=bool(case.get('ambiguity')),ambiguity_safe=bool(slots) and not unsafe and not invented and not unassessed,
                unassessed_critical_facts=unassessed,assessed_critical_fields=assessed,empty_output=not bool(slots),
                slots_expected=len(expected),slots_covered=covered,remainder_ok=remainder_ok,source_preservation_ok=source_preservation_ok,
                safely_unresolved=unresolved_safe,manual_business_inputs_proxy=len(missing),
                failure_repair_count_proxy=len(invented)+len(unsafe),necessary_unresolved_input_proxy=necessary,actual_kinds=actual_kinds)


def summarize(results):
    n=len(results); field_n=sum(x['field_total'] for x in results)
    amb=[x for x in results if x['ambiguity_case']];supported=[x for x in results if x['supported']]
    return dict(cases=n,route_accuracy=sum(x['route_ok'] for x in results)/n if n else None,
                key_field_accuracy=sum(x['field_correct'] for x in results)/field_n if field_n else None,
                supported_route_recall=sum(x['route_ok'] for x in supported)/len(supported) if supported else None,
                ambiguity_safety=sum(x['ambiguity_safe'] for x in amb)/len(amb) if amb else None,
                unsafe_incorrect_routes=sum(bool(x['unsafe']) for x in results),
                invented_fact_cases=sum(bool(x['invented']) for x in results),
                unassessed_critical_fact_cases=sum(bool(x['unassessed_critical_facts']) for x in results),
                unassessed_critical_facts=sum(len(x['unassessed_critical_facts']) for x in results),
                critical_fact_assessment_coverage=sum(x['assessed_critical_fields'] for x in results)/max(1,sum(x['assessed_critical_fields']+len(x['unassessed_critical_facts']) for x in results)),
                safely_unresolved=sum(x['safely_unresolved'] for x in results),
                semantic_slot_coverage=sum(x['slots_covered'] for x in results)/sum(x['slots_expected'] for x in results) if n else None,
                remainder_preservation=sum(x['remainder_ok'] for x in results)/n if n else None,
                labelled_source_preservation=sum(x['source_preservation_ok'] for x in results)/n if n else None,
                manual_business_inputs_proxy=sum(x['manual_business_inputs_proxy'] for x in results)/n if n else None,
                supported_manual_business_inputs_proxy=sum(x['manual_business_inputs_proxy'] for x in supported)/len(supported) if supported else None,
                requested_alias_prefill_recall=sum(x['requested_prefill_correct'] for x in results)/sum(x['requested_prefill_total'] for x in results) if sum(x['requested_prefill_total'] for x in results) else None,
                source_supplied_unbound_fact_cases=sum(bool(x['requested_prefill_gap']) for x in results),
                failure_repair_count_proxy=sum(x['failure_repair_count_proxy'] for x in results)/n if n else None,
                necessary_unresolved_input_proxy=sum(x['necessary_unresolved_input_proxy'] for x in results)/n if n else None,
                taps_to_confirm_measured=None,physical_device_acceptance='OWNER_PENDING')


def safety_reasons(result):
    reasons=[]
    if result['unsafe']:reasons.append('unsafe_authoritative_route_or_duplicate')
    if result['invented']:reasons.append('contradictory_or_filled_unknown_fact')
    if result['unassessed_critical_facts']:reasons.append('unassessed_emitted_critical_fact')
    if result['empty_output']:reasons.append('empty_preparation_output')
    if not result['remainder_ok']:reasons.append('unresolved_remainder_lost')
    if not result['source_preservation_ok']:reasons.append('labelled_source_constraint_lost')
    return reasons


def run(cases, *, parity=True):
    results=[];errors=[];parity_failures=[]
    for case in cases:
        try:
            validate_case(case)
            slots=prepare_case(case)
            if parity and slots!=prepare_case(case,'voice_transcript'):parity_failures.append(case['id'])
            results.append(score(case,slots))
        except Exception as exc:
            errors.append(dict(id=case['id'],error=type(exc).__name__+': '+str(exc)))
            result=score(case,[]);result['unsafe']=['PREPARATION_EXCEPTION'];results.append(result)
    grouped=defaultdict(list)
    for result in results:grouped[result['family']].append(result)
    safety_cases=[dict(id=r['id'],reasons=safety_reasons(r)) for r in results if safety_reasons(r)]
    return dict(summary=summarize(results),safety_gate=dict(passed=bool(parity) and not(safety_cases or errors or parity_failures),required_checks_complete=bool(parity),threshold='zero',case_failures=safety_cases,preparation_exceptions=len(errors),text_voice_mismatches=len(parity_failures),scope='Semantic preparation only; persisted source/idempotency/authorization covered by mandatory PostgreSQL and browser regressions'),families={k:summarize(v) for k,v in sorted(grouped.items())},
                parity=dict(cases=len(cases) if parity else 0,failures=parity_failures,
                            scope='actual prepare on identical retained text; PostgreSQL modes tested separately; no acoustic claim'),
                errors=errors,failures=[x for x in results if not x['route_ok'] or not x['key_fields_ok'] or x['unsafe'] or x['invented'] or x['unassessed_critical_facts'] or not x['remainder_ok']],
                measurement_scope='read-only fixture references, actual OwnerCaptureService.prepare; manual/clarification counts are missing-labelled-field proxies, not measured UI taps')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--corpus',type=Path,default=CORPUS/'train.jsonl');ap.add_argument('--out',type=Path);ap.add_argument('--limit',type=int);ap.add_argument('--no-parity',action='store_true');ap.add_argument('--strict',action='store_true');ap.add_argument('--require-safety',action='store_true')
    args=ap.parse_args();raw=args.corpus.read_bytes();cases=[json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    import importlib.util
    package_root=Path(next(iter(importlib.util.find_spec('greenhouse_owner_os').submodule_search_locations)))
    fingerprint=lambda:{str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(package_root.glob('services/*.py'))}
    before_fingerprint=fingerprint()
    before_harness_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    corpus_case_count=len(cases)
    if args.require_safety and args.no_parity:ap.error('--require-safety requires TEXT/VOICE parity; remove --no-parity')
    if args.limit:cases=cases[:args.limit]
    report=run(cases,parity=not args.no_parity)
    import sys,subprocess
    from greenhouse_owner_os.services import owner_capture
    service_files=sorted({str(Path(mod.__file__).resolve()) for name,mod in sys.modules.items() if name.startswith('greenhouse_owner_os.services.') and getattr(mod,'__file__',None)})
    report['implementation']={'owner_capture_path':str(Path(owner_capture.__file__).resolve()),'service_sha256':{path:hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in service_files},'checkout_head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'checkout_dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),'note':'For isolated baseline, service paths/hashes are authority; checkout head belongs to harness repo. Candidate final gates must match exact clean SHA.'}
    after_fingerprint=fingerprint()
    if before_fingerprint!=after_fingerprint or before_harness_hash!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        report['errors'].append({'id':'RUN_INTEGRITY','error':'Implementation or harness files changed during evaluation; report is not a stable-source measurement'})
        report['safety_gate']['passed']=False
    report['implementation']['before_service_sha256']=before_fingerprint
    report['implementation']['source_changed_during_run']=before_fingerprint!=after_fingerprint
    report['coverage_gate']={'passed':not(report['failures'] or report['errors'] or report['parity']['failures']),'strict_equivalent_exit_code':1 if report['failures'] or report['errors'] or report['parity']['failures'] else 0,'assertion_failure_cases':len(report['failures'])}
    report['harness_sha256']=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    report.update(corpus_sha256=hashlib.sha256(raw).hexdigest(),corpus_path=str(args.corpus),corpus_case_count=corpus_case_count,evaluated_case_count=len(cases),partial_evaluation=len(cases)!=corpus_case_count,capture_day=DAY.isoformat())
    if args.out:write_json(args.out,report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('failures','errors')},ensure_ascii=False,indent=2))
    if args.strict and (report['failures'] or report['errors'] or report['parity']['failures']):raise SystemExit(1)
    if args.require_safety and not report['safety_gate']['passed']:raise SystemExit(1)

if __name__=='__main__':main()
