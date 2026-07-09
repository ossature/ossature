import re
from textwrap import dedent

import pytest

from ossature.models.shared import Status
from ossature.parsers.vmd import VMDParseError, parse_vmd, parse_vmd_file
from ossature.renderer.vmd import render_vmd, save_vmd

VALID_SPEC = dedent("""\
    @spec RELATIVE_TIME
    @arch RELATIVE_TIME
    @status draft

    @fixture REF = 1704067200
    @fixture conn = !fresh empty sqlite

    # reference is 2024-01-01T00:00:00Z on every row

    @covers timeago
    timeago(timestamp, reference) -> str
    just_now   | 1704067200   | REF | "just now"
    one_minute | 1704067155   | REF | "1 minute ago"    # trailing comment
    bad_input  | "not-a-date" | REF | !ValueError
    pipe_str   | "a|b"        | REF | "with | pipe"

    parse_duration(text) -> int
    compact | "2h30m" | 9000
    empty   | ""      | !ValueError: empty

    duration(seconds) -> str ~struct ~decimal
    inf | Infinity | !ValueError

    add(conn, url, desc, tags) -> id
    empty_url | "" | "x" | "" | !InvalidInput: url must not be empty

    summarize(data, amount:decimal) -> out ~approx:0.01
    close | {"a": 1} | "12.50" | 25.0

    @covers timeago
    scenario formats a two minute difference:
    given ts = 1704067110
    when timeago(ts, REF)
    then returns "2 minutes ago"

    scenario deleting a missing expense fails:
    given data = {"next_id": 1, "expenses": []}
    when delete_expense(data, 99)
    then raises KeyError: not found

    scenario rejects invalid utf-8 argv:
    when $ yep hello \\xc3\\x28
    then exit 1
    then stderr has "UTF-8"

    scenario add then list round trip:
    when $ spenny add --amount 12.50 --category Food
    > Expense added: #1
    when $ spenny list
    then stdout has "12.50"
""")


def _wrap(body: str) -> str:
    return f"@spec S\n\n{body}\n"


def _expect_error(text: str, match: str) -> None:
    with pytest.raises(VMDParseError) as exc_info:
        parse_vmd(text)
    assert any(match in e for e in exc_info.value.errors), exc_info.value.errors


