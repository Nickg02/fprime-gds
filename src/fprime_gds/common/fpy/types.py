from __future__ import annotations
from abc import ABC
import inspect
from dataclasses import dataclass, field, fields
import struct
import traceback
import typing
from typing import Union, get_args, get_origin

# In Python 3.10+, the `|` operator creates a `types.UnionType`.
# We need to handle this for forward compatibility, but it won't exist in 3.9.
try:
    from types import UnionType

    UNION_TYPES = (Union, UnionType)
except ImportError:
    UNION_TYPES = (Union,)

from fprime_gds.common.fpy.bytecode.directives import (
    StackOpDirective,
    FloatLogDirective,
    Directive,
    ExitDirective,
    WaitAbsDirective,
    WaitRelDirective,
)
from fprime_gds.common.templates.ch_template import ChTemplate
from fprime_gds.common.templates.cmd_template import CmdTemplate
from fprime_gds.common.templates.prm_template import PrmTemplate
from fprime.common.models.serialize.time_type import TimeType
from fprime.common.models.serialize.serializable_type import SerializableType
from fprime.common.models.serialize.array_type import ArrayType
from fprime.common.models.serialize.numerical_types import (
    U32Type,
    U16Type,
    U64Type,
    U8Type,
    I16Type,
    I32Type,
    I64Type,
    I8Type,
    F32Type,
    F64Type,
    FloatType,
    IntegerType,
)
from fprime.common.models.serialize.string_type import StringType
from fprime.common.models.serialize.bool_type import BoolType
from fprime_gds.common.fpy.parser import (
    AstExpr,
    AstGetAttr,
    AstOp,
    Ast,
    AstAssign,
)
from fprime.common.models.serialize.type_base import BaseType as FprimeValue

MAX_DIRECTIVES_COUNT = 1024
MAX_DIRECTIVE_SIZE = 2048
MAX_STACK_SIZE = 65535

COMPILER_MAX_STRING_SIZE = 128


# this is the "internal" integer type that integer literals have by
# default. it is arbitrary precision
class InternalIntType(IntegerType):
    @classmethod
    def range(cls):
        raise NotImplementedError()

    @staticmethod
    def get_serialize_format():
        raise NotImplementedError()

    @classmethod
    def get_bits(cls):
        raise NotImplementedError()

    @classmethod
    def validate(cls, val):
        if not isinstance(val, int):
            raise RuntimeError()


InternalStringType = StringType.construct_type("InternalStringType", None)


SPECIFIC_NUMERIC_TYPES = (
    U32Type,
    U16Type,
    U64Type,
    U8Type,
    I16Type,
    I32Type,
    I64Type,
    I8Type,
    F32Type,
    F64Type,
)
SPECIFIC_INTEGER_TYPES = (
    U32Type,
    U16Type,
    U64Type,
    U8Type,
    I16Type,
    I32Type,
    I64Type,
    I8Type,
)
SIGNED_INTEGER_TYPES = (
    I16Type,
    I32Type,
    I64Type,
    I8Type,
)
UNSIGNED_INTEGER_TYPES = (
    U32Type,
    U16Type,
    U64Type,
    U8Type,
)
SPECIFIC_FLOAT_TYPES = (
    F32Type,
    F64Type,
)


def is_instance_compat(obj, cls):
    """
    A wrapper for isinstance() that correctly handles Union types in Python 3.9+.

    Args:
        obj: The object to check.
        cls: The class, tuple of classes, or Union type to check against.

    Returns:
        True if the object is an instance of the class or any type in the Union.
    """
    origin = get_origin(cls)
    if origin in UNION_TYPES:
        # It's a Union type, so get its arguments.
        # e.g., get_args(Union[int, str]) returns (int, str)
        return isinstance(obj, get_args(cls))

    # It's not a Union, so it's a regular type (like int) or a
    # tuple of types ((int, str)), which isinstance handles natively.
    return isinstance(obj, cls)


@dataclass
class FpyValue:
    """the base type for all fpy values. if it is a subclass of this,
    it is the valid result of evaluating an expression in fpy"""

    type: FpyType


