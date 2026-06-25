"""Tests for diff -> changed-functions extraction (Phase 7), pure (no GitHub/network)."""

from typewright import diff

_CONTENT = (
    "def foo(x):\n"       # 1
    "    return x + 1\n"  # 2
    "\n"                  # 3
    "\n"                  # 4
    "def bar(y):\n"       # 5
    "    return y * 2\n"  # 6
)


def test_changed_line_numbers_single_hunk():
    patch = "@@ -1,2 +1,2 @@\n def foo(x):\n-    return x\n+    return x + 1\n"
    assert diff.changed_line_numbers(patch) == {2}


def test_changed_line_numbers_multi_hunk():
    patch = (
        "@@ -1,1 +1,2 @@\n line1\n+added2\n"
        "@@ -10,1 +11,2 @@\n line11\n+added12\n"
    )
    assert diff.changed_line_numbers(patch) == {2, 12}


def test_changed_line_numbers_added_file():
    assert diff.changed_line_numbers("@@ -0,0 +1,2 @@\n+def f():\n+    return 1\n") == {1, 2}


def test_changed_line_numbers_empty_or_none():
    assert diff.changed_line_numbers(None) == set()
    assert diff.changed_line_numbers("") == set()


def test_changed_functions_selects_intersecting():
    funcs = diff.changed_functions(_CONTENT, {2})  # line 2 is inside foo (lines 1-2)
    assert [f.name for f in funcs] == ["foo"]


def test_changed_functions_selects_multiple():
    funcs = diff.changed_functions(_CONTENT, {2, 6})  # foo and bar both touched
    assert sorted(f.name for f in funcs) == ["bar", "foo"]


def test_changed_functions_ignores_unchanged():
    assert diff.changed_functions(_CONTENT, {3}) == []  # a blank line between functions


def test_changed_functions_empty_changed_lines():
    assert diff.changed_functions(_CONTENT, set()) == []


def test_changed_functions_skips_unparseable_file():
    assert diff.changed_functions("def f(:\n    pass", {1}) == []