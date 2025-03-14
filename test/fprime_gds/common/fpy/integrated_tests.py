import tempfile

from pathlib import Path
import time
from fprime.common.models.serialize.time_type import TimeType
from fprime_gds.common.data_types.ch_data import ChData
from fprime_gds.common.fpy.serialize_bytecode import serialize_bytecode
from fprime_gds.common.testing_fw.api import IntegrationTestAPI
import fprime_gds.common.logger.test_logger

# disable excel logging.... wtf ew
fprime_gds.common.logger.test_logger.MODULE_INSTALLED = False
SEQ_MAX_STATEMENT_COUNT = 1024
MAX_LOCAL_VARIABLE_VALUE_SIZE = 499


def compile_seq(fprime_test_api, seq: str) -> Path:
    with tempfile.NamedTemporaryFile(suffix=".seq", delete=False) as fp:
        fp.write(seq.encode())
        input_path = Path(fp.name)
        output_path = input_path.with_suffix(".bin")

    serialize_bytecode(
        input_path, fprime_test_api.pipeline.dictionary_path, output_path
    )
    return output_path


def assert_compile_fails(fprime_test_api, seq: str):
    try:
        compile_seq(fprime_test_api, seq)
    except BaseException as e:
        return
    raise RuntimeError("compile_seq did not fail")


def assert_compile_succeeds(fprime_test_api, seq: str):
    try:
        return compile_seq(fprime_test_api, seq)
    except BaseException as e:
        raise RuntimeError("compile_seq failed") from e


def assert_run_succeeds(
    fprime_test_api: IntegrationTestAPI, seq_bin: Path, max_duration: float = 1.0
):
    fprime_test_api.send_and_assert_command(
        "Ref.fpySeq.RUN", [str(seq_bin), "BLOCK"], max_duration, int(max_duration)
    )


def assert_seq(
    fprime_test_api,
    seq,
    compile_success=True,
    run_success=True,
    min_runtime: float = 0.0,
    max_runtime: float = 1.0,
):
    if compile_success:
        bin = assert_compile_succeeds(fprime_test_api, seq)
    else:
        assert_compile_fails(fprime_test_api, seq)
        return

    if run_success:
        run_start = time.time()
        assert_run_succeeds(fprime_test_api, bin, max_runtime)
        runtime = time.time() - run_start
        assert runtime > min_runtime, (
            "Sequence only ran for " + str(runtime) + " seconds"
        )
    else:
        try:
            assert_run_succeeds(fprime_test_api, bin, max_runtime)
        except BaseException as e:
            # it failed... successfully
            return
        raise RuntimeError("Sequence was expected to fail but did not")


def get_dispatched_count(fprime_test_api: IntegrationTestAPI) -> int:

    t_pred = fprime_test_api.get_telemetry_pred(
        "Ref.fpySeq.StatementsDispatched", None, None
    )

    # wait 1 sec for ref deployment to telemeter it
    time.sleep(1)
    item: ChData = fprime_test_api.find_history_item(
        t_pred, fprime_test_api.telemetry_history, "NOW", 2
    )
    if item is None:
        raise RuntimeError("Unable to find dispatched statements count")
    return item.get_val()


def test_empty_seq(fprime_test_api: IntegrationTestAPI):
    seq = """
        
    ; testing cmt


    """
    assert_seq(fprime_test_api, seq, True, True)


def test_no_op(fprime_test_api: IntegrationTestAPI):
    seq = """
    Ref.cmdDisp.CMD_NO_OP
    Ref.cmdDisp.CMD_NO_OP_STRING "Hello World"
    Ref.cmdDisp.CMD_NO_OP
    """
    assert_seq(fprime_test_api, seq, True, True)

    fprime_test_api.assert_event_count(
        3, ["Ref.cmdDisp.NoOpReceived", "Ref.cmdDisp.NoOpStringReceived"]
    )