class TestVMDParser:
    def test_parse_valid_spec(self):
        spec = parse_vmd(VALID_SPEC)

        assert spec.spec_id == "RELATIVE_TIME"
        assert spec.arch_id == "RELATIVE_TIME"
        assert spec.status == Status.DRAFT
        assert [g.name for g in spec.groups] == [
            "timeago",
            "parse_duration",
            "duration",
            "add",
            "summarize",
        ]
        assert len(spec.scenarios) == 4

    def test_fixtures(self):
        spec = parse_vmd(VALID_SPEC)

        ref = next(f for f in spec.fixtures if f.name == "REF")
        assert not ref.opaque
        assert ref.value == 1704067200
        assert ref.raw == "1704067200"

        conn = next(f for f in spec.fixtures if f.name == "conn")
        assert conn.opaque
        assert conn.label == "fresh empty sqlite"

    def test_value_fixture_substitutes_column(self):
        spec = parse_vmd(VALID_SPEC)
        timeago = spec.groups[0]

        case = timeago.cases[0]
        assert case.inputs == [1704067200, 1704067200]
        assert case.raw_inputs == ["1704067200", "1704067200"]

    def test_opaque_fixture_consumes_no_column(self):
        spec = parse_vmd(VALID_SPEC)
        add = next(g for g in spec.groups if g.name == "add")

        assert [p.name for p in add.params] == ["conn", "url", "desc", "tags"]
        assert add.params[0].opaque_fixture == "conn"
        assert add.arity == 3
        assert add.cases[0].inputs == ["", "x", ""]

    def test_covers_attaches_to_next_group(self):
        spec = parse_vmd(VALID_SPEC)

        assert spec.groups[0].covers == ["timeago"]
        assert spec.groups[1].covers == []

    def test_signature_returns_and_modes(self):
        spec = parse_vmd(VALID_SPEC)
        timeago = spec.groups[0]
        duration = next(g for g in spec.groups if g.name == "duration")
        summarize = next(g for g in spec.groups if g.name == "summarize")

        assert timeago.returns == "str"
        assert timeago.compare_modes == []
        assert duration.compare_modes == ["struct", "decimal"]
        assert summarize.compare_modes == ["approx"]
        assert summarize.approx_tol == 0.01

    def test_decimal_column(self):
        spec = parse_vmd(VALID_SPEC)
        summarize = next(g for g in spec.groups if g.name == "summarize")

        assert summarize.params[1].type == "decimal"
        assert summarize.cases[0].inputs == [{"a": 1}, "12.50"]

    def test_error_expectations(self):
        spec = parse_vmd(VALID_SPEC)
        timeago = spec.groups[0]
        parse_duration = spec.groups[1]

        bad = timeago.cases[2]
        assert bad.expect_kind == "error"
        assert bad.error_type == "ValueError"
        assert bad.error_message == ""

        empty = parse_duration.cases[1]
        assert empty.error_type == "ValueError"
        assert empty.error_message == "empty"

    def test_nonfinite_input(self):
        spec = parse_vmd(VALID_SPEC)
        duration = next(g for g in spec.groups if g.name == "duration")

        assert duration.cases[0].inputs[0] == float("inf")

    def test_pipe_and_hash_inside_strings(self):
        spec = parse_vmd(VALID_SPEC)
        case = spec.groups[0].cases[3]

        assert case.inputs[0] == "a|b"
        assert case.expected == "with | pipe"

    def test_trailing_comment_stripped(self):
        spec = parse_vmd(VALID_SPEC)
        case = spec.groups[0].cases[1]

        assert case.expected == "1 minute ago"

    def test_comment_line_does_not_end_group(self):
        text = dedent("""\
            @spec S

            f(x)
            a | 1 | 2
            # a comment between rows
            b | 3 | 4
        """)
        spec = parse_vmd(text)

        assert [c.name for c in spec.groups[0].cases] == ["a", "b"]

    def test_arch_defaults_to_spec(self):
        spec = parse_vmd("@spec S\n\nf(x)\na | 1 | 2\n")

        assert spec.arch_id == "S"
        assert spec.status == Status.DRAFT

    def test_arity_zero_group(self):
        spec = parse_vmd('@spec S\n\nversion()\nv | "1.0"\n')

        assert spec.groups[0].arity == 0
        assert spec.groups[0].cases[0].expected == "1.0"

    def test_ok_expected(self):
        spec = parse_vmd('@spec S\n\ninit(path)\nopens | "test.db" | Ok\n')

        assert spec.groups[0].cases[0].expect_kind == "ok"

    def test_same_name_different_arity_allowed(self):
        text = dedent("""\
            @spec S

            duration(seconds)
            a | 1 | "1s"

            duration(seconds, compact)
            b | 1 | true | "1s"
        """)
        spec = parse_vmd(text)

        assert len(spec.groups) == 2


