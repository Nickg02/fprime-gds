from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import struct
from fprime.common.models.serialize.type_base import BaseType

from fprime_gds.common.templates.ch_template import ChTemplate

class StatementType(Enum):
    DIRECTIVE = 0
    CMD = 1


@dataclass
class StatementTemplate:
    statement_type: StatementType
    opcode: int
    name: str
    args: list[type[BaseType]]


@dataclass
class StatementData:
    template: StatementTemplate
    arg_values: list[BaseType]


HEADER_FORMAT = "!BBBBBHI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)


@dataclass
class Header:
    majorVersion: int
    minorVersion: int
    patchVersion: int
    schemaVersion: int
    argumentCount: int
    statementCount: int
    bodySize: int


FOOTER_FORMAT = "!I"
FOOTER_SIZE = struct.calcsize(FOOTER_FORMAT)


@dataclass
class Footer:
    crc: int


class DirectiveOpcode(Enum):
    INVALID = 0
    WAIT_REL = 0x00000001
    WAIT_ABS = 0x00000002
    SET_LOCAL_VAR = 0x00000003
    GOTO = 0x00000004
    IF = 0x00000005
    STATEMENT_BUF_PUSH = 0x00000006
    STATEMENT_BUF_POP = 0x00000007
    GET_TLM_VAL = 0x00000008
    GET_TLM_TIME = 0x00000009
    GET_PRM_VAL = 0x0000000a
    EQ_U64_U64 = 0x0000000b

@dataclass
class BytecodeParseContext:
    goto_tags: map[str, int] = field(default_factory=dict)
    """a map of tag name with tag statement index"""
    types: map[str, type[BaseType]] = field(default_factory=dict)
    """a map of name to all parsed types available in the dictionary"""
    channels: map[str, ChTemplate] = field(default_factory=dict)
    """a map of name to ChTemplate object for all tlm channels"""

