"""Cross-checks the benchmark scoring (scripts/benchmark.score_pair) against an independent implementation
(docs/benchmarks/recompute_metrics.py) and against hand-computed numbers, and re-scores the committed fresh-split dumps."""
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH_DIR = ROOT / "docs" / "benchmarks"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


recompute = _load("recompute_metrics", BENCH_DIR / "recompute_metrics.py")
bench = _load("benchmark_script", ROOT / "backend" / "scripts" / "benchmark.py")  # main() is guarded; import is side-effect free


def R(cat, old=None, new=None):
    return {"category": cat, "old_text": old, "new_text": new}


A1, A2 = "The bank shall add alpha within 5 days of onboarding.", "The bank shall add beta at least once every 6 months."
D1, D2 = "The bank shall remove gamma and keep a copy for 7 years.", "The bank shall remove delta within 8 days of onboarding."
M1o, M1n = "The bank shall change epsilon within 9 days of onboarding.", "The bank shall change epsilon within 12 days of onboarding."
M2o, M2n = "The bank shall change zeta within 10 days of onboarding.", "The bank may change zeta within 10 days of onboarding."
U1 = "The bank shall keep eta unchanged within 11 days of onboarding."
TRUTH = {"added": [A1, A2], "removed": [D1, D2], "modified": [(M1o, M1n), (M2o, M2n)], "moved": []}
OLD_SECTIONS = [("Head", [D1, D2, M1o, M2o, U1])]
RECS = [
    R("UNCHANGED", "Head", "Head"),
    R("ADDED", None, "2.1 " + A1),                     # TP added (with clause number, PDF line break below)
    R("NEEDS_REVIEW", "1.2 " + D2, "1.4 " + A2),        # gold removed x gold added sent to review: strict FN for both
    R("REMOVED", "1.1 " + D1.replace(" and ", "\nand "), None),  # TP removed despite line break
    R("MODIFIED", "1.3 " + M1o, "1.3 " + M1n),          # TP modified
    R("NEEDS_REVIEW", "1.4 " + M2o, "1.5 " + M2n),      # gold modified sent to review: strict FN, surfaced
    R("UNCHANGED", "1.5 " + U1, "1.6 " + U1),
    R("ADDED", None, "9.9 A spurious clause that is not in the gold."),  # FP added
    R("ADDED", None, "2.1 " + A1),                     # duplicate record => FP (multiset semantics)
]


def test_hand_computed_strict_and_surfaced_numbers():
    s = bench.score_pair(RECS, TRUTH, OLD_SECTIONS)
    assert (s["add_tp"], s["add_fp"], s["add_fn"]) == (1, 2, 1)
    assert (s["rem_tp"], s["rem_fp"], s["rem_fn"]) == (1, 0, 1)
    assert (s["mod_tp"], s["mod_fp"], s["mod_fn"]) == (1, 0, 1)
    assert (s["add_surfaced"], s["rem_surfaced"], s["mod_surfaced"]) == (2, 2, 2)
    assert (s["review"], s["review_true_modified_pair"], s["review_removed_x_added"], s["review_other"]) == (2, 1, 1, 0)
    assert (s["unc_n"], s["unc_ok"], s["head_n"], s["head_ok"]) == (1, 1, 1, 1)
    assert bench.prf(1, 2, 1) == (0.333, 0.5, 0.4)


def test_independent_recompute_agrees_with_benchmark_scoring():
    ind = recompute.score_seed(RECS, TRUTH)
    s = bench.score_pair(RECS, TRUTH, OLD_SECTIONS)
    for kind, pre in (("added", "add"), ("removed", "rem"), ("modified", "mod")):
        assert (ind[kind]["tp"], ind[kind]["fp"], ind[kind]["fn"]) == (s[f"{pre}_tp"], s[f"{pre}_fp"], s[f"{pre}_fn"])
        assert ind[kind]["tp"] + ind[kind]["surfaced_extra"] == s[f"{pre}_surfaced"]
    assert ind["needs_review"] == s["review"]


def test_needs_review_never_counts_as_false_positive():
    only_review = [R("NEEDS_REVIEW", D1, A1)]
    s = bench.score_pair(only_review, {"added": [A1], "removed": [D1], "modified": [], "moved": []}, [("H", [D1])])
    assert s["add_fp"] == s["rem_fp"] == s["mod_fp"] == 0 and s["add_fn"] == s["rem_fn"] == 1
    assert s["add_surfaced"] == s["rem_surfaced"] == 1


@pytest.mark.parametrize("backend", ["hashing", "minilm"])
def test_committed_fresh_split_results_rescore_identically(backend):
    dump, res = BENCH_DIR / f"fresh_{backend}_records.json", BENCH_DIR / f"fresh_{backend}.json"
    if not dump.exists() or not res.exists():
        pytest.skip("fresh-split dump not generated")
    ind = recompute.score_dump(json.loads(dump.read_text()))
    stored = json.loads(res.read_text())
    for kind in ("added", "removed", "modified"):
        for m in ("precision", "recall", "f1"):
            assert ind[kind]["strict"][m] == stored[kind][m], (kind, m)
        assert ind[kind]["surfaced"]["recall"] == stored[kind]["surfaced_recall_incl_needs_review"]
