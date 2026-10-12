from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory
import yaml
from greenhouse_owner_os.api.app import create_app

def test_inbox_migration_is_exact_linear_head():
    scripts=ScriptDirectory.from_config(Config('alembic.ini'))
    assert scripts.get_heads()==['20261012_0033']
    chain=list(scripts.walk_revisions())
    assert len(chain)==33 and chain[-1].revision=='20261004_0001'
    assert chain[0].revision=='20261012_0033' and chain[0].down_revision=='20261011_0032'
    chain=chain[1:]
    assert chain[0].revision=='20261011_0032' and chain[0].down_revision=='20261011_0031'
    chain=chain[1:]
    assert chain[0].down_revision=='20261011_0030'
    chain=chain[1:]
    assert chain[0].down_revision=='20261010_0029'
    chain=chain[1:]
    assert chain[0].down_revision=='20261010_0028'
    chain=chain[1:]
    assert chain[0].down_revision=='20261010_0027'
    chain=chain[1:]
    assert chain[0].down_revision=='20261010_0026'
    chain=chain[1:]
    assert chain[0].down_revision=='20261010_0025'
    assert chain[1].down_revision=='20261008_0024'
    chain=chain[2:]
    assert chain[0].down_revision=='20261008_0023'
    assert chain[1].down_revision=='20261006_0022'
    chain=chain[2:]
    assert chain[0].down_revision=='20261006_0021' and chain[1].down_revision=='20261006_0020'
    assert chain[7].revision=='20261005_0015' and chain[7].down_revision=='20261005_0014'

def test_inbox_openapi_matches_runtime_paths_and_request_schemas():
    runtime=create_app(finance_service=None,reference_service=object(),actor_provider=lambda:None).openapi()
    artifact=yaml.safe_load(Path('openapi/owner_core_v1.yaml').read_text())
    for path,value in runtime['paths'].items():
        if '/inbox' in path:assert artifact['paths'][path.removeprefix('/v1')]==value
    for name in ('InboxInput','InboxConfirm','InboxReject'):assert artifact['components']['schemas'][name]==runtime['components']['schemas'][name]