TypeFwdRef = None

ALL_TYPES: dict[str, FpyType] = {}

# types are first class objects in Fpy
class FpyType(FpyValue):

    def __init__(
        self,
        base: FpyType,
        name: str,
        members: dict[str, FpyType] = None,
        element_type: FpyType = None,
        length: int = None,
    ):
        if members is not None:
            assert element_type is None and length is None, (element_type, length)
        if element_type is not None:
            assert length is not None and members is None, (length, members)
        super().__init__(TypeFwdRef, None)
        self.base = base
        self.name = name
        self.members = members if members is not None else {}
        ALL_TYPES[name] = self

    def is_subtype(self, other: FpyType):
        if self is Object:
            # all types are subtypes of object
            return True

        # the other type is a subtype iff
        # this type is the same type as the other type,
        # or one of other's parents is this type
        while other is not Object and other is not self:
            other = other.base

        return other is not Object  # => other is self

    def construct(self, *args):
        return FpyValue(self)


# object doesn't have a base type, but it is still considered
# a subtype of itself due to impl of is_subtype
Object = FpyType(None, "object")

Type = FpyType(Object, "type")
# the type of a type object is Type (say this 10 times fast)
Type.type = Type
Object.type = Type
# now update the fwd ref
TypeFwdRef = Type

UnitType = FpyType(Object, "unit")
# the one valid value of the unit type
Unit = UnitType.construct()

Function = FpyType(Object, "function", {"return_type": Type, "args": Object})

Number = FpyType(Object, "number")
Integer = FpyType(Number, "integer")
I8 = FpyType(Integer, "I8")
I16 = FpyType(Integer, "I16")
I32 = FpyType(Integer, "I32")
I64 = FpyType(Integer, "I64")
U8 = FpyType(Integer, "U8")
U16 = FpyType(Integer, "U16")
U32 = FpyType(Integer, "U32")
U64 = FpyType(Integer, "U64")
Float = FpyType(Number, "float")
F32 = FpyType(Float, "F32")
F64 = FpyType(Float, "F64")
Bool = FpyType(Object, "bool")


def construct_fpy_type_from_fprime_type(
    fqn: str, fprime_type: type[FprimeValue]
) -> FpyType:
    existing = ALL_TYPES.get(fqn, None)
    if existing is not None:
        return existing
    if issubclass(fprime_type, ArrayType):
        fpy_type = FpyType(
            Object,
            fqn,
            element_type=fprime_type.MEMBER_TYPE,
            length=fprime_type.LENGTH
        )
    elif issubclass(fprime_type, SerializableType):
        fpy_type = FpyType(
            Object,
            fqn,
            members
        )
    elif fprime_type == TimeType:
        members = 


# named variables can be tlm chans, prms, callables, or directly referenced consts (usually enums)
@dataclass
class FpyVariable:
    """a mutable, typed value referenced by an unqualified name"""

    type_expr: AstExpr
    """the expression denoting the var's type"""
    declaration: AstAssign
    """the node where this var is declared"""
    type: FpyValueType | None = None
    """the resolved type of the variable. None if type unsure at the moment"""
    lvar_offset: int | None = None
    """the offset in the lvar array where this var is stored"""


# a scope
FpyScope = dict[str, "FpyValue"]


class CompileException(BaseException):
    def __init__(self, msg, node: Ast):
        self.msg = msg
        self.node = node
        self.stack_trace = "\n".join(traceback.format_stack(limit=8)[:-1])

    def __str__(self):
        if self.node is not None:
            return f'{self.stack_trace}\nAt line {self.node.meta.line} "{self.node.node_text}": {self.msg}'
        return f"{self.stack_trace}\n{self.msg}"


# MACROS: dict[str, FpyMacro] = {
#     "sleep": FpyMacro(
#         UnitType,
#         [
#             (
#                 "seconds",
#                 U32Type,
#             ),
#             ("microseconds", U32Type),
#         ],
#         WaitRelDirective,
#     ),
#     "sleep_until": FpyMacro(UnitType, [("wakeup_time", TimeType)], WaitAbsDirective),
#     "exit": FpyMacro(UnitType, [("success", BoolType)], ExitDirective),
#     "log": FpyMacro(F64Type, [("operand", F64Type)], FloatLogDirective),
# }


