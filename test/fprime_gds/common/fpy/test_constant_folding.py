from fprime.common.models.serialize.numerical_types import U32Type
import pytest
from fprime_gds.common.fpy.test_helpers import (
    assert_run_success,
    assert_compile_failure,
    assert_compile_success,
    assert_run_failure,
    lookup_type,
)


# define this function if you want to just use the Python fpy model
@pytest.fixture(name="fprime_test_api", scope="module")
def fprime_test_api_override():
    """A file-specific override that simply returns None."""
    return None


def test_simple_bool(fprime_test_api):
    seq = \
"""
varBool : bool = True == True
"""

    assert_run_success(fprime_test_api, seq)