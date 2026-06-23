"""Tests for parsing a SandboxResult into bugs (Phase 5, Unit 2).

The parser reads real-shaped pytest -q / Hypothesis output (D39): the `FAILED ... - <msg>`
short-summary lines (failed set + message) and `Falsifying example:` blocks (failing input).
Severity is crash vs property_violation (D40). No live Kestrel and no LLM.
"""

from typewright import results
from typewright.kestrel import SandboxResult
from typewright.models import (
    BugSeverity,
    DetectedProperty,
    PropertyAnalysis,
    PropertyClass,
)

ANALYSIS = PropertyAnalysis(
    detected=[
        DetectedProperty(
            property_class=PropertyClass.IDEMPOTENCE,
            relation="normalize(normalize(x)) == normalize(x)",
            rationale="r",
            confidence=0.9,
        ),
        DetectedProperty(
            property_class=PropertyClass.TOTALITY,
            relation="first_char(v) does not raise",
            rationale="r",
            confidence=0.6,
        ),
        DetectedProperty(
            property_class=PropertyClass.METAMORPHIC,
            relation="f(x) == f(g(x)) [A]",
            rationale="r",
            confidence=0.7,
        ),
        DetectedProperty(
            property_class=PropertyClass.METAMORPHIC,
            relation="f(x) <= f(x + 1) [B]",
            rationale="r",
            confidence=0.7,
        ),
    ],
    input_types={"x": "str"},
    return_type="str",
)


def _result(stdout="", *, exit_code=0, timed_out=False, stderr="", stdout_truncated=False):
    return SandboxResult(
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        duration_ms=10,
        timed_out=timed_out,
        stdout_truncated=stdout_truncated,
    )


SAMPLE_PASS = "..\n2 passed in 0.05s\n"

SAMPLE_ASSERT = (
    "F                                                                        [100%]\n"
    "=================================== FAILURES ===================================\n"
    "_______________________________ test_idempotence _______________________________\n"
    "    @given(x=st.text())\n"
    "    def test_idempotence(x):\n"
    ">       assert normalize(normalize(x)) == normalize(x)\n"
    "E       assert 'AA' == 'A'\n"
    "main.py:14: AssertionError\n"
    "Falsifying example: test_idempotence(\n"
    "    x='A',\n"
    ")\n"
    "=========================== short test summary info ============================\n"
    "FAILED main.py::test_idempotence - assert 'AA' == 'A'\n"
    "1 failed in 0.30s\n"
)

# A crash plus a mixed tally, to check severity=crash AND the pass/fail counts.
SAMPLE_MIXED = (
    "...F                                                                     [100%]\n"
    "Falsifying example: test_totality(v='')\n"
    "=========================== short test summary info ============================\n"
    "FAILED main.py::test_totality - IndexError: string index out of range\n"
    "1 failed, 3 passed in 0.40s\n"
)

# AssertionError with an explicit message (the prefixed form, vs SAMPLE_ASSERT's bare assert).
SAMPLE_ASSERT_MSG = (
    "F                                                                        [100%]\n"
    "Falsifying example: test_idempotence(x='Q')\n"
    "FAILED main.py::test_idempotence - AssertionError: not idempotent\n"
    "1 failed in 0.10s\n"
)

# Two failures of the same property class -> test_metamorphic and test_metamorphic_2.
SAMPLE_MULTI = (
    "FF                                                                       [100%]\n"
    "Falsifying example: test_metamorphic(x=0)\n"
    "Falsifying example: test_metamorphic_2(x=-1)\n"
    "=========================== short test summary info ============================\n"
    "FAILED main.py::test_metamorphic - assert 0 == 1\n"
    "FAILED main.py::test_metamorphic_2 - assert -1 == 0\n"
    "2 failed in 0.20s\n"
)

# Real pytest -q layout: Hypothesis's "Falsifying example:" block is rendered INSIDE the
# FAILURES traceback, so pytest prefixes every line with its "E   " marker. The hand-written
# SAMPLE_ASSERT omits that; the live smoke (2026-06-24) showed the parser leaking it as
# "E x='A' E". This reproduces the real shape.
SAMPLE_ASSERT_EPREFIX = (
    "F                                                                        [100%]\n"
    "=================================== FAILURES ===================================\n"
    "_____________________________ test_idempotence _____________________________\n"
    "    @given(x=st.text())\n"
    "    def test_idempotence(x):\n"
    ">       assert normalize(normalize(x)) == normalize(x)\n"
    "E       assert 'AA' == 'A'\n"
    "E       Falsifying example: test_idempotence(\n"
    "E           x='A',\n"
    "E       )\n"
    "main.py:14: AssertionError\n"
    "=========================== short test summary info ============================\n"
    "FAILED main.py::test_idempotence - assert 'AA' == 'A'\n"
    "1 failed in 0.30s\n"
)


