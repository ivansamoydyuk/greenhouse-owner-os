"""Keep CI test names bounded; untrusted payloads belong in fixtures, not logs."""
import pytest


def pytest_collection_modifyitems(items):
    # Verbose/JUnit reporters print node IDs before executing a test. A binary
    # parameter's automatic ID can exhaust the Actions log transport even when
    # the test itself finishes. Require explicit IDs rather than dropping cases.
    oversized = [item for item in items if len(item.nodeid) > 512]
    for item in oversized:
        # Collection-only reporters may still render items after a hook error.
        item._nodeid = 'invalid-parameter-id'
    if oversized:
        raise pytest.UsageError('Test node ID exceeds 512 characters; set explicit short parameter ids')