class TestVMDScenarios:
    def test_names_and_slugs(self):
        spec = parse_vmd(VALID_SPEC)

        assert [s.slug for s in spec.scenarios] == [
            "formats_a_two_minute_difference",
            "deleting_a_missing_expense_fails",
            "rejects_invalid_utf_8_argv",
            "add_then_list_round_trip",
        ]
        assert spec.scenarios[0].name == "formats a two minute difference"

    def test_covers_attaches_to_scenario(self):
        spec = parse_vmd(VALID_SPEC)

        assert spec.scenarios[0].covers == ["timeago"]
        assert spec.scenarios[1].covers == []

    def test_call_scenario_with_given_and_fixture_args(self):
        spec = parse_vmd(VALID_SPEC)
        scenario = spec.scenarios[0]

        assert scenario.kind == "call"
        assert scenario.givens[0].name == "ts"
        assert scenario.givens[0].value == 1704067110
        call = scenario.call
        assert call is not None
        assert call.target == "timeago"
        assert call.args == [1704067110, 1704067200]
        assert call.expect_kind == "value"
        assert call.expected == "2 minutes ago"

    def test_call_scenario_raises(self):
        spec = parse_vmd(VALID_SPEC)
        call = spec.scenarios[1].call

        assert call is not None
        assert call.expect_kind == "error"
        assert call.error_type == "KeyError"
        assert call.error_message == "not found"
        assert call.args == [{"next_id": 1, "expenses": []}, 99]

    def test_command_scenario_with_bytes_and_stderr(self):
        spec = parse_vmd(VALID_SPEC)
        scenario = spec.scenarios[2]

        assert scenario.kind == "command"
        step = scenario.steps[0]
        assert step.argv == ["yep", "hello", b"\xc3\x28"]
        assert step.exit_code == 1
        assert step.stderr_mode == "has"
        assert step.stderr == "UTF-8"
        assert step.stdout_mode == ""

    def test_command_sequence_with_output_lines(self):
        spec = parse_vmd(VALID_SPEC)
        scenario = spec.scenarios[3]

        assert scenario.kind == "command"
        assert len(scenario.steps) == 2
        first, second = scenario.steps
        assert first.argv == ["spenny", "add", "--amount", "12.50", "--category", "Food"]
        assert first.exit_code == 0
        assert first.stdout_lines == ["Expense added: #1"]
        assert second.stdout_mode == "has"
        assert second.stdout == "12.50"

    def test_quoted_words_and_escapes(self):
        spec = parse_vmd(
            '@spec S\n\nscenario quoting:\nwhen $ tool "two words" a\\"b\nthen exit 0\n'
        )
        step = spec.scenarios[0].steps[0]

        assert step.argv == ["tool", "two words", 'a"b']

    def test_multiline_output_with_blank_and_hash(self):
        text = dedent("""\
            @spec S

            scenario listing:
            when $ tool list
            > ID   Name # not a comment
            >
            > 2    done
        """)
        spec = parse_vmd(text)
        step = spec.scenarios[0].steps[0]

        assert step.stdout_lines == ["ID   Name # not a comment", "", "2    done"]

    def test_stdout_empty_and_stderr_is(self):
        text = dedent("""\
            @spec S

            scenario quiet:
            when $ tool run
            then stdout empty
            then stderr is "done"
        """)
        spec = parse_vmd(text)
        step = spec.scenarios[0].steps[0]

        assert step.stdout_mode == "empty"
        assert step.stdout is None
        assert step.stderr_mode == "is"
        assert step.stderr == "done"

    def test_then_ok(self):
        spec = parse_vmd("@spec S\n\nscenario touches:\nwhen init(1)\nthen ok\n")

        call = spec.scenarios[0].call
        assert call is not None
        assert call.expect_kind == "ok"

    def test_scenario_only_file_is_valid(self):
        spec = parse_vmd("@spec S\n\nscenario a:\nwhen $ t x\nthen exit 0\n")

        assert spec.groups == []
        assert len(spec.scenarios) == 1