@dataclass
class FieldReference:
    """a reference to a field/index of an fprime type"""

    parent: "FpyReference"
    """the qualifier"""
    type: FpyValueType
    """the fprime type of this reference"""
    offset: int
    """the constant offset in the parent type at which to find this field"""
    name: str = None
    """the name of the field, if applicable"""
    idx: int = None
    """the index of the field, if applicable"""

    def get_from(self, parent_val: FppValue) -> FppValue:
        """gets the field value from the parent value"""
        assert isinstance(parent_val, self.type)
        assert self.name is not None or self.idx is not None
        value = None
        if self.name is not None:
            if isinstance(parent_val, SerializableType):
                value = parent_val.val[self.name]
            elif isinstance(parent_val, TimeType):
                if self.name == "seconds":
                    value = parent_val.__secs
                elif self.name == "useconds":
                    value = parent_val.__usecs
                elif self.name == "time_base":
                    value = parent_val.__timeBase
                elif self.name == "time_context":
                    value = parent_val.__timeContext
                else:
                    assert False, self.name
            else:
                assert False, parent_val

        else:

            assert isinstance(parent_val, ArrayType), parent_val

            value = parent_val._val[self.idx]

        assert isinstance(value, self.type), (value, self.type)
        return value


def create_scope(
    references: dict[str, FpyValue],
) -> FpyScope:
    """from a flat dict of strs to references, creates a hierarchical, scoped
    dict. no two leaf nodes may have the same name"""

    base = {}

    for fqn, ref in references.items():
        names_strs = fqn.split(".")

        ns = base
        while len(names_strs) > 1:
            existing_child = ns.get(names_strs[0], None)
            if existing_child is None:
                # this scope is not defined atm
                existing_child = {}
                ns[names_strs[0]] = existing_child

            if not isinstance(existing_child, dict):
                # something else already has this name
                print(
                    f"WARNING: {fqn} is already defined as {existing_child}, tried to redefine it as {ref}"
                )
                break

            ns = existing_child
            names_strs = names_strs[1:]

        if len(names_strs) != 1:
            # broke early. skip this loop
            continue

        # okay, now ns is the complete scope of the attribute
        # i.e. everything up until the last '.'
        name = names_strs[0]

        existing_child = ns.get(name, None)

        if existing_child is not None:
            # uh oh, something already had this name with a diff value
            print(
                f"WARNING: {fqn} is already defined as {existing_child}, tried to redefine it as {ref}"
            )
            continue

        ns[name] = ref

    return base


def union_scope(lhs: FpyScope, rhs: FpyScope) -> FpyScope:
    """returns the two scopes, joined into one. if there is a conflict, chooses lhs over rhs"""
    lhs_keys = set(lhs.keys())
    rhs_keys = set(rhs.keys())
    common_keys = lhs_keys.intersection(rhs_keys)

    only_lhs_keys = lhs_keys.difference(common_keys)
    only_rhs_keys = rhs_keys.difference(common_keys)

    new = FpyScope()

    for key in common_keys:
        if not isinstance(lhs[key], dict) or not isinstance(rhs[key], dict):
            # cannot be merged cleanly. one of the two is not a scope
            print(f"WARNING: {key} is defined as {lhs[key]}, ignoring {rhs[key]}")
            new[key] = lhs[key]
            continue

        new[key] = union_scope(lhs[key], rhs[key])

    for key in only_lhs_keys:
        new[key] = lhs[key]
    for key in only_rhs_keys:
        new[key] = rhs[key]

    return new


