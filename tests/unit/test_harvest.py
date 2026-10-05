from decimal import Decimal
from types import SimpleNamespace as Ref
from datetime import date
from uuid import uuid4
import pytest
from greenhouse_owner_os.domain.harvest import quality,line_amount
from greenhouse_owner_os.services.capture import extract
@pytest.mark.parametrize('label,normalized',[('Sera','FIRST'),('Grand','FIRST'),('Yerli','YERLI'),('İkinci','SECOND'),('2 inci','SECOND'),('Kava','VERIFY'),('Other','VERIFY')])
def test_quality_preserves_unknown(label,normalized):assert quality(label)==normalized

def test_exact_rounding_and_weighted_average():
    assert line_amount('1234.50','25.75')==Decimal('31788.38')
    assert line_amount('0.001','5')==Decimal('.01')

def test_capture_load_not_guessed_link_or_receipt():
    area=Ref(id=uuid4(),name='İmamlı',kind='greenhouse')
    route,f,g=extract('Сегодня с Имамлы отправили одну машину банана.',areas=[area],today=date(2026,10,5))
    assert route=='HARVEST_DEPOT' and f['kind']=='batch' and f['event_date']=='2026-10-05'
    assert 'report_ids' not in f and g

def test_harvest_openapi_matches_runtime_and_linear_migration():
    from pathlib import Path
    import yaml,importlib.util
    from greenhouse_owner_os.api.app import create_app
    spec=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    runtime=create_app(finance_service=None,actor_provider=lambda:None).openapi()
    for path,ops in runtime['paths'].items():
        if '/harvest' in path:assert spec['paths'][path.removeprefix('/v1')]==ops
    p=Path('alembic/versions/20261005_0010_harvest_depot.py')
    module_spec=importlib.util.spec_from_file_location('harvest_migration',p);m=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(m)
    assert m.revision=='20261005_0010' and m.down_revision=='20261005_0009'


def test_evidence_identity_migration_is_forward_only_new_head():
    from pathlib import Path
    import importlib.util
    p=Path('alembic/versions/20261005_0011_evidence_identity.py')
    spec=importlib.util.spec_from_file_location('identity_migration',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.revision=='20261005_0011' and m.down_revision=='20261005_0010'
    with pytest.raises(RuntimeError):m.downgrade()