def test_largest_possible_seq(fprime_test_api: IntegrationTestAPI):
    seq = """
    Ref.cmdDisp.CMD_NO_OP
    """
    seq = "\n".join([seq] * SEQ_MAX_STATEMENT_COUNT)
    assert_seq(fprime_test_api, seq, True, True)

    # this is quite flaky--sometimes GDS captures them all, sometimes it doesn't
    fprime_test_api.assert_event_count(
        SEQ_MAX_STATEMENT_COUNT, ["Ref.cmdDisp.NoOpReceived"], timeout=1
    )


def test_too_big_seq(fprime_test_api: IntegrationTestAPI):
    seq = """
    Ref.cmdDisp.CMD_NO_OP
    """
    seq = "\n".join([seq] * (SEQ_MAX_STATEMENT_COUNT + 1))
    assert_seq(fprime_test_api, seq, True, False)


def test_wait_rel(fprime_test_api: IntegrationTestAPI):
    seq = """
    Ref.cmdDisp.CMD_NO_OP
    WAIT_REL 2, 0
    Ref.cmdDisp.CMD_NO_OP_STRING "Hello World"
    """
    pre = get_dispatched_count(fprime_test_api)
    assert_seq(fprime_test_api, seq, True, True, min_runtime=2, max_runtime=4)
    assert get_dispatched_count(fprime_test_api) - pre == 3


def test_wait_abs(fprime_test_api: IntegrationTestAPI):
    time = fprime_test_api.get_latest_time()

    seq = f"""
    WAIT_ABS {{ "time_base": 2, "time_context": 0, "seconds": {time.seconds + 5}, "useconds": 0 }}
    """
    # i see a lot of variability in this depending on tlm rates. cuz latest time just returns latest tlm timestamp
    # so it might be somewhat in the past
    assert_seq(fprime_test_api, seq, True, True, min_runtime=3.5, max_runtime=6.1)


def test_wait_abs_past(fprime_test_api: IntegrationTestAPI):
    time = fprime_test_api.get_latest_time()

    seq = f"""
    WAIT_ABS {{ "time_base": 2, "time_context": 0, "seconds": {time.seconds - 8}, "useconds": 0 }}
    """
    assert_seq(fprime_test_api, seq, True, True, min_runtime=0, max_runtime=2)


def test_wait_bad_base(fprime_test_api: IntegrationTestAPI):
    # timebase dont match, should fail
    time = fprime_test_api.get_latest_time()
    seq = f"""
    WAIT_ABS {{ "time_base": 0, "time_context": 0, "seconds": {time.seconds + 5}, "useconds": 0 }}
    """
    assert_seq(fprime_test_api, seq, True, False, min_runtime=0, max_runtime=2)


def test_wait_bad_context(fprime_test_api: IntegrationTestAPI):
    # timectx dont match, should fail
    time = fprime_test_api.get_latest_time()
    seq = f"""
    WAIT_ABS {{ "time_base": 2, "time_context": 123, "seconds": {time.seconds + 5}, "useconds": 0 }}
    """
    assert_seq(fprime_test_api, seq, True, False, min_runtime=0, max_runtime=2)


def test_run_then_cancel(fprime_test_api: IntegrationTestAPI):
    seq = """
    Ref.cmdDisp.CMD_NO_OP
    WAIT_REL 10, 0
    Ref.cmdDisp.CMD_NO_OP
    """
    bin = compile_seq(fprime_test_api, seq)

    fprime_test_api.send_command("Ref.fpySeq.RUN", [str(bin), "BLOCK"])
    time.sleep(2)
    fprime_test_api.send_and_assert_command("Ref.fpySeq.CANCEL")

    # make sure only one of the no ops got through
    fprime_test_api.assert_event_count(1, ["Ref.cmdDisp.NoOpReceived"])


def test_cancel_while_no_run(fprime_test_api: IntegrationTestAPI):
    fprime_test_api.send_command("Ref.fpySeq.CANCEL")

    # should fail
    fprime_test_api.assert_event_count(1, ["Ref.fpySeq.InvalidCommand"], timeout=1)


