"""Mechanical, auditable variants of TRAINING GOLD only; never author GOLD.

Every changed fact has an exact source-token replacement and corresponding label
replacement. No fuzzy match, output-derived label, synonym meaning guess, unit
conversion, or negation/correction removal is allowed. The deliberately small
transformation vocabulary is a coverage limitation, reported in the manifest.
"""
from copy import deepcopy
from datetime import date,timedelta
from itertools import product
import argparse,hashlib,json,re
from pathlib import Path
from scripts.owner_language_benchmark import CORPUS,load,write_json,atomic_write

MONEY=[str(125*i) for i in range(1,65)]+['125','500','850','1200','1800','2500','5000','7500','10000','16000','28000','31000','50000','100000','400000','1250.50']
QUANTITY=[str(i) for i in range(1,41)]+['1','2','3','5','8','10','12','15','20','25','50','60','100','125','2.5','12.5']
WORKERS={'umar':'Умар','inayat':'Инаят','muhammad':'Мухаммед'}
AREAS={'imamli':'Имамлы','yesiloz':'Ешилёз','ozvadi':'Озвади'}
TIME={'сегодня':0,'завтра':1,'послезавтра':2,'вчера':-1}


def replace_literal(case,old,new,changes):
    """Replace exactly one bounded literal, with no semantic label inheritance."""
    p=re.compile(r'(?<!\w)'+re.escape(old)+r'(?!\w)',re.I)
    if len(p.findall(case['text']))!=1:return False
    case['text']=p.sub(lambda _:new,case['text'])
    for e in case['expected']:
        if e.get('preserve'):e['preserve']=[p.sub(lambda _:new,s) for s in e['preserve']]
    changes.append(dict(old=old,new=new,method='exact_single_bounded_token'))
    return True


def eligible(case):
    # Compound/corrected/ambiguous sources are still tested as GOLD+noise; their
    # fact scopes are not safe for automatic semantic rewriting.
    return not exclusion_reasons(case)

def exclusion_reasons(case):
    reasons=[]
    if len(case['expected'])!=1:reasons.append('multiple_semantic_slots')
    if case.get('ambiguity'):reasons.append('declared_ambiguity')
    if case.get('remainder'):reasons.append('unresolved_remainder')
    if re.search(r'(?i)(\bне\b|\bнет\b|потом|остал|половин|раздел|кажд|по \d|\d[-–]\d)',case['text']):reasons.append('negation_correction_partial_or_quantity_scope')
    return reasons


def candidates(case):
    if not eligible(case):return
    e=case['expected'][0];fields=e.get('fields',{});dimensions=[]
    if 'amount' in fields and isinstance(fields['amount'],str):
        old=fields['amount']
        if re.search(r'(?<![\w\d])'+re.escape(old)+r'(?![\w\d])',case['text']):dimensions.append(('amount',old,MONEY))
    if 'lines.0.quantity' in fields and isinstance(fields['lines.0.quantity'],str):
        old=fields['lines.0.quantity'];literal=old if old in case['text'] else old.replace('.',',')
        if literal in case['text']:dimensions.append(('lines.0.quantity',literal,QUANTITY))
    for key,mapping in [('worker_id',WORKERS),('greenhouse_id',AREAS)]:
        value=fields.get(key)
        if isinstance(value,dict) and value.get('ref') in mapping:
            old=mapping[value['ref']]
            if len(re.findall(r'(?<!\w)'+re.escape(old)+r'(?!\w)',case['text'],re.I))==1:dimensions.append((key,old,list(mapping.values())))
    times=[w for w in TIME if re.search(r'(?i)\b'+w+r'\b',case['text'])]
    if len(times)==1 and any(k in fields for k in ('planned_date','event_date')):
        allowed=['сегодня','завтра','послезавтра'] if e['kind'] in ('PERSONAL_TASK','WORKER_TASK') else ['сегодня','вчера']
        if times[0] in allowed:dimensions.append(('relative_date',times[0],allowed))
    if not dimensions:return
    for vals in product(*(d[2] for d in dimensions)):
        candidate=deepcopy(case);changes=[];valid=True
        for (key,old,opts),new in zip(dimensions,vals):
            if old.casefold()==new.casefold():continue
            if not replace_literal(candidate,old,new,changes):valid=False;break
            ef=candidate['expected'][0]['fields']
            if key=='relative_date':
                for target in ('planned_date','event_date'):
                    if target in ef:ef[target]=(date.fromisoformat(case['capture_day'])+timedelta(days=TIME[new])).isoformat()
            elif key=='worker_id':ef[key]={'ref':next(k for k,v in WORKERS.items() if v==new)}
            elif key=='greenhouse_id':ef[key]={'ref':next(k for k,v in AREAS.items() if v==new)}
            else:ef[key]=new
        if valid and changes:
            candidate.update(tier='semantic_variant',origin='generated_from_training_gold',parent_id=case['id'],transforms=changes)
            yield candidate


def semantic_variants(train,target):
    all_candidates=[];seen=set()
    # Round-robin through parent iterators prevents a few broad templates from
    # dominating all5000; cap each parent and report lineage distribution.
    gens=[iter(candidates(c) or ()) for c in train]
    while gens and len(all_candidates)<target:
        active=[]
        for gen in gens:
            try:c=next(gen)
            except StopIteration:continue
            active.append(gen);key=(c['text'],c['capture_day'])
            if key in seen:continue
            seen.add(key);c['id']='SEM-'+hashlib.sha256((c['parent_id']+'|'+c['text']).encode()).hexdigest()[:18]
            all_candidates.append(c)
            if len(all_candidates)==target:break
        gens=active
    return all_candidates


