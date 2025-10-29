from fprime.common.models.serialize.numerical_types import U32Type
import pytest
from fprime_gds.common.fpy.test_helpers import (
    assert_run_success,
    assert_compile_failure,
    assert_compile_success,
    assert_run_failure,
    lookup_type,
    compare_optimized_to_regular,
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
    compare_optimized_to_regular(fprime_test_api, seq)
    assert_run_success(fprime_test_api, seq)

def test_simple_addition(fprime_test_api):
    seq = \
"""
varBool : U32 = 1 + 2
"""
    compare_optimized_to_regular(fprime_test_api, seq)
    assert_run_success(fprime_test_api, seq)