class TestVMDScenarioErrors:
    def test_scenario_without_when(self):
        _expect_error(_wrap("scenario empty:"), "no when step")

    def test_call_without_then(self):
        _expect_error(_wrap("scenario nt:\nwhen f(1)"), "has no then")

    def test_mixed_step_kinds(self):
        _expect_error(_wrap("scenario mix:\nwhen f(1)\nwhen $ tool x"), "mixes call and command")

    def test_second_call_when(self):
        _expect_error(_wrap("scenario two:\nwhen f(1)\nwhen g(2)"), "exactly one when step")

    def test_given_after_when(self):
        _expect_error(
            _wrap("scenario late:\nwhen f(1)\ngiven x = 1\nthen ok"),
            "given steps come before",
        )

    def test_duplicate_given(self):
        _expect_error(
            _wrap("scenario dup:\ngiven x = 1\ngiven x = 2\nwhen f(x)\nthen ok"),
            "duplicate given",
        )

    def test_given_unknown_fixture(self):
        _expect_error(_wrap("scenario fx:\ngiven MISSING\nwhen f(1)\nthen ok"), "unknown fixture")

    def test_given_bad_json(self):
        _expect_error(_wrap("scenario bad:\ngiven x = {oops\nwhen f(x)\nthen ok"), "not valid JSON")

    def test_call_unknown_arg_name(self):
        _expect_error(_wrap("scenario an:\nwhen f(mystery)\nthen ok"), "unknown name 'mystery'")

    def test_then_before_when(self):
        _expect_error(_wrap("scenario tb:\nthen exit 0"), "needs a preceding when")

    def test_bad_call_then(self):
        _expect_error(_wrap("scenario bt:\nwhen f(1)\nthen explodes"), "expected 'then returns")

    def test_bad_command_then(self):
        _expect_error(_wrap("scenario bc:\nwhen $ t x\nthen explodes"), "expected 'then exit N'")

    def test_second_call_then(self):
        _expect_error(
            _wrap("scenario t2:\nwhen f(1)\nthen ok\nthen returns 1"),
            "has one then",
        )

    def test_duplicate_exit(self):
        _expect_error(
            _wrap("scenario de:\nwhen $ t x\nthen exit 1\nthen exit 2"), "duplicate 'then exit'"
        )

    def test_duplicate_stdout_check(self):
        _expect_error(
            _wrap('scenario ds:\nwhen $ t x\nthen stdout has "a"\nthen stdout is "b"'),
            "duplicate 'then stdout'",
        )

    def test_output_line_without_when(self):
        _expect_error(_wrap("scenario ol:\n> hello"), "'>' output lines need")

    def test_output_lines_conflict_with_stdout_check(self):
        _expect_error(
            _wrap('scenario oc:\nwhen $ t x\nthen stdout has "a"\n> hello'),
            "mutually exclusive",
        )

    def test_shell_metachars_rejected(self):
        _expect_error(_wrap("scenario sh:\nwhen $ t x | grep y"), "shell features")

    def test_unterminated_quote(self):
        _expect_error(_wrap('scenario uq:\nwhen $ t "unclosed'), "unterminated quote")

    def test_invalid_escape(self):
        _expect_error(_wrap("scenario ie:\nwhen $ t \\q"), "invalid escape")

    def test_empty_command(self):
        _expect_error(_wrap("scenario ec:\nwhen $"), "command is empty")

    def test_duplicate_scenario_name(self):
        _expect_error(
            _wrap("scenario same:\nwhen f(1)\nthen ok\n\nscenario same:\nwhen g(1)\nthen ok"),
            "duplicates the scenario",
        )

    def test_unnameable_scenario(self):
        _expect_error(_wrap("scenario !!!:\nwhen f(1)\nthen ok"), "at least one word")

    def test_unexpected_step_line(self):
        _expect_error(_wrap("scenario ux:\nwhen f(1)\nthn ok"), "inside a scenario")

    def test_bad_exit_value(self):
        _expect_error(_wrap("scenario be:\nwhen $ t x\nthen exit soon"), "needs an integer")

    def test_stream_check_needs_string(self):
        _expect_error(_wrap("scenario ss:\nwhen $ t x\nthen stdout has 42"), "needs a JSON string")

    def test_empty_takes_no_value(self):
        _expect_error(_wrap('scenario ev:\nwhen $ t x\nthen stdout empty "x"'), "takes no value")

    def test_invalid_matches_pattern(self):
        _expect_error(
            _wrap('scenario ip:\nwhen $ t x\nthen stderr matches "(unclosed"'),
            "invalid matches pattern",
        )

    def test_opaque_fixture_as_call_argument(self):
        _expect_error(
            "@spec S\n@fixture conn = !db\n\nscenario oa:\nwhen f(conn)\nthen ok\n",
            "opaque fixture 'conn' cannot be used as a call argument",
        )

    def test_cli_mode_points_to_scenarios(self):
        _expect_error(
            _wrap('yep(argv) ~cli\nbad | ["x"] | "" | 1'),
            "replaced by scenarios",
        )

    def test_call_returns_needs_json(self):
        _expect_error(
            _wrap("scenario rj:\nwhen f(1)\nthen returns oops"),
            "'then returns' needs a JSON value",
        )

    def test_call_raises_needs_valid_type(self):
        _expect_error(_wrap("scenario rt:\nwhen f(1)\nthen raises 9bad"), "invalid error type")

    def test_bad_when_form(self):
        _expect_error(
            _wrap("scenario bw:\nwhen just prose"),
            "expected 'when target(args)' or 'when $ command'",
        )