# ASR/text-like formatting noise only. Never duplicate negation, mutate a digit,
# strip a decimal separator, corrupt a named identity, or remove an event clause.
PREFIXES=['ну ', 'ну вот ', 'так ', 'значит ', 'смотри ', 'в общем ', 'короче ', 'это самое ', 'так вот ', 'ага ', 'ну значит ', 'смотри пожалуйста ', 'итак ', 'ну смотри ', 'в общем-то ', 'вот ', 'э-э ', 'эм ', 'так значит ', 'ну короче ', 'а значит ', 'в общем смотри ', 'ну в общем ', 'так ну ']

def noise_variants(train,target):
    result=[];seen=set()
    for idx,prefix in enumerate(PREFIXES):
        for base in train:
            c=deepcopy(base);source=c['text']
            # Multi-line depot/document rows preserve their separators. Other
            # rows may gain harmless spaces, never lose structural delimiters.
            noisy=prefix+source
            if idx%3==1:noisy=noisy.replace(' ','  ')
            elif idx%3==2:noisy=noisy.casefold()
            if noisy in seen:continue
            seen.add(noisy)
            c.update(text=noisy,tier='noise_variant',origin='generated_from_training_gold',parent_id=base['id'],transforms=[dict(type='filler_prefix',value=prefix),dict(type='double_spaces' if idx%3==1 else 'lowercase' if idx%3==2 else 'unchanged_source')])
            # Case and whitespace normalization is observationally equivalent;
            # source-preservation assertions track the actual captured wording.
            if idx%3==2:
                for ex in c['expected']:
                    for key,value in list(ex.get('fields',{}).items()):
                        if key.endswith('original_quality_name') and isinstance(value,str):ex['fields'][key]=value.casefold()
            if idx%3==1:
                for ex in c['expected']:
                    if ex.get('preserve'):ex['preserve']=[p.replace(' ','  ') for p in ex['preserve']]
                if c.get('remainder'):c['remainder']=[p.replace(' ','  ') for p in c['remainder']]
            c['id']='NOISE-'+hashlib.sha256((base['id']+'|'+noisy).encode()).hexdigest()[:18]
            result.append(c)
            if len(result)==target:return result
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output-dir',type=Path,default=CORPUS.parents[2]/'artifacts/owner-language');ap.add_argument('--update-manifest',action='store_true');args=ap.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    train=load(CORPUS/'train.jsonl')
    assert all(c['split']=='train' for c in train)
    forbidden={'source_message_url','source_seed_id','source_seed_ids','source_created_at','owner_authorship','source_template_group'}
    assert all(not(forbidden&set(c)) and c.get('origin')=='curated_test' and re.fullmatch(r'GOLD-\d{4}',c['id']) and re.fullmatch(r'GROUP-\d{4}',c['semantic_group']) for c in train),'Publication corpus contains non-TEST metadata'
    semantic=semantic_variants(train,5200);noise=noise_variants(train,5200)
    # If the finite fact lattice is smaller than desired, increase only explicit
    # numeric values; never pad by relabeling/no-op copying.
    if len(semantic)<5000:raise SystemExit(f'Only {len(semantic)} sound semantic variants; extend declared input lattice, not labels')
    files={}
    for name,cases in [('semantic_variants',semantic),('noise_variants',noise)]:
        path=args.output_dir/(name+'.jsonl')
        atomic_write(path,''.join(json.dumps(c,ensure_ascii=False,sort_keys=True)+'\n' for c in cases))
        files[name]=dict(filename=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),count=len(cases))
    allcases=semantic+noise
    manifest=json.loads((CORPUS/'manifest.json').read_text())
    previous=manifest.get('variants',{}).get('files')
    manifest['variants']={
        'files':files,
        'deterministic_exclusions':{reason:sum(reason in exclusion_reasons(c) for c in train) for reason in ('multiple_semantic_slots','declared_ambiguity','unresolved_remainder','negation_correction_partial_or_quantity_scope')},
        'temporal_rule':'Actual events only today/yesterday; task dates today/tomorrow/day-after. No future actuality inheritance.',
        'semantic':len(semantic),'noise':len(noise),'total':len(allcases),
        'semantic_parent_count':len({c['parent_id'] for c in semantic}),
        'noise_parent_count':len({c['parent_id'] for c in noise}),
        'distinct_texts':len({c['text'] for c in allcases}),
        'scope':'Exact single-token amount/quantity/current-worker/current-greenhouse/relative-date crossproducts; filler/case/whitespace noise. No acoustic model, no fuzzy label inheritance, no heldout templates.',
        'limitations':'Semantic coverage intentionally narrower than all GOLD families. Noise is formatting/voice-filler simulation, not real audio recognition or arbitrary typo robustness.',
        'by_family':{f:dict(semantic=sum(c['family']==f for c in semantic),noise=sum(c['family']==f for c in noise)) for f in manifest['counts_by_family']}}
    if args.update_manifest:write_json(CORPUS/'manifest.json',manifest)
    elif previous and previous!=files:raise SystemExit('Generated variant hashes differ from committed manifest; independent review required')
    write_json(args.output_dir/'generation_report.json',manifest['variants'])
    print(json.dumps(manifest['variants'],ensure_ascii=False,indent=2))

if __name__=='__main__':main()