def test_validate_then_cancel(fprime_test_api: IntegrationTestAPI):
    seq = """
    Ref.cmdDisp.CMD_NO_OP
    WAIT_REL 10, 0
    Ref.cmdDisp.CMD_NO_OP
    """
    bin = compile_seq(fprime_test_api, seq)

    fprime_test_api.send_and_assert_command("Ref.fpySeq.VALIDATE", [str(bin)])
    fprime_test_api.send_and_assert_command("Ref.fpySeq.CANCEL")

    # sequence should be cancelled
    fprime_test_api.assert_event_count(1, ["Ref.fpySeq.SequenceCancelled"])


def test_run_validated(fprime_test_api: IntegrationTestAPI):
    seq = """
    Ref.cmdDisp.CMD_NO_OP
    """
    bin = compile_seq(fprime_test_api, seq)

    fprime_test_api.send_and_assert_command("Ref.fpySeq.VALIDATE", [str(bin)])
    fprime_test_api.send_and_assert_command("Ref.fpySeq.RUN_VALIDATED", ["BLOCK"])

    # sequence should be cancelled
    fprime_test_api.assert_event_count(1, ["Ref.cmdDisp.NoOpReceived"])


def test_no_block_run(fprime_test_api: IntegrationTestAPI):
    seq = """
    WAIT_REL 2, 0
    """
    bin = compile_seq(fprime_test_api, seq)

    # should return immediately
    fprime_test_api.send_and_assert_command(
        "Ref.fpySeq.RUN", [str(bin), "NO_BLOCK"], max_delay=1
    )


def test_run_twice(fprime_test_api: IntegrationTestAPI):
    seq = """
    WAIT_REL 10, 0
    """
    bin = compile_seq(fprime_test_api, seq)

    fprime_test_api.send_command("Ref.fpySeq.RUN", [str(bin), "BLOCK"])
    try:
        fprime_test_api.send_and_assert_command(
            "Ref.fpySeq.RUN", [str(bin), "BLOCK"], max_delay=1
        )
        assert False  # should have failed
    except BaseException as e:
        # failed successfully
        pass


def test_goto_idx(fprime_test_api: IntegrationTestAPI):
    seq = """
    GOTO 2
    Ref.cmdDisp.CMD_NO_OP
    """

    # make sure compiles and runs successfully
    assert_seq(fprime_test_api, seq, True, True)

    # should not have gotten any no ops
    fprime_test_api.assert_event_count(0, ["Ref.cmdDisp.NoOpReceived"])


def test_goto_tag(fprime_test_api: IntegrationTestAPI):
    seq = """
    GOTO "tag"
    Ref.cmdDisp.CMD_NO_OP
    tag:
    Ref.cmdDisp.CMD_NO_OP
    """

    # make sure compiles and runs successfully
    assert_seq(fprime_test_api, seq, True, True)

    # should have gotten one no op
    fprime_test_api.assert_event_count(1, ["Ref.cmdDisp.NoOpReceived"])


def test_goto_eof(fprime_test_api: IntegrationTestAPI):
    seq = """
    GOTO "end"
    Ref.cmdDisp.CMD_NO_OP
    Ref.cmdDisp.CMD_NO_OP
    end:
    """

    # make sure compiles and runs successfully
    assert_seq(fprime_test_api, seq, True, True)

    # should have gotten one no op
    fprime_test_api.assert_event_count(0, ["Ref.cmdDisp.NoOpReceived"])


def test_local_var_set(fprime_test_api: IntegrationTestAPI):
    seq = """
    SET_LOCAL_VAR 0, {"type": "bool", "value": true}
    """

    # make sure compiles and runs successfully
    assert_seq(fprime_test_api, seq, True, True)


def test_if_true(fprime_test_api: IntegrationTestAPI):
    seq = """
    SET_LOCAL_VAR 0, {"type": "bool", "value": true}
    IF 0, "else"
    Ref.cmdDisp.CMD_NO_OP
    GOTO "end"
    else:
    Ref.cmdDisp.CMD_NO_OP_STRING "should not happen"
    end:
    """

    assert_seq(fprime_test_api, seq, True, True)

    # should have executed the "true" case
    fprime_test_api.assert_event_count(1, ["Ref.cmdDisp.NoOpReceived"])
    fprime_test_api.assert_event_count(0, ["Ref.cmdDisp.NoOpStringReceived"])