class TestVMDDirectiveErrorBranches:
    def test_directive_without_value(self):
        _expect_error("@spec\n\nf(x)\na | 1 | 2\n", "@spec needs a value")

    def test_invalid_spec_id(self):
        _expect_error("@spec bad id!\n\nf(x)\na | 1 | 2\n", "invalid @spec id")

    def test_invalid_arch_id(self):
        _expect_error("@spec S\n@arch bad!\n\nf(x)\na | 1 | 2\n", "invalid @arch id")

    def test_malformed_fixture(self):
        _expect_error("@spec S\n@fixture broken\n\nf(x)\na | 1 | 2\n", "malformed @fixture")

    def test_invalid_fixture_name(self):
        _expect_error("@spec S\n@fixture 9bad = 1\n\nf(x)\na | 1 | 2\n", "invalid fixture name")

    def test_opaque_fixture_without_label(self):
        _expect_error(
            "@spec S\n@fixture conn = !\n\nf(x)\na | 1 | 2\n", "needs a constructor label"
        )

    def test_fixture_value_not_json(self):
        _expect_error("@spec S\n@fixture A = {bad\n\nf(x)\na | 1 | 2\n", "not valid JSON")

    def test_covers_without_target(self):
        _expect_error(_wrap("@covers\nf(x)\na | 1 | 2"), "needs at least one target")

    def test_covers_empty_target(self):
        _expect_error(_wrap("@covers a, ,b\nf(x)\na | 1 | 2"), "empty @covers target")

    def test_covers_malformed_quoted_target(self):
        _expect_error(_wrap('@covers "unterminated\nf(x)\na | 1 | 2'), "malformed quoted")

    def test_covers_empty_quoted_target(self):
        _expect_error(_wrap('@covers ""\nf(x)\na | 1 | 2'), "non-empty string")

    def test_covers_invalid_slug(self):
        _expect_error(_wrap("@covers bad target!\nf(x)\na | 1 | 2"), "invalid @covers target")

    def test_covers_quoted_target_with_comma(self):
        spec = parse_vmd(_wrap('@covers "Add, then list"\nf(x)\na | 1 | 2'))
        assert spec.groups[0].covers == ["Add, then list"]

    def test_dangling_covers(self):
        _expect_error(_wrap("f(x)\na | 1 | 2\n\n@covers thing"), "not followed by a group")

    def test_unknown_directive(self):
        _expect_error("@spec S\n@fixtur A = 1\n\nf(x)\na | 1 | 2\n", "unknown directive")

    def test_duplicate_directive(self):
        _expect_error("@spec S\n@spec T\n\nf(x)\na | 1 | 2\n", "duplicate @spec")

    def test_reserved_fixture_name(self):
        _expect_error("@spec S\n@fixture Ok = 1\n\nf(x)\na | 1 | 2\n", "reserved")

    def test_duplicate_fixture(self):
        _expect_error(
            "@spec S\n@fixture A = 1\n@fixture A = 2\n\nf(x)\na | A | 2\n", "duplicate fixture"
        )

    def test_missing_spec_directive(self):
        _expect_error("f(x)\na | 1 | 2\n", "Missing required directive: @spec")

    def test_invalid_status(self):
        _expect_error("@spec S\n@status bogus\n\nf(x)\na | 1 | 2\n", "Invalid status: 'bogus'")

    def test_no_groups_or_scenarios(self):
        _expect_error("@spec S\n", "No case groups or scenarios")