def test_all_passed_yields_no_bugs():
    report = results.parse_results(_result(SAMPLE_PASS, exit_code=0), ANALYSIS)

    assert report.bugs == []
    assert report.tests_passed == 2
    assert report.timed_out is False


def test_property_violation_bare_assert():
    report = results.parse_results(_result(SAMPLE_ASSERT, exit_code=1), ANALYSIS)

    assert len(report.bugs) == 1
    bug = report.bugs[0]
    assert bug.test_name == "test_idempotence"
    assert bug.error == "AssertionError"
    assert bug.severity == BugSeverity.PROPERTY_VIOLATION
    assert bug.failing_input == "x='A'"  # multi-line example normalised to one line
    assert bug.violated_property == "normalize(normalize(x)) == normalize(x)"


def test_crash_is_classified_with_the_exception_type():
    report = results.parse_results(_result(SAMPLE_MIXED, exit_code=1), ANALYSIS)

    assert len(report.bugs) == 1
    bug = report.bugs[0]
    assert bug.error == "IndexError"
    assert bug.severity == BugSeverity.CRASH
    assert bug.failing_input == "v=''"
    assert bug.violated_property == "first_char(v) does not raise"


def test_counts_are_parsed_from_the_tally():
    report = results.parse_results(_result(SAMPLE_MIXED, exit_code=1), ANALYSIS)

    assert report.tests_passed == 3
    assert report.tests_failed == 1


def test_assertionerror_with_message_is_property_violation():
    report = results.parse_results(_result(SAMPLE_ASSERT_MSG, exit_code=1), ANALYSIS)

    assert report.bugs[0].error == "AssertionError"
    assert report.bugs[0].severity == BugSeverity.PROPERTY_VIOLATION


def test_repeated_class_maps_to_the_nth_relation():
    report = results.parse_results(_result(SAMPLE_MULTI, exit_code=1), ANALYSIS)

    by_name = {b.test_name: b for b in report.bugs}
    assert by_name["test_metamorphic"].violated_property == "f(x) == f(g(x)) [A]"
    assert by_name["test_metamorphic_2"].violated_property == "f(x) <= f(x + 1) [B]"


def test_timed_out_yields_no_bugs_but_flags_it():
    report = results.parse_results(_result("", exit_code=-1, timed_out=True), ANALYSIS)

    assert report.bugs == []
    assert report.timed_out is True


def test_output_truncation_is_surfaced():
    report = results.parse_results(
        _result(SAMPLE_ASSERT, exit_code=1, stdout_truncated=True), ANALYSIS
    )

    assert report.output_truncated is True


def test_scan_handles_brackets_inside_string_values():
    examples = results._extract_falsifying("Falsifying example: test_f(s=')(')\n")

    assert examples == {"test_f": "s=')('"}


def test_fallback_builds_bugs_when_summary_is_absent():
    text = "F\nFalsifying example: test_idempotence(x='Z')\n1 failed in 0.1s\n"
    report = results.parse_results(_result(text, exit_code=1), ANALYSIS)

    assert len(report.bugs) == 1
    assert report.bugs[0].failing_input == "x='Z'"
    assert report.bugs[0].severity == BugSeverity.PROPERTY_VIOLATION


def test_falsifying_example_with_pytest_E_prefix_is_stripped():
    # Regression for the live-smoke bug: the input must come back clean ("x='A'"),
    # not "E x='A' E", when the Falsifying block carries pytest's "E   " prefixes.
    report = results.parse_results(_result(SAMPLE_ASSERT_EPREFIX, exit_code=1), ANALYSIS)

    assert len(report.bugs) == 1
    assert report.bugs[0].failing_input == "x='A'"
    assert report.bugs[0].error == "AssertionError"
    assert report.bugs[0].severity == BugSeverity.PROPERTY_VIOLATION