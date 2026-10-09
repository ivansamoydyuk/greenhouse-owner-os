"""Intermediate UI pushes cannot open owner review; exact-SHA rerun can."""
from pathlib import Path
import os
import subprocess

WORKFLOW=Path(__file__).resolve().parents[1]/'.github/workflows/owner-finance-preview.yml'

def test_ui_preview_launch_gate_allows_only_explicit_final_start(tmp_path):
    workflow=WORKFLOW.read_text()
    script=workflow.split('        run: |\n',1)[1].split('      - ',1)[0]
    script='\n'.join(line[10:] for line in script.splitlines())
    for branch,event,attempt,expected in [
        ('06-owner-ui-r2-modern-dashboard','push','1','false'),
        ('06-owner-ui-r2-modern-dashboard','pull_request','1','false'),
        ('06-owner-ui-r2-modern-dashboard','push','2','true'),
        ('06-owner-ui-r2-modern-dashboard','workflow_dispatch','1','true'),
        ('06-owner-ux-r2-capture-confirmation','push','1','true'),
    ]:
        output=tmp_path/f'{branch}-{event}-{attempt}'
        subprocess.run(['bash','-eu','-c',script],env={**os.environ,'GITHUB_OUTPUT':str(output),
            'REVIEW_BRANCH':branch,'REVIEW_EVENT':event,'REVIEW_ATTEMPT':attempt},check=True)
        assert output.read_text().strip()=='enabled='+expected


def test_all_preview_setup_tunnel_access_artifact_and_window_steps_are_gated():
    steps=WORKFLOW.read_text().split('    steps:\n',1)[1].split('      - ')[1:]
    assert steps[0].startswith('name: Gate owner review launch')
    for step in steps[1:]:
        if step.startswith('name: Stop preview'):
            assert 'if: always()' in step and 'if [ -f "$process_file" ]' in step
        else:
            assert "if: steps.review.outputs.enabled == 'true'" in step,step
    assert 'OWNER_PREVIEW_FEATURE_SHA' in WORKFLOW.read_text()
    assert "assert config['feature_sha']==os.environ['OWNER_PREVIEW_FEATURE_SHA']" in WORKFLOW.read_text()
