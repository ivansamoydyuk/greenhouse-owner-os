from types import SimpleNamespace

import pytest

from greenhouse_owner_os.services.harvest import field_count_summary


@pytest.mark.parametrize('counts,total,known,complete', [
    ([], None, 0, False), ([None], None, 0, False),
    ([7, None], 7, 1, False), ([7], 7, 1, True),
    ([7, 3], 10, 2, True), ([None, None], None, 0, False),
])
def test_field_coverage_counts_reports_without_replacing_unknown(counts, total, known, complete):
    rows = [SimpleNamespace(cut_count=count) for count in counts]
    assert field_count_summary(rows) == (total, dict(
        report_count=len(counts), known_report_count=known,
        unknown_report_count=len(counts)-known, complete=complete))
    assert [row.cut_count for row in rows] == counts