@dataclass
class CompileState:
    """a collection of input, internal and output state variables and maps"""

    types: FpyScope
    """a scope whose leaf nodes are subclasses of BaseType"""
    functions: FpyScope
    """a scope whose leaf nodes are FpyFunction instances"""
    tlms: FpyScope
    """a scope whose leaf nodes are ChTemplates"""
    prms: FpyScope
    """a scope whose leaf nodes are PrmTemplates"""
    consts: FpyScope
    """a scope whose leaf nodes are instances of subclasses of BaseType"""
    variables: FpyScope = field(default_factory=dict)
    """a scope whose leaf nodes are FpyVariables"""
    runtime_values: FpyScope = None
    """a scope whose leaf nodes are tlms/prms/consts/variables, all of which
    have some value at runtime."""

    def __post_init__(self):
        self.runtime_values = union_scope(
            self.tlms,
            union_scope(self.prms, union_scope(self.consts, self.variables)),
        )

    expr_types: dict[AstExpr, FpyValueType] = field(default_factory=dict)
    """expr to its fprime type, or nothing type if none"""

    stack_op_directives: dict[AstOp, type[StackOpDirective]] = field(
        default_factory=dict
    )
    """some stack operation to which directive will be emitted for it"""

    type_coercions: dict[AstExpr, FpyValueType] = field(default_factory=dict)
    """expr to fprime type it must be converted into at runtime"""

    expr_values: dict[AstExpr, FpyValue] = field(default_factory=dict)
    """expr to its fpy value"""

    attribute_offsets: dict[AstGetAttr, int] = field(default_factory=dict)

    directives: dict[Ast, list[Directive] | None] = field(default_factory=dict)
    """a list of code generated by each node, or None/empty list if no directives"""

    node_dir_counts: dict[Ast, int] = field(default_factory=dict)
    """node to the number of directives generated by it"""

    lvar_array_size_bytes: int = 0
    """the size in bytes of the lvar array"""

    start_line_idx: dict[Ast, int] = field(default_factory=dict)
    """the line index at which each node's directives will be included in the output"""

    errors: list[CompileException] = field(default_factory=list)
    """a list of all compile exceptions generated by passes"""

    def err(self, msg, n):
        """adds a compile exception to internal state"""
        self.errors.append(CompileException(msg, n))


class Visitor:
    """visits each class, calling a custom visit function, if one is defined, for each
    node type"""

    def _find_custom_visit_func(self, node: Ast):
        for name, func in inspect.getmembers(type(self), inspect.isfunction):
            if not name.startswith("visit") or name == "visit_default":
                # not a visitor, or the default visit func
                continue
            signature = inspect.signature(func)
            params = list(signature.parameters.values())
            assert len(params) == 3
            assert params[1].annotation is not None
            annotations = typing.get_type_hints(func)
            param_type = annotations[params[1].name]
            if is_instance_compat(node, param_type):
                return func
        else:
            # call the default
            return type(self).visit_default

    def _visit(self, node: Ast, state: CompileState):
        visit_func = self._find_custom_visit_func(node)
        visit_func(self, node, state)

    def visit_default(self, node: Ast, state: CompileState):
        pass

    def run(self, start: Ast, state: CompileState):
        """runs the visitor, starting at the given node, descending depth-first"""

        def _descend(node: Ast):
            if not isinstance(node, Ast):
                return
            children = []
            for field in fields(node):
                field_val = getattr(node, field.name)
                if isinstance(field_val, list):
                    children.extend(field_val)
                else:
                    children.append(field_val)

            for child in children:
                if not isinstance(child, Ast):
                    continue
                _descend(child)
                if len(state.errors) != 0:
                    break
                self._visit(child, state)
                if len(state.errors) != 0:
                    break

        _descend(start)
        self._visit(start, state)


class TopDownVisitor(Visitor):

    def run(self, start: Ast, state: CompileState):
        """runs the visitor, starting at the given node, descending breadth-first"""

        def _descend(node: Ast):
            if not isinstance(node, Ast):
                return
            children = []
            for field in fields(node):
                field_val = getattr(node, field.name)
                if isinstance(field_val, list):
                    children.extend(field_val)
                else:
                    children.append(field_val)

            for child in children:
                if not isinstance(child, Ast):
                    continue
                self._visit(child, state)
                if len(state.errors) != 0:
                    break
                _descend(child)
                if len(state.errors) != 0:
                    break

        self._visit(start, state)
        _descend(start)