def test_if_false(fprime_test_api: IntegrationTestAPI):
    seq = """
    SET_LOCAL_VAR 0, {"type": "bool", "value": false}
    IF 0, "else"
    Ref.cmdDisp.CMD_NO_OP
    GOTO "end"
    else:
    Ref.cmdDisp.CMD_NO_OP_STRING "should happen"
    end:
    """

    assert_seq(fprime_test_api, seq, True, True)

    # should have executed the "false" case
    fprime_test_api.assert_event_count(0, ["Ref.cmdDisp.NoOpReceived"])
    fprime_test_api.assert_event_count(1, ["Ref.cmdDisp.NoOpStringReceived"])


def test_local_var_set_bad_idx(fprime_test_api: IntegrationTestAPI):
    seq = """
    SET_LOCAL_VAR 255, {"type": "bool", "value": false}
    """

    # should compile cuz 255 is in range of U8, but should
    # fail to run because default max local var count is <255
    assert_seq(fprime_test_api, seq, True, False)


def test_local_var_set_string(fprime_test_api: IntegrationTestAPI):
    seq = """
    SET_LOCAL_VAR 0, {"type": "string", "value": "test string"}
    """

    assert_seq(fprime_test_api, seq, True, True)


def test_local_var_set_value_largest_possible(fprime_test_api: IntegrationTestAPI):
    # set a local variable to a string with the largest possible length
    # string is encoded as 2 byte len + chars following
    string_len = MAX_LOCAL_VARIABLE_VALUE_SIZE - 2
    seq = f"""
    SET_LOCAL_VAR 0, {{"type": "string", "value": "{"a" * (string_len)}"}}
    """

    # should work...
    assert_seq(fprime_test_api, seq, True, True)


def test_local_var_set_value_too_big(fprime_test_api: IntegrationTestAPI):
    # set a local variable to a string with the largest possible length + 1
    # string is encoded as 2 byte len + chars following. so instead of -2 here we do
    # -1
    string_len = MAX_LOCAL_VARIABLE_VALUE_SIZE - 1
    seq = f"""
    SET_LOCAL_VAR 0, {{"type": "string", "value": "{"a" * (string_len)}"}}
    """

    # should work...
    assert_seq(fprime_test_api, seq, True, False)


def test_local_var_set_value_way_too_big(fprime_test_api: IntegrationTestAPI):
    # max len of string type is 2^16
    string_len = 2**15
    seq = f"""
    SET_LOCAL_VAR 0, {{"type": "string", "value": "{"a" * (string_len)}"}}
    """

    # should fail. i think right now this is failing due to some other reason though
    # we can keep the test just for fun
    assert_seq(fprime_test_api, seq, True, False)


def test_local_var_set_bad_type(fprime_test_api: IntegrationTestAPI):
    seq = """
    SET_LOCAL_VAR 0, {"type": "unknown_asdfasdfasdf", "value": 8}
    """

    assert_seq(fprime_test_api, seq, False)


def test_stmt_buf_push(fprime_test_api: IntegrationTestAPI):
    seq = """
    SET_LOCAL_VAR 0, {"type": "string", "value": "dynamic string!!!"}
    STATEMENT_BUF_PUSH 0
    """

    assert_seq(fprime_test_api, seq, True, True)


def test_stmt_buf_pop(fprime_test_api: IntegrationTestAPI):
    seq = """
    SET_LOCAL_VAR 0, {"type": "string", "value": "dynamic string!!!"}
    STATEMENT_BUF_PUSH 0
    STATEMENT_BUF_POP 1, 1281
    """

    assert_seq(fprime_test_api, seq, True, True)
    fprime_test_api.assert_event_count(1, ["Ref.cmdDisp.NoOpStringReceived"])
