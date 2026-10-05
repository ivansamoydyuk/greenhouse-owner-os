"""Exercise collection before verbose/JUnit reporting can emit a binary payload."""
import shutil
import subprocess
import sys
from pathlib import Path


def test_binary_parameter_requires_bounded_reporting_id(tmp_path):
    shutil.copyfile(Path(__file__).parents[1] / 'conftest.py', tmp_path / 'conftest.py')
    (tmp_path / 'pytest.ini').write_text('[pytest]\n')
    source = "import pytest\n@pytest.mark.parametrize('raw', [b'', b'x'*600]{ids})\ndef test_upload(raw): pass\n"
    fixture = tmp_path / 'test_upload.py'
    command = [sys.executable, '-m', 'pytest', '--collect-only', '-q', '-c', str(tmp_path / 'pytest.ini')]
    fixture.write_text(source.format(ids=''))
    blocked = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True, timeout=15)
    assert blocked.returncode == 4
    assert 'Test node ID exceeds 512 characters' in blocked.stderr
    assert 'x' * 600 not in blocked.stdout + blocked.stderr
    fixture.write_text(source.format(ids=", ids=['empty', 'oversized']"))
    safe = subprocess.run(command, cwd=tmp_path, text=True, capture_output=True, timeout=15)
    assert safe.returncode == 0, safe.stdout + safe.stderr
    assert 'test_upload[empty]' in safe.stdout and 'test_upload[oversized]' in safe.stdout
    assert '2 tests collected' in safe.stdout
