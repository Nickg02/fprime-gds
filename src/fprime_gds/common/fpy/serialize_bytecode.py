from __future__ import annotations
from dataclasses import astuple
import inspect
import json
from pathlib import Path
from argparse import ArgumentParser
import struct
import zlib
from fprime_gds.common.fpy.types import (
    StatementTemplate,
    StatementData,
    Header,
    Footer,
    HEADER_FORMAT,
    FOOTER_FORMAT,
    StatementType,
    DirectiveOpcode,
    BytecodeParseContext,
)
from fprime_gds.common.loaders.cmd_json_loader import CmdJsonLoader
from fprime_gds.common.loaders.ch_json_loader import ChJsonLoader
from fprime_gds.common.loaders.json_loader import PRIMITIVE_TYPE_MAP
from fprime.common.models.serialize.array_type import ArrayType
from fprime.common.models.serialize.bool_type import BoolType
from fprime.common.models.serialize.enum_type import EnumType
from fprime.common.models.serialize.numerical_types import (
    F32Type,
    F64Type,
    I8Type,
    I16Type,
    I32Type,
    I64Type,
    U8Type,
    U16Type,
    U32Type,
    U64Type,
)
from fprime.common.models.serialize.serializable_type import SerializableType
from fprime.common.models.serialize.string_type import StringType
from fprime.common.models.serialize.time_type import TimeBase, TimeType
from fprime.common.models.serialize.type_base import BaseType, ValueType


def get_type_obj_for(type: str) -> type[ValueType]:
    if type == "FwOpcodeType":
        return U32Type
    elif type == "FwSizeStoreType":
        return U16Type
    elif type == "FwChanIdType":
        return U32Type
    elif type == "FwPrmIdType":
        return U32Type

    raise RuntimeError("Unknown FPrime type alias " + str(type))


def serialize_statement(stmt: StatementData) -> bytes:
    # see https://github.com/nasa/fprime/issues/3023#issuecomment-2693051677
    # TODO replace this with actual documentation

    # type: U8 (0 if directive, 1 if cmd)
    # opcode: FwOpcodeType (default U32)
    # argBufSize: FwSizeStoreType (default U16)
    # argBuf: X bytes

    output = bytes()
    output += U8Type(stmt.template.statement_type.value).serialize()
    output += get_type_obj_for("FwOpcodeType")(stmt.template.opcode).serialize()

    arg_bytes = bytes()
    for arg in stmt.arg_values:
        arg_bytes += arg.serialize()

    output += get_type_obj_for("FwSizeStoreType")(len(arg_bytes)).serialize()
    output += arg_bytes

    return output


def parse_str_as_statement(
    stmt: str, templates: list[StatementTemplate], context: BytecodeParseContext
) -> StatementData:
    name = stmt.split()[0]
    args = stmt[len(name) :]

    args = json.loads("[" + args + "]")

    matching_template = [t for t in templates if t.name == name]
    if len(matching_template) != 1:
        # no unique match
        if len(matching_template) == 0:
            raise RuntimeError("Could not find command or directive " + str(name))
        raise RuntimeError(
            "Found multiple commands or directives with name " + str(name)
        )
    matching_template = matching_template[0]

    arg_values = []
    if len(args) < len(matching_template.args):
        raise RuntimeError(
            "Missing arguments for statement "
            + str(matching_template.name)
            + ": "
            + str(matching_template.args[len(args) :])
        )
    if len(args) > len(matching_template.args):
        raise RuntimeError(
            "Extra arguments for"
            + str(matching_template.name)
            + ": "
            + str(args[len(matching_template.args) :])
        )
    for index, arg_json in enumerate(args):
        arg_type = matching_template.args[index]
        if inspect.isclass(arg_type):
            # it's a type. instantiate it with the json
            arg_value = arg_type(arg_json)
        else:
            # it's a function. give it the json and the ctx
            arg_value = arg_type(arg_json, context)
        arg_values.append(arg_value)

    return StatementData(matching_template, arg_values)


def time_type_from_json(js, ctx: BytecodeParseContext):
    return TimeType(js["time_base"], js["time_context"], js["seconds"], js["useconds"])


def arbitrary_type_from_json(js, ctx: BytecodeParseContext):
    type_name = js["type"]

    if type_name == "string":
        # by default no max size restrictions in the bytecode
        return StringType.construct_type(f"String", None)(js["value"])

    # try first checking parsed_types, then check primitive types
    type_class = ctx.types.get(type_name, PRIMITIVE_TYPE_MAP.get(type_name, None))
    if type_class is None:
        raise RuntimeError("Unknown type " + str(type_name))

    return type_class(js["value"])


def goto_tag_or_idx_from_json(js, ctx: BytecodeParseContext):
    if isinstance(js, str):
        # it's a tag
        if js not in ctx.goto_tags:
            raise RuntimeError("Unknown goto tag " + str(js))
        return U32Type(ctx.goto_tags[js])

    # otherwise it is a statement index
    return U32Type(js)


