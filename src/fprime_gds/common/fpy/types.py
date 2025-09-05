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
    value: typing.Any

TypeFwdRef = None


class FpyType(FpyValue):

    def __init__(self, base: FpyType, name: str):
        super().__init__(TypeFwdRef, None)
        self.base = base
        self.name = name

    def check_subtype(self, other: FpyType):
        if self is Any:
            # all types are subtypes of any
            return True

        # the other type is a subtype iff
        # this type is the same type as the other type,
        # or one of other's parents is this type
        while other is not Any and other is not self:
            other = other.base

        return other is not Any

    def construct(self, *args):
        return FpyValue(self)


Type = FpyType(None, "Type")
# Type's base type is itself
Type.base = Type
# Type is the only type whose type is itself
Type.type = Type
# now update the fwd ref
TypeFwdRef = Type

Any = FpyType(None, "Any")
# Any's base type is itself
Any.base = Any

UnitType = FpyType(Any, "Unit")
# the one valid value of the unit type
Unit = UnitType.construct()

Callable = FpyType(Any, "Callable")

# TODO macros shouldn't be callables, they are a separate thing which need their
# own compiler pass
Macro = FpyType(Callable, "Macro")

Command = FpyType(Callable, "Command")


class FpyCallableType(FpyType):
    """a type representing an object which can be called with () syntax"""

    def construct(
        self, return_type: FpyType, args: list[tuple[str, FpyType]], action: typing.Any
    ):
        return FpyValue(self, (return_type, args, action))


@dataclass
class FpyCmd(FpyCallableType):
    cmd: CmdTemplate


@dataclass
class FpyMacro(FpyCallableType):
    dir: type[Directive]


@dataclass
class FpyTypeCtor(FpyCallableType):
    type: FpyType


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


MACROS: dict[str, FpyMacro] = {
    "sleep": FpyMacro(
        UnitType,
        [
            (
                "seconds",
                U32Type,
            ),
            ("microseconds", U32Type),
        ],
        WaitRelDirective,
    ),
    "sleep_until": FpyMacro(UnitType, [("wakeup_time", TimeType)], WaitAbsDirective),
    "exit": FpyMacro(UnitType, [("success", BoolType)], ExitDirective),
    "log": FpyMacro(F64Type, [("operand", F64Type)], FloatLogDirective),
}


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


def get_type_of_value(val: FpyValue) -> FpyValueType:
    """returns the type of the value, if it were to be evaluated as an expression"""

    if isinstance(val, type):
        # type of a type is "type"? idk we really shouldn't get here...
        assert False, val
        return type
    elif isinstance(val, FppValue):
        # constant value
        return type(val)
    elif isinstance(val, UnitValue):
        return UnitValue
    elif isinstance(val, FpyCallableType):
        return type(val)
    elif isinstance(val, FpyVariable):
        return val.type
    elif isinstance(val, dict):
        return type(val)
    elif isinstance(val, ChTemplate):
        return val.ch_type_obj
    elif isinstance(val, PrmTemplate):
        return val.prm_type_obj


@dataclass
class CompileState:
    """a collection of input, internal and output state variables and maps"""

    types: FpyScope
    """a scope whose leaf nodes are subclasses of BaseType"""
    callables: FpyScope
    """a scope whose leaf nodes are FpyCallable instances"""
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

    resolved_references: dict[AstReference, FpyReference] = field(
        default_factory=dict, repr=False
    )
    """reference to its singular resolution"""

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
