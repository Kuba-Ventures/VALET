"""file_index — pure ranking + kind/recency parsing (Stage 2).

The mdfind subprocess (find_files) is integration-only; here we test the pure
pieces: detect_kind splitting and rank_hits ordering with injected paths.

Run:  ./.venv/bin/python tests/test_file_index.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import file_index


def test_detect_kind_pdf():
    kind, name, recent = file_index.detect_kind("my q2 report pdf")
    assert kind == "pdf", kind
    assert name == "q2 report", name
    assert recent is False


def test_detect_kind_screenshot_recent():
    kind, name, recent = file_index.detect_kind("screenshots from yesterday")
    assert kind == "screenshot", kind
    assert recent is True, recent
    assert name == "", repr(name)  # all words were kind/recency/filler


def test_detect_kind_spreadsheet_alias():
    kind, name, _ = file_index.detect_kind("the budget excel")
    assert kind == "spreadsheet", kind
    assert name == "budget", name


def test_detect_kind_plain_name():
    kind, name, recent = file_index.detect_kind("q2 report")
    assert kind is None
    assert name == "q2 report"
    assert recent is False


def test_rank_exact_name_and_home_win():
    home = "/Users/x"
    paths = [
        "/Library/Caches/Q2 Report copy.pdf",
        "/Users/x/Documents/Q2 Report.pdf",
        "/Users/x/Downloads/old/something-q2-ish.pdf",
    ]
    hits = file_index.rank_hits(paths, "q2 report", home=home)
    order = [h.path for h in hits]
    # exact name in the user's home wins outright
    assert hits[0].path == "/Users/x/Documents/Q2 Report.pdf", order
    # the /Library hit is penalized below that exact home match
    assert order.index("/Library/Caches/Q2 Report copy.pdf") > 0, order


def test_rank_prefix_beats_substring():
    home = "/Users/x"
    paths = [
        "/Users/x/notes/old budget review.txt",   # substring
        "/Users/x/budget 2026.numbers",           # prefix
    ]
    hits = file_index.rank_hits(paths, "budget", home=home)
    assert hits[0].path == "/Users/x/budget 2026.numbers", [h.path for h in hits]


def test_rank_shallower_path_breaks_ties():
    home = "/Users/x"
    paths = [
        "/Users/x/a/b/c/report.pdf",
        "/Users/x/report.pdf",
    ]
    hits = file_index.rank_hits(paths, "report", home=home)
    assert hits[0].path == "/Users/x/report.pdf", [h.path for h in hits]


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
