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

    spenny(argv) ~cli
    add_neg    | ["add", "--amount", "-12.50"] | | 1 | ~matches "Amount must be positive"
    list_empty | ["list"] | "No expenses found." | 0
    bad_utf8   | [!bytes[0x80,0xff]] | "" | 1
""")


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
            "spenny",
        ]

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
        text = dedent("""\
            @spec S

            f(x)
            a | 1 | 2
        """)
        spec = parse_vmd(text)

        assert spec.arch_id == "S"
        assert spec.status == Status.DRAFT

    def test_arity_zero_group(self):
        text = dedent("""\
            @spec S

            version()
            v | "1.0"
        """)
        spec = parse_vmd(text)

        assert spec.groups[0].arity == 0
        assert spec.groups[0].cases[0].expected == "1.0"

    def test_ok_expected(self):
        text = dedent("""\
            @spec S

            init(path)
            opens | "test.db" | Ok
        """)
        spec = parse_vmd(text)

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


class TestVMDCliGroups:
    def test_cli_group(self):
        spec = parse_vmd(VALID_SPEC)
        cli = next(g for g in spec.groups if g.name == "spenny")

        assert cli.kind == "cli"
        assert len(cli.cli_cases) == 3

    def test_cli_channels(self):
        spec = parse_vmd(VALID_SPEC)
        cli = next(g for g in spec.groups if g.name == "spenny")

        add_neg = cli.cli_cases[0]
        assert add_neg.argv == ["add", "--amount", "-12.50"]
        assert add_neg.stdout is None
        assert add_neg.exit_code == 1
        assert add_neg.stderr == "Amount must be positive"
        assert add_neg.stderr_is_pattern

        list_empty = cli.cli_cases[1]
        assert list_empty.stdout == "No expenses found."
        assert not list_empty.stdout_is_pattern
        assert list_empty.exit_code == 0
        assert list_empty.stderr is None

    def test_cli_bytes_literal(self):
        spec = parse_vmd(VALID_SPEC)
        cli = next(g for g in spec.groups if g.name == "spenny")

        bad_utf8 = cli.cli_cases[2]
        assert bad_utf8.argv == [b"\x80\xff"]
        assert bad_utf8.stdout == ""
        assert bad_utf8.exit_code == 1

    def test_cli_bytes_decimal_values(self):
        text = dedent("""\
            @spec S

            tool(argv) ~cli
            a | [!bytes[128, 255]] | "" | 1
        """)
        spec = parse_vmd(text)

        assert spec.groups[0].cli_cases[0].argv == [b"\x80\xff"]

    def test_cli_no_checked_channel_rejected(self):
        text = dedent("""\
            @spec S

            tool(argv) ~cli
            a | ["x"]
        """)
        with pytest.raises(VMDParseError, match="at least one of stdout"):
            parse_vmd(text)

    def test_cli_bad_argv(self):
        text = dedent("""\
            @spec S

            tool(argv) ~cli
            a | ["x", 3] | "" | 0
        """)
        with pytest.raises(VMDParseError, match="argv elements must be JSON strings"):
            parse_vmd(text)

    def test_cli_bad_exit(self):
        text = dedent("""\
            @spec S

            tool(argv) ~cli
            a | ["x"] | "" | nope
        """)
        with pytest.raises(VMDParseError, match="exit: expected an integer"):
            parse_vmd(text)

    def test_cli_signature_must_be_argv(self):
        text = dedent("""\
            @spec S

            tool(args) ~cli
            a | ["x"] | "" | 0
        """)
        with pytest.raises(VMDParseError, match="must be 'tool\\(argv\\) ~cli'"):
            parse_vmd(text)

    def test_cli_mode_is_exclusive(self):
        text = dedent("""\
            @spec S

            tool(argv) ~cli ~matches
            a | ["x"] | "" | 0
        """)
        with pytest.raises(VMDParseError, match="cannot combine"):
            parse_vmd(text)

    def test_cli_invalid_pattern(self):
        text = dedent("""\
            @spec S

            tool(argv) ~cli
            a | ["x"] | ~matches "(unclosed" | 0
        """)
        with pytest.raises(VMDParseError, match="invalid ~matches pattern"):
            parse_vmd(text)


class TestVMDParserErrors:
    def test_missing_spec_directive(self):
        text = dedent("""\
            f(x)
            a | 1 | 2
        """)
        with pytest.raises(VMDParseError, match="Missing required directive: @spec"):
            parse_vmd(text)

    def test_invalid_status(self):
        text = dedent("""\
            @spec S
            @status bogus

            f(x)
            a | 1 | 2
        """)
        with pytest.raises(VMDParseError, match="Invalid status: 'bogus'"):
            parse_vmd(text)

    def test_no_groups(self):
        with pytest.raises(VMDParseError, match="No case groups"):
            parse_vmd("@spec S\n")

    def test_group_without_cases(self):
        text = dedent("""\
            @spec S

            f(x)
        """)
        with pytest.raises(VMDParseError, match="Group 'f': no case rows"):
            parse_vmd(text)

    def test_wrong_column_count(self):
        text = dedent("""\
            @spec S

            f(x, y)
            a | 1 | 2
        """)
        with pytest.raises(VMDParseError, match="rows need 4 columns"):
            parse_vmd(text)

    def test_non_json_cell(self):
        text = dedent("""\
            @spec S

            f(x)
            a | hello | 2
        """)
        with pytest.raises(VMDParseError, match="not valid JSON"):
            parse_vmd(text)

    def test_duplicate_case_name(self):
        text = dedent("""\
            @spec S

            f(x)
            a | 1 | 2
            a | 3 | 4
        """)
        with pytest.raises(VMDParseError, match="duplicate case name 'a'"):
            parse_vmd(text)

    def test_duplicate_group(self):
        text = dedent("""\
            @spec S

            f(x)
            a | 1 | 2

            f(y)
            b | 1 | 2
        """)
        with pytest.raises(VMDParseError, match="duplicate group 'f'"):
            parse_vmd(text)

    def test_duplicate_fixture(self):
        text = dedent("""\
            @spec S
            @fixture A = 1
            @fixture A = 2

            f(x)
            a | A | 2
        """)
        with pytest.raises(VMDParseError, match="duplicate fixture 'A'"):
            parse_vmd(text)

    def test_reserved_fixture_name(self):
        text = dedent("""\
            @spec S
            @fixture Ok = 1

            f(x)
            a | 1 | 2
        """)
        with pytest.raises(VMDParseError, match="reserved"):
            parse_vmd(text)

    def test_opaque_fixture_as_value(self):
        text = dedent("""\
            @spec S
            @fixture conn = !fresh db

            f(x)
            a | conn | 2
        """)
        with pytest.raises(VMDParseError, match="opaque fixture 'conn' cannot be used"):
            parse_vmd(text)

    def test_unknown_directive(self):
        text = dedent("""\
            @spec S
            @fixtur A = 1

            f(x)
            a | 1 | 2
        """)
        with pytest.raises(VMDParseError, match="unknown directive '@fixtur'"):
            parse_vmd(text)

    def test_duplicate_directive(self):
        text = dedent("""\
            @spec S
            @spec T

            f(x)
            a | 1 | 2
        """)
        with pytest.raises(VMDParseError, match="duplicate @spec"):
            parse_vmd(text)

    def test_unknown_mode(self):
        text = dedent("""\
            @spec S

            f(x) ~fuzzy
            a | 1 | 2
        """)
        with pytest.raises(VMDParseError, match="unknown mode ~fuzzy"):
            parse_vmd(text)

    def test_covers_without_group(self):
        text = dedent("""\
            @spec S

            f(x)
            a | 1 | 2

            @covers something
        """)
        with pytest.raises(VMDParseError, match="not followed by a group signature"):
            parse_vmd(text)

    def test_bad_signature(self):
        text = dedent("""\
            @spec S

            just some prose
        """)
        with pytest.raises(VMDParseError, match="expected a group signature"):
            parse_vmd(text)

    def test_duplicate_parameter(self):
        text = dedent("""\
            @spec S

            f(x, x)
            a | 1 | 2 | 3
        """)
        with pytest.raises(VMDParseError, match="duplicate parameter name 'x'"):
            parse_vmd(text)

    def test_unknown_parameter_type(self):
        text = dedent("""\
            @spec S

            f(x:datetime)
            a | 1 | 2
        """)
        with pytest.raises(VMDParseError, match="unknown parameter type 'datetime'"):
            parse_vmd(text)

    def test_decimal_column_rejects_non_numeric(self):
        text = dedent("""\
            @spec S

            f(amount:decimal)
            a | "abc" | 2
        """)
        with pytest.raises(VMDParseError, match="decimal"):
            parse_vmd(text)

    def test_matches_mode_needs_string_expected(self):
        text = dedent("""\
            @spec S

            f(x) ~matches
            a | 1 | 42
        """)
        with pytest.raises(VMDParseError, match="need a string"):
            parse_vmd(text)

    def test_error_messages_carry_line_numbers(self):
        text = dedent("""\
            @spec S

            f(x)
            a | 1 | 2
            b | nope | 2
        """)
        with pytest.raises(VMDParseError, match="line 5"):
            parse_vmd(text)

    def test_errors_are_batched(self):
        text = dedent("""\
            @spec S

            f(x)
            a | nope | 2
            a | 1 | 2 | 3
        """)
        with pytest.raises(VMDParseError) as exc_info:
            parse_vmd(text)
        errors = exc_info.value.errors
        assert any("not valid JSON" in e for e in errors)
        assert any("rows need 3 columns" in e for e in errors)


class TestVMDRoundTrip:
    def test_render_parse_round_trip(self):
        spec = parse_vmd(VALID_SPEC)
        rendered = render_vmd(spec)
        reparsed = parse_vmd(rendered)

        assert reparsed == spec

    def test_parse_file(self, tmp_path):
        path = tmp_path / "test.vmd"
        path.write_text(VALID_SPEC)

        spec = parse_vmd_file(path)
        assert spec.spec_id == "RELATIVE_TIME"


def _wrap(body: str) -> str:
    return f"@spec S\n\n{body}\n"


def _expect_error(text: str, match: str) -> None:
    with pytest.raises(VMDParseError) as exc_info:
        parse_vmd(text)
    assert any(match in e for e in exc_info.value.errors), exc_info.value.errors


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

    def test_cli_group_cannot_declare_return(self):
        _expect_error(
            _wrap('tool(argv) -> out ~cli\na | ["x"] | "" | 0'), "cannot declare a return"
        )

    def test_empty_parameter(self):
        _expect_error(_wrap("f(x, , y)\na | 1 | 2 | 3"), "empty parameter")

    def test_invalid_parameter_name(self):
        _expect_error(_wrap("f(9bad)\na | 1 | 2"), "invalid parameter name")


class TestVMDValueRowErrorBranches:
    def test_empty_input_cell(self):
        _expect_error(_wrap("f(x)\na |  | 2"), "empty cell")

    def test_invalid_case_name(self):
        _expect_error(_wrap("f(x)\nbad name | 1 | 2"), "invalid case name")

    def test_invalid_error_type(self):
        _expect_error(_wrap("f(x)\na | 1 | !9bad"), "invalid error type")

    def test_expected_cell_not_json(self):
        _expect_error(_wrap("f(x)\na | 1 | nope"), "expected: not valid JSON")

    def test_decimal_column_rejects_bool(self):
        _expect_error(_wrap("f(amount:decimal)\na | true | 2"), "decimal")

    def test_decimal_column_accepts_plain_number(self):
        spec = parse_vmd(_wrap("f(amount:decimal)\na | 5 | 2"))
        assert spec.groups[0].cases[0].inputs == [5]

    def test_escaped_quote_inside_string_keeps_pipe_protected(self):
        spec = parse_vmd(_wrap('f(x)\na | "quote \\" and | pipe" | 2'))
        assert spec.groups[0].cases[0].inputs == ['quote " and | pipe']


class TestVMDCliRowErrorBranches:
    def test_too_many_columns(self):
        _expect_error(_wrap('t(argv) ~cli\na | ["x"] | "" | 0 | "" | "extra"'), "2 to 5 columns")

    def test_invalid_cli_case_name(self):
        _expect_error(_wrap('t(argv) ~cli\nbad name | ["x"] | "" | 0'), "invalid case name")

    def test_duplicate_cli_case_name(self):
        _expect_error(
            _wrap('t(argv) ~cli\na | ["x"] | "" | 0\na | ["y"] | "" | 0'), "duplicate case name"
        )

    def test_argv_not_json(self):
        _expect_error(_wrap('t(argv) ~cli\na | nope | "" | 0'), "not a valid JSON array")

    def test_argv_not_a_list(self):
        _expect_error(_wrap('t(argv) ~cli\na | {"a": 1} | "" | 0'), "must be a JSON array")

    def test_empty_bytes_literal(self):
        _expect_error(_wrap('t(argv) ~cli\na | [!bytes[]] | "" | 0'), "malformed !bytes")

    def test_non_numeric_bytes_literal(self):
        _expect_error(_wrap('t(argv) ~cli\na | [!bytes[zz]] | "" | 0'), "malformed !bytes")

    def test_out_of_range_bytes_literal(self):
        _expect_error(_wrap('t(argv) ~cli\na | [!bytes[300]] | "" | 0'), "malformed !bytes")

    def test_bytes_token_inside_string_stays_literal(self):
        spec = parse_vmd(_wrap('t(argv) ~cli\na | ["!bytes[0x80]"] | "" | 0'))
        assert spec.groups[0].cli_cases[0].argv == ["!bytes[0x80]"]

    def test_matches_prefix_without_pattern(self):
        _expect_error(_wrap('t(argv) ~cli\na | ["x"] | ~matches | 0'), "needs a string pattern")

    def test_stdout_not_json(self):
        _expect_error(_wrap('t(argv) ~cli\na | ["x"] | nope | 0'), "expected a JSON string")

    def test_stdout_not_a_string(self):
        _expect_error(_wrap('t(argv) ~cli\na | ["x"] | 42 | 0'), "expected a JSON string")

    def test_stderr_not_a_string(self):
        _expect_error(
            _wrap('t(argv) ~cli\na | ["x"] | "" | 0 | 42'), "stderr: expected a JSON string"
        )


class TestVMDRendererBranches:
    def test_approx_tolerance_round_trips(self):
        spec = parse_vmd(_wrap("f(x) ~approx:0.5\na | 1 | 1.0"))
        rendered = render_vmd(spec)
        assert "~approx:0.5" in rendered
        assert parse_vmd(rendered) == spec

    def test_distinct_arch_round_trips(self):
        text = "@spec S\n@arch OTHER\n\nf(x)\na | 1 | 2\n"
        spec = parse_vmd(text)
        rendered = render_vmd(spec)
        assert "@arch OTHER" in rendered
        assert parse_vmd(rendered) == spec


class TestVMDRendererApproxWithoutTolerance:
    def test_bare_approx_round_trips(self):
        spec = parse_vmd("@spec S\n\nf(x) ~approx\na | 1 | 1.0\n")
        rendered = render_vmd(spec)
        assert "~approx" in rendered
        assert parse_vmd(rendered) == spec


class TestVMDRendererOkBranch:
    def test_ok_expected_round_trips(self):
        spec = parse_vmd('@spec S\n\ninit(path)\nopens | "db" | Ok\n')
        rendered = render_vmd(spec)
        assert "| Ok" in rendered
        assert parse_vmd(rendered) == spec


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
