"""Fail if the DayWorkers delivery branch silently omits a retained CI suite."""
from pathlib import Path
import re

import yaml

BRANCH = '16-greenhouse-daily-day-workers'
WORKFLOWS = ('postgresql-verification', 'r3-connected-pilot', 'capture-daily-checkpoint')
RETAINED_R3 = (
    'overnight-workers.mjs', 'r3-capture-friction.mjs', 'overnight-fertilizer.mjs',
    'harvest-owner-journey.mjs', 'overnight-finance.mjs', 'overnight-purchases.mjs',
    'fertilizer-completion.mjs',
)


def _selected(condition):
    if condition is None:
        return True
    # These delivery selectors deliberately use only literal branch equality.
    # Fail closed when their grammar changes instead of using eval or substring checks.
    matches = [re.fullmatch(r"github\.ref_name\s*==\s*'([^']+)'", part.strip())
               for part in condition.split('||')]
    assert all(matches), f'Unsupported branch selector: {condition}'
    return BRANCH in [match.group(1) for match in matches]


def check_day_workers_selection(root):
    directory = root / '.github' / 'workflows'
    workflows = {name: yaml.load((directory / (name + '.yml')).read_text(), Loader=yaml.BaseLoader)
                 for name in WORKFLOWS}
    for name, workflow in workflows.items():
        push = workflow['on']['push']
        assert BRANCH in push['branches'], name
        assert not {'paths', 'paths-ignore', 'branches-ignore'} & push.keys(), name
    pg = workflows['postgresql-verification']['jobs']
    assert set(pg) == {'verify', 'owner-r2-browser'}
    assert all(_selected(job.get('if')) for job in pg.values())
    r3 = workflows['r3-connected-pilot']['jobs']
    assert set(r3) == {'connected-browser', 'private-daily-browser'}
    assert all(_selected(job.get('if')) for job in r3.values())
    steps = r3['connected-browser']['steps']
    for script in RETAINED_R3:
        matching = [step for step in steps if 'node tests/e2e/' + script in step.get('run', '')]
        assert len(matching) == 1 and _selected(matching[0].get('if')), script
    baseline = next(step for step in steps if step.get('name') == 'Before evidence on immutable delivered baseline')
    assert not _selected(baseline.get('if')), 'Do not run the historical friction baseline on this branch'
    capture = workflows['capture-daily-checkpoint']['jobs']['corpus-browser-sql']
    assert _selected(capture.get('if'))
    assert capture['strategy']['matrix'] == {'mode': ['candidate'], 'engine': ['chromium', 'webkit']}
    candidate = next(step for step in capture['steps'] if 'node tests/e2e/capture-daily-checkpoint.mjs' in step.get('run', ''))
    assert candidate['if'] == "matrix.mode == 'candidate'"
    day = yaml.load((directory / 'day-workers-checkpoint.yml').read_text(), Loader=yaml.BaseLoader)
    assert BRANCH in day['on']['push']['branches']
    job = day['jobs']['day-workers-browser-sql']
    assert job['env']['DAY_WORKERS_PHASE'] == 'full'
    semantic = [step for step in job['steps'] if 'node tests/e2e/task-audience.mjs' in step.get('run', '')]
    assert len(semantic) == 1 and _selected(semantic[0].get('if'))
    script = semantic[0]['run']
    assert 'set -euo pipefail' in script and 'for engine in chromium webkit; do' in script
    assert 'export TASK_AUDIENCE_ENGINE="$engine"' in script
    assert 'export E2E_ARTIFACTS="artifacts/task-audience/$engine"' in script
    assert 'task_audience_sql_evidence.py --prepare' in script
    assert 'task_audience_sql_evidence.py --before' in script
    assert 'task_audience_sql_evidence.py --verify' in script
    upload = next(step for step in job['steps'] if step.get('uses') == 'actions/upload-artifact@v4')
    assert 'artifacts/task-audience/' in upload['with']['path'].splitlines()
    assert upload['if'] == "always() && steps.guard.outcome == 'success'"



def test_day_workers_branch_runs_all_retained_ci_suites():
    check_day_workers_selection(Path(__file__).resolve().parents[2])