class TestVMDSignatureErrorBranches:
    def test_invalid_group_name(self):
        _expect_error(_wrap("9func(x)\na | 1 | 2"), "invalid group name")

    def test_unexpected_text_after_signature(self):
        _expect_error(_wrap("f(x) trailing junk\na | 1 | 2"), "unexpected text after signature")

    def test_non_mode_token_after_modes(self):
        _expect_error(_wrap("f(x) ~struct oops\na | 1 | 2"), "expected a ~mode token")

    def test_invalid_approx_tolerance(self):
        _expect_error(_wrap("f(x) ~approx:abc\na | 1 | 2"), "invalid ~approx tolerance")

    def test_mode_with_unexpected_argument(self):
        _expect_error(_wrap("f(x) ~struct:5\na | 1 | 2"), "takes no argument")

    def test_duplicate_mode(self):
        _expect_error(_wrap("f(x) ~struct ~struct\na | 1 | 2"), "duplicate mode")

    def test_unknown_mode(self):
        _expect_error(_wrap("f(x) ~fuzzy\na | 1 | 2"), "unknown mode ~fuzzy")

    def test_empty_parameter(self):
        _expect_error(_wrap("f(x, , y)\na | 1 | 2 | 3"), "empty parameter")

    def test_invalid_parameter_name(self):
        _expect_error(_wrap("f(9bad)\na | 1 | 2"), "invalid parameter name")

    def test_duplicate_parameter(self):
        _expect_error(_wrap("f(x, x)\na | 1 | 2 | 3"), "duplicate parameter name")

    def test_unknown_parameter_type(self):
        _expect_error(_wrap("f(x:datetime)\na | 1 | 2"), "unknown parameter type")

    def test_bad_signature(self):
        _expect_error(_wrap("just some prose"), "expected a group signature")

    def test_duplicate_group(self):
        _expect_error(_wrap("f(x)\na | 1 | 2\n\nf(y)\nb | 1 | 2"), "duplicate group 'f'")

    def test_group_without_cases(self):
        _expect_error(_wrap("f(x)"), "Group 'f': no case rows")


class TestVMDValueRowErrorBranches:
    def test_empty_input_cell(self):
        _expect_error(_wrap("f(x)\na |  | 2"), "empty cell")

    def test_invalid_case_name(self):
        _expect_error(_wrap("f(x)\nbad name | 1 | 2"), "invalid case name")

    def test_duplicate_case_name(self):
        _expect_error(_wrap("f(x)\na | 1 | 2\na | 3 | 4"), "duplicate case name 'a'")

    def test_wrong_column_count(self):
        _expect_error(_wrap("f(x, y)\na | 1 | 2"), "rows need 4 columns")

    def test_non_json_cell(self):
        _expect_error(_wrap("f(x)\na | hello | 2"), "not valid JSON")

    def test_invalid_error_type(self):
        _expect_error(_wrap("f(x)\na | 1 | !9bad"), "invalid error type")

    def test_expected_cell_not_json(self):
        _expect_error(_wrap("f(x)\na | 1 | nope"), "expected: not valid JSON")

    def test_decimal_column_rejects_bool(self):
        _expect_error(_wrap("f(amount:decimal)\na | true | 2"), "decimal")

    def test_decimal_column_rejects_word_string(self):
        _expect_error(_wrap('f(amount:decimal)\na | "abc" | 2'), "decimal")

    def test_decimal_column_accepts_plain_number(self):
        spec = parse_vmd(_wrap("f(amount:decimal)\na | 5 | 2"))
        assert spec.groups[0].cases[0].inputs == [5]

    def test_matches_mode_needs_string_expected(self):
        _expect_error(_wrap("f(x) ~matches\na | 1 | 42"), "need a string")

    def test_opaque_fixture_as_value(self):
        _expect_error(
            "@spec S\n@fixture conn = !fresh db\n\nf(x)\na | conn | 2\n",
            "opaque fixture 'conn' cannot be used",
        )

    def test_escaped_quote_inside_string_keeps_pipe_protected(self):
        spec = parse_vmd(_wrap('f(x)\na | "quote \\" and | pipe" | 2'))
        assert spec.groups[0].cases[0].inputs == ['quote " and | pipe']

    def test_error_messages_carry_line_numbers(self):
        _expect_error(_wrap("f(x)\na | 1 | 2\nb | nope | 2"), "line 5")


