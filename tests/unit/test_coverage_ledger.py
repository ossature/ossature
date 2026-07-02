from textwrap import dedent

from ossature.models.plan import Plan, PlanMeta, PlanTask
from ossature.models.shared import Status
from ossature.models.smd import Priority, Requirement, SMDSpec
from ossature.parsers.smd import _split_heading_markers, parse_smd
from ossature.parsers.vmd import parse_vmd
from ossature.renderer.smd import render_smd
from ossature.verification.ledger import build_coverage_ledger, format_coverage_issues

SMD_WITH_ANCHORS = dedent("""\
    ---
    id: EXPENSE_TRACKER
    status: draft
    priority: high
    ---

    # Spenny

    ## Overview

    A command-line expense tracker.

    ## Goals

    - Track expenses

    ## Non-Goals

    - GUI

    ## Requirements

    ### Add an Expense {#add-expense}

    Record a new expense.

    **Accepts:** amount, category

    **Returns:** the created record

    **Errors:**

    - Amount is zero or negative -> raise ValueError with a message
    - Category is empty -> raise ValueError with a message

    ### List Expenses

    Display recorded expenses.

    **Accepts:** optional filters

    **Returns:** matching records

    ### Helpers {#helpers .no-verify}

    Internal utilities.

    **Accepts:** N/A

    **Returns:** N/A

    ## Constraints

    - Standard library only

    ## Examples

    ### Basic

    **Input:**

    ```
    add 12.50 Food
    ```

    **Output:**

    ```
    Expense added
    ```

    ## Acceptance Criteria

    - Expenses persist
""")

VMD = dedent("""\
    @spec EXPENSE_TRACKER

    @covers add-expense
    add_expense(data, amount, category)
    ok       | {"expenses": []} | "12.50" | "Food" | {"id": 1}
    negative | {"expenses": []} | "-5.00" | "Food" | !ValueError: must be positive

    list_expenses(data)
    empty | {"expenses": []} | []
""")


def _plan_with_task(covers: list[str]) -> Plan:
    return Plan(
        meta=PlanMeta(generated_at="now", total_tasks=1, specs=["EXPENSE_TRACKER"]),
        tasks=[
            PlanTask(
                id="004",
                spec="EXPENSE_TRACKER",
                title="Storage roundtrip test",
                description="",
                outputs=["tests/test_storage.py"],
                depends_on=[],
                spec_refs=[],
                arch_refs=[],
                verify=["pytest tests/test_storage.py"],
                covers=covers,
            )
        ],
    )


class TestSMDAnchors:
    def test_anchor_parsed_and_stripped(self):
        spec = parse_smd(SMD_WITH_ANCHORS)

        add = spec.requirements[0]
        assert add.title == "Add an Expense"
        assert add.anchor == "add-expense"
        assert not add.no_verify

    def test_no_verify_marker(self):
        spec = parse_smd(SMD_WITH_ANCHORS)

        helpers = spec.requirements[2]
        assert helpers.title == "Helpers"
        assert helpers.anchor == "helpers"
        assert helpers.no_verify

    def test_plain_heading_unchanged(self):
        spec = parse_smd(SMD_WITH_ANCHORS)

        assert spec.requirements[1].title == "List Expenses"
        assert spec.requirements[1].anchor == ""

    def test_render_round_trip(self):
        spec = parse_smd(SMD_WITH_ANCHORS)
        reparsed = parse_smd(render_smd(spec))

        assert reparsed.requirements == spec.requirements

    def test_non_marker_braces_kept_in_title(self):
        title, anchor, no_verify = _split_heading_markers("Weird {braces} title")
        assert title == "Weird {braces} title"
        assert anchor == ""
        assert not no_verify

    def test_no_verify_without_anchor(self):
        title, anchor, no_verify = _split_heading_markers("Audio {.no-verify}")
        assert title == "Audio"
        assert anchor == ""
        assert no_verify


