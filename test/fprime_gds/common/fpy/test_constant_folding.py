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

@pytest.fixture(scope="session", autouse=True)
def setup_once():
    """Runs once at the start of the entire test session"""
    print("Setting up test environment...")
    # with open('diff_optimizations.txt', 'w') as f:
    #     f.write('')
    # Uncomment this is you wish to get diff results in a text file
    yield  # This is where tests run
    print("Tearing down test environment...")
    # Cleanup code here

# define this function if you want to just use the Python fpy model
@pytest.fixture(name="fprime_test_api", scope="module")
def fprime_test_api_override():
    """A file-specific override that simply returns None."""
    return None


def test_simple_bool(fprime_test_api):
    seq = \
"""
varBool : bool = True == True
exit(varBool)
"""

    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_simple_addition(fprime_test_api):
    seq = \
"""
varInt : U32 = 1 + 2
varBool : bool = varInt == 3
exit(varBool)
"""
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_inequality(fprime_test_api):
    seq = \
"""
varBool : bool = (1 < 2) == True
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_inequality_opposite(fprime_test_api):
    seq = \
"""
varBool : bool = ((2 < 1) == False)
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_inequality_compared_boolean(fprime_test_api):
    seq = \
"""
varBool : bool = (1 > 2) == True
exit(varBool)
"""
    
    assert_run_failure(fprime_test_api, seq, options=["-O1"])

def test_inequality_compared_boolean_not(fprime_test_api):
    seq = \
"""
varBool : bool = (1 > 2) == True
exit(not(varBool))
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

# Edge cases for numeric types and overflow
# Constant folding catches values that are out of range and emits error!
def test_integer_overflow_u32(fprime_test_api):
    seq = \
"""
varU32 : U32 = 4294967295 + 1
"""
    
    assert_compile_failure(fprime_test_api, seq, options=["-O1"])

# Catches another error
def test_integer_underflow_subtraction(fprime_test_api):
    seq = \
"""
varU32 : U32 = 0 - 1
"""
    
    assert_compile_failure(fprime_test_api, seq, options=["-O1"])


def test_division_by_zero(fprime_test_api):
    seq = \
"""
varU32 : U32 = 10 / 0
"""
    
    # This should likely fail at runtime, but test consistency
    assert_compile_failure(fprime_test_api, seq, options=["-O1"])

def test_modulo_by_zero(fprime_test_api):
    seq = \
"""
varU32 : U32 = 10 % 0
"""
    
    assert_compile_failure(fprime_test_api, seq, options=["-O1"])

# Operator precedence tests
def test_operator_precedence_multiplication_addition(fprime_test_api):
    seq = \
"""
varU32 : U32 = 2 + 3 * 4
exit(varU32 == 14)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_operator_precedence_parentheses(fprime_test_api):
    seq = \
"""
varU32 : U32 = (2 + 3) * 4
exit(varU32 == 20)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_complex_boolean_expression(fprime_test_api):
    seq = \
"""
varBool : bool = (1 < 2) and (3 > 2) or (5 == 6)
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_complex_boolean_expression_opposite(fprime_test_api):
    seq = \
"""
varBool : bool = (1 < 2) and (3 > 2) or (5 == 6)
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

# Unary operator tests
def test_unary_minus_positive(fprime_test_api):
    seq = \
"""
varU32 : U32 = -(-5)
exit(varU32 == 5)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_unary_not_boolean(fprime_test_api):
    seq = \
"""
varBool : bool = not(False)
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_double_negation_boolean(fprime_test_api):
    seq = \
"""
varBool : bool = not(not(True))
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])


def test_deeply_nested_boolean(fprime_test_api):
    seq = \
"""
varBool : bool = ((1 < 2) and (3 > 2)) or ((4 == 4) and not(5 != 5))
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

# Type coercion edge cases
def test_mixed_numeric_types(fprime_test_api):
    seq = \
"""
varU32 : U32 = 1 + 2.5
"""
    
    assert_compile_failure(fprime_test_api, seq, options=["-O1"])


# Comparison edge cases
# def test_floating_point_equality(fprime_test_api):
#     seq = \
# """
# varBool : bool = 0.1 + 0.2 == 0.3
# exit(varBool)
# """
    
#     assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_negative_zero_comparison(fprime_test_api):
    seq = \
"""
varBool : bool = 0.0 == -0.0
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

# Large numbers that might cause precision issues
def test_large_number_arithmetic(fprime_test_api):
    seq = \
"""
varU32 : U32 = 1000000 * 1000
exit(varU32 == 1000000000)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

