from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from greenhouse_owner_os.domain.inventory import InventoryLine,ConversionInput
from greenhouse_owner_os.api.app import create_app
import pytest,yaml

def test_inventory_linear_exact_head_and_recovery():
 scripts=ScriptDirectory.from_config(Config('alembic.ini'));chain=list(scripts.walk_revisions())
 assert scripts.get_heads()==['20261006_0021'] and len(chain)==21
 assert chain[0].down_revision=='20261006_0020' and chain[1].down_revision=='20261006_0019' and chain[2].down_revision=='20261006_0018' and chain[3].down_revision=='20261005_0017' and chain[4].down_revision=='20261005_0016'
 for m in chain[:2]:assert 'Forward-only' in Path(m.path).read_text()

def test_inventory_openapi_runtime_equal():
 runtime=create_app(finance_service=None,reference_service=object(),actor_provider=lambda:None).openapi()
 artifact=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
 for p,v in runtime['paths'].items():
  if '/inventory' in p:assert artifact['paths'][p.removeprefix('/v1')]==v
 for name in ['InventoryInput','InventoryLine','InventoryEvidence','InventoryPost','InventoryReverse','ProductInput','ConversionInput','LocationInput','ValuationCurrencyInput']:
  assert artifact['components']['schemas'][name]==runtime['components']['schemas'][name], {k:(artifact['components']['schemas'][name]['properties'].get(k),v) for k,v in runtime['components']['schemas'][name]['properties'].items() if artifact['components']['schemas'][name]['properties'].get(k)!=v}

@pytest.mark.parametrize('v',[1.2,'NaN','Infinity','1e2','-1','0.0000000000001'])
def test_decimal_boundary_rejects_guess_rounding_and_float(v):
 with pytest.raises(ValidationError):InventoryLine(original_product_name='TEST',quantity=v)

def test_explicit_conversion_requires_verification():
 with pytest.raises(ValidationError):ConversionInput(unit='bag',factor='25',verification='')


def test_decimal_input_preserves_more_than_context_precision():
 value='999999999999999999.123456789012'
 assert InventoryLine(original_product_name='TEST',quantity=value).quantity==value