class TestCoverageLedger:
    def test_explicit_covers_by_anchor(self):
        smd = parse_smd(SMD_WITH_ANCHORS)
        vmd = parse_vmd(VMD)

        ledger = build_coverage_ledger([smd], [vmd])
        add = next(e for e in ledger.entries if e.anchor == "add-expense")

        assert add.covered
        assert add.groups == ["add_expense"]

    def test_inferred_group_name_match(self):
        smd = parse_smd(SMD_WITH_ANCHORS)
        vmd = parse_vmd(VMD)

        ledger = build_coverage_ledger([smd], [vmd])
        listing = next(e for e in ledger.entries if e.title == "List Expenses")

        assert listing.covered
        assert listing.groups == ["list_expenses"]

    def test_exempt_requirement(self):
        smd = parse_smd(SMD_WITH_ANCHORS)
        vmd = parse_vmd(VMD)

        ledger = build_coverage_ledger([smd], [vmd])
        helpers = next(e for e in ledger.entries if e.title == "Helpers")

        assert helpers.exempt
        assert helpers not in ledger.uncovered()

    def test_error_type_coverage(self):
        smd = parse_smd(SMD_WITH_ANCHORS)
        vmd = parse_vmd(VMD)

        ledger = build_coverage_ledger([smd], [vmd])
        add = next(e for e in ledger.entries if e.anchor == "add-expense")

        assert add.declared_error_types == ["ValueError"]
        assert add.covered_error_types == ["ValueError"]
        assert add.missing_error_types == []

    def test_missing_error_case_reported(self):
        smd = parse_smd(SMD_WITH_ANCHORS)
        vmd_text = VMD.replace("!ValueError: must be positive", '{"id": 2}')
        vmd = parse_vmd(vmd_text)

        ledger = build_coverage_ledger([smd], [vmd])
        add = next(e for e in ledger.entries if e.anchor == "add-expense")
        issues = format_coverage_issues(ledger)

        assert add.missing_error_types == ["ValueError"]
        assert any("ValueError" in i for i in issues.advisory)

    def test_task_covers(self):
        smd = parse_smd(SMD_WITH_ANCHORS)
        plan = _plan_with_task(covers=["List Expenses"])

        ledger = build_coverage_ledger([smd], [], plan)
        listing = next(e for e in ledger.entries if e.title == "List Expenses")

        assert listing.covered
        assert listing.tasks == ["task:004"]

    def test_dangling_covers_target(self):
        smd = parse_smd(SMD_WITH_ANCHORS)
        vmd_text = VMD.replace("@covers add-expense", "@covers nonexistent-thing")
        vmd = parse_vmd(vmd_text)

        ledger = build_coverage_ledger([smd], [vmd])

        assert any("nonexistent-thing" in d for d in ledger.dangling)

    def test_uncovered_requirement(self):
        smd = parse_smd(SMD_WITH_ANCHORS)

        ledger = build_coverage_ledger([smd], [])
        uncovered_titles = [e.title for e in ledger.uncovered()]

        assert "Add an Expense" in uncovered_titles
        assert "List Expenses" in uncovered_titles
        assert "Helpers" not in uncovered_titles

    def test_quoted_heading_covers_target(self):
        smd = parse_smd(SMD_WITH_ANCHORS)
        vmd_text = VMD.replace("@covers add-expense", '@covers "Add an Expense"')
        vmd = parse_vmd(vmd_text)

        ledger = build_coverage_ledger([smd], [vmd])
        add = next(e for e in ledger.entries if e.anchor == "add-expense")

        assert add.covered

    def test_untagged_group_with_no_match_is_soft(self):
        smd = SMDSpec(
            title="T",
            spec_id="S",
            status=Status.DRAFT,
            priority=Priority.HIGH,
            overview="o",
            requirements=[
                Requirement(title="Something", description="d", accepts="a", returns="r")
            ],
        )
        vmd = parse_vmd("@spec S\n\nunrelated(x)\na | 1 | 2\n")

        ledger = build_coverage_ledger([smd], [vmd])

        assert ledger.dangling == []
        assert len(ledger.uncovered()) == 1