def tlm_chan_id_from_json(js, ctx: BytecodeParseContext):
    if isinstance(js, str):
        if js not in ctx.channels:
            raise RuntimeError("Unknown telemetry channel " + str(js))
        return get_type_obj_for("FwChanIdType")(ctx.channels[js].id)
    elif isinstance(js, int):
        matching = [tmp for tmp in ctx.channels.keys() if tmp.id == js]
        if len(matching) != 1:
            if len(matching) == 0:
                raise RuntimeError("Unknown telemetry channel id " + str(js))
            raise RuntimeError("Multiple matches for telemetry channel id " + str(js))
        matching = matching[0]
        return get_type_obj_for("FwChanIdType")(matching.id)


directives: list[StatementTemplate] = [
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.WAIT_REL.value,
        "WAIT_REL",
        [U32Type, U32Type],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.WAIT_ABS.value,
        "WAIT_ABS",
        [time_type_from_json],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.SET_LOCAL_VAR.value,
        "SET_LOCAL_VAR",
        [U8Type, arbitrary_type_from_json],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.GOTO.value,
        "GOTO",
        [goto_tag_or_idx_from_json],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.IF.value,
        "IF",
        [U8Type, goto_tag_or_idx_from_json],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.STATEMENT_BUF_PUSH.value,
        "STATEMENT_BUF_PUSH",
        [U8Type],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.STATEMENT_BUF_POP.value,
        "STATEMENT_BUF_POP",
        [U8Type, get_type_obj_for("FwOpcodeType")],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.GET_TLM_VAL.value,
        "GET_TLM_VAL",
        [tlm_chan_id_from_json, U8Type],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.GET_TLM_TIME.value,
        "GET_TLM_TIME",
        [tlm_chan_id_from_json, U8Type],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.GET_PRM_VAL.value,
        "GET_PRM_VAL",
        [get_type_obj_for("FwPrmIdType"), U8Type],
    ),
    StatementTemplate(
        StatementType.DIRECTIVE,
        DirectiveOpcode.EQ_U64_U64.value,
        "EQ_U64_U64",
        [U8Type, U8Type, U8Type],
    ),
]


def main():
    arg_parser = ArgumentParser()
    arg_parser.add_argument(
        "input", type=Path, help="The path to the input .fpybc file"
    )

    arg_parser.add_argument(
        "-d",
        "--dictionary",
        type=Path,
        help="The JSON topology dictionary to compile against",
        required=True,
    )

    arg_parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="The output .bin file path. Defaults to the input file path with a .bin extension",
        default=None,
    )

    args = arg_parser.parse_args()

    if not args.input.exists():
        print("Input file", args.input, "does not exist")
        exit(1)

    if not args.dictionary.exists():
        print("Dictionary file", args.dictionary, "does not exist")
        exit(1)

    serialize_bytecode(args.input, args.dictionary, args.output)


def serialize_bytecode(input: Path, dictionary: Path, output: Path = None):

    cmd_json_dict_loader = CmdJsonLoader(str(dictionary))
    (cmd_id_dict, cmd_name_dict, versions) = cmd_json_dict_loader.construct_dicts(
        str(dictionary)
    )

    stmt_templates = []
    stmt_templates.extend(directives)
    for cmd_template in cmd_name_dict.values():
        stmt_template = StatementTemplate(
            StatementType.CMD,
            cmd_template.opcode,
            cmd_template.get_full_name(),
            [arg[2] for arg in cmd_template.arguments],
        )
        stmt_templates.append(stmt_template)

    stmts = []

    tlm_json_loader = ChJsonLoader(str(dictionary))
    (cmd_id_dict, cmd_name_dict, versions) = tlm_json_loader.construct_dicts(
        str(dictionary)
    )

    context = BytecodeParseContext()
    context.types = cmd_json_dict_loader.parsed_types
    context.channels = cmd_name_dict

    input_lines = input.read_text().splitlines()
    input_lines = [line.strip() for line in input_lines]
    # remove comments and empty lines
    input_lines = [
        line for line in input_lines if not line.startswith(";") and len(line) > 0
    ]

    goto_tags = {}
    statement_idx = 0
    statements = []
    for stmt_idx, stmt in enumerate(input_lines):
        if stmt.endswith(":"):
            # it's a goto tag
            goto_tags[stmt[:-1]] = statement_idx
        else:
            statements.append(stmt)
            statement_idx += 1

    context.goto_tags = goto_tags

    for stmt_idx, stmt in enumerate(statements):
        try:
            stmt_data = parse_str_as_statement(stmt, stmt_templates, context)
            stmts.append(stmt_data)
        except BaseException as e:
            raise RuntimeError(
                "Exception while parsing statement index " + str(stmt_idx) + ": " + stmt
            ) from e

    output_bytes = bytes()

    for stmt in stmts:
        output_bytes += serialize_statement(stmt)

    header = Header(0, 0, 0, 1, 0, len(stmts), len(output_bytes))
    output_bytes = struct.pack(HEADER_FORMAT, *astuple(header)) + output_bytes

    crc = zlib.crc32(output_bytes) % (1 << 32)
    footer = Footer(crc)
    output_bytes += struct.pack(FOOTER_FORMAT, *astuple(footer))

    if output is None:
        output = input.with_suffix(".bin")

    output.write_bytes(output_bytes)


if __name__ == "__main__":
    main()