# def test_very_small_float_arithmetic(fprime_test_api):
#     seq = \
# """
# varF32 : F32 = 0.000001 * 0.000001
# exit(varF32 == 0.00000000001)
# """
    
#     assert_run_success(fprime_test_api, seq, options=["-O1"])

# Constant folding with repeated operations
def test_repeated_addition(fprime_test_api):
    seq = \
"""
varU32 : U32 = 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1 + 1
exit(varU32 == 10)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_repeated_multiplication(fprime_test_api):
    seq = \
"""
varU32 : U32 = 2 * 2 * 2 * 2 * 2
exit(varU32 == 32)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

# Edge case: constant expressions that should be identical
def test_mathematically_equivalent_expressions(fprime_test_api):
    seq = \
"""
varU32A : U32 = 2 * 3 + 4
varU32B : U32 = 4 + 2 * 3
varBool : bool = varU32A == varU32B
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

# Boundary values for different integer types
def test_u32_max_value(fprime_test_api):
    seq = \
"""
varBool : bool = 4294967295 == 2 ** 32 - 1
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_zero_operations(fprime_test_api):
    seq = \
"""
varU32 : U32 = 0 * 1000000
exit(varU32 == 0)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_identity_operations(fprime_test_api):
    seq = \
"""
varU32 : U32 = 42 * 1
exit(varU32 == 42)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

# Signed integer edge cases
# Catches overflow early
def test_signed_integer_overflow_i32(fprime_test_api):
    seq = \
"""
varI32 : I32 = 2147483647 + 1
exit(varI32 == -2147483648)
"""
    
    assert_compile_failure(fprime_test_api, seq, options=["-O1"])

def test_signed_integer_underflow_i32(fprime_test_api):
    seq = \
"""
varI32 : I32 = -2147483648 - 1
exit(varI323 == 2147483647)
"""
    
    assert_compile_failure(fprime_test_api, seq, options=["-O1"])

def test_negative_modulo(fprime_test_api):
    seq = \
"""
varI32 : I32 = -7 % 3
exit(varI32 == 2)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_division_rounding_negative(fprime_test_api):
    seq = \
"""
varI32 : I32 = -7 // 3
exit(varI32 == -2)
"""
# Check that this is right
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])


# Mixed signed/unsigned operations
def test_mixed_signed_unsigned_comparison(fprime_test_api):
    seq = \
"""
varBool : bool = -1 < 4294967295
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_mixed_signed_unsigned_arithmetic(fprime_test_api):
    seq = \
"""
varU32 : U32 = -1 + 2
exit(varU32 == 1)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_mixed_signed_unsigned_arithmetic_negative(fprime_test_api):
    seq = \
"""
varU32 : U32 = -3 + 2
exit(varU32 == 2**32-1)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])


# Tests for constant propagation vs folding
def test_constant_with_variable_reference(fprime_test_api):
    seq = \
"""
const42 : U32 = 42
varU32 : U32 = const42 * 2
exit(varU32 == 84)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_multiple_constant_references(fprime_test_api):
    seq = \
"""
constA : U32 = 10
constB : U32 = 20
varU32 : U32 = constA + constB
exit(varU32 == 30)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

# Boolean logic optimization edge cases
def test_boolean_quadruple_negation_optimization(fprime_test_api):
    seq = \
"""
varBool : bool = not(not(not(not(True))))
exit(varBool)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_boolean_identity_laws(fprime_test_api):
    seq = \
"""
varBoolA : bool = True and True
varBoolB : bool = False or False
varBoolC : bool = True or False
varBoolD : bool = False and True
exit(varBoolA and not(varBoolB) and varBoolC and not(varBoolD))
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])

def test_de_morgan_laws(fprime_test_api):
    seq = \
"""
varBoolA : bool = not(True and False)
varBoolB : bool = (not(True)) or (not(False))
varBoolC : bool = not(True or False)
varBoolD : bool = (not(True)) and (not(False))
exit(varBoolA and varBoolB and not(varBoolC) and not(varBoolD))
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])


# Multiple assignment constant folding
def test_chained_constant_assignments(fprime_test_api):
    seq = \
"""
varA : U32 = 1 + 2
varB : U32 = varA * 3 # 9
varC : U32 = varB + 4 # 13
exit(varC == 13)
"""
    
    assert_run_success(fprime_test_api, seq, options=["-O1"])