class TestVMDRoundTrip:
    def test_render_parse_round_trip(self):
        spec = parse_vmd(VALID_SPEC)
        rendered = render_vmd(spec)
        reparsed = parse_vmd(rendered)

        assert reparsed == spec

    def test_bare_approx_round_trips(self):
        spec = parse_vmd("@spec S\n\nf(x) ~approx\na | 1 | 1.0\n")
        rendered = render_vmd(spec)
        assert "~approx" in rendered
        assert parse_vmd(rendered) == spec

    def test_distinct_arch_round_trips(self):
        text = "@spec S\n@arch OTHER\n\nf(x)\na | 1 | 2\n"
        spec = parse_vmd(text)
        rendered = render_vmd(spec)
        assert "@arch OTHER" in rendered
        assert parse_vmd(rendered) == spec

    def test_ok_expected_round_trips(self):
        spec = parse_vmd('@spec S\n\ninit(path)\nopens | "db" | Ok\n')
        rendered = render_vmd(spec)
        assert "| Ok" in rendered
        assert parse_vmd(rendered) == spec

    def test_bytes_word_round_trips(self):
        spec = parse_vmd("@spec S\n\nscenario b:\nwhen $ yep \\xff\\xfe\nthen exit 1\n")
        rendered = render_vmd(spec)
        assert "\\xff\\xfe" in rendered
        assert parse_vmd(rendered) == spec

    def test_printable_bytes_word_round_trips(self):
        # \x41 is 'A': the renderer must keep at least one escape or the
        # word would reparse as a plain string.
        spec = parse_vmd("@spec S\n\nscenario p:\nwhen $ tool \\x41B\nthen exit 2\n")
        assert spec.scenarios[0].steps[0].argv[1] == b"AB"
        assert parse_vmd(render_vmd(spec)) == spec

    def test_quoted_word_round_trips(self):
        spec = parse_vmd('@spec S\n\nscenario q:\nwhen $ tool "two words"\nthen exit 2\n')
        assert parse_vmd(render_vmd(spec)) == spec

    def test_given_fixture_reference_round_trips(self):
        text = (
            '@spec S\n\n@fixture BASE = {"a": 1}\n\n'
            "scenario g:\ngiven BASE\nwhen f(BASE, 2)\nthen returns 3\n"
        )
        spec = parse_vmd(text)
        assert parse_vmd(render_vmd(spec)) == spec

    def test_parse_file(self, tmp_path):
        path = tmp_path / "test.vmd"
        path.write_text(VALID_SPEC)

        spec = parse_vmd_file(path)
        assert spec.spec_id == "RELATIVE_TIME"

    def test_matches_pattern_is_usable(self):
        spec = parse_vmd('@spec S\n\nscenario m:\nwhen $ t x\nthen stderr matches "(?i)utf-?8"\n')
        step = spec.scenarios[0].steps[0]
        assert step.stderr is not None
        assert re.search(step.stderr, "invalid UTF-8 argument")


class TestSaveVmd:
    def test_saves_parseable_file(self, tmp_path):
        spec = parse_vmd("@spec S\n\nf(x)\na | 1 | 2\n")
        path = tmp_path / "s.vmd"

        save_vmd(spec, path)

        assert parse_vmd_file(path) == spec

    def test_refuses_to_overwrite_by_default(self, tmp_path):
        spec = parse_vmd("@spec S\n\nf(x)\na | 1 | 2\n")
        path = tmp_path / "s.vmd"
        path.write_text("existing")

        with pytest.raises(FileExistsError):
            save_vmd(spec, path)

    def test_overwrite_flag_replaces_file(self, tmp_path):
        spec = parse_vmd("@spec S\n\nf(x)\na | 1 | 2\n")
        path = tmp_path / "s.vmd"
        path.write_text("existing")

        save_vmd(spec, path, overwrite=True)

        assert parse_vmd_file(path) == spec
