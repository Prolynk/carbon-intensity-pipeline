"""Prove the data-quality tests can fail.

A suite that only ever passes tells you nothing. This runs the same tests against
`tests/fixtures/sample_with_defects.json`, which carries one planted instance of
each defect class, and checks that every planted defect tripped at least one of
the tests nominated for it in `planted_defects.json`.

    python scripts/verify_defect_detection.py

Exit code 0 if every planted defect was caught, 1 otherwise. Writes
docs/defect_detection_report.md, which is the evidence behind the CV number.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLANTED = ROOT / "tests" / "fixtures" / "planted_defects.json"
REPORT = ROOT / "docs" / "defect_detection_report.md"


def run_suite(target: str) -> tuple[set[str], int, int]:
    """Run the data-quality suite and return (failing test names, passed, failed)."""
    proc = subprocess.run(
        [
            sys.executable, "-m", "pytest", "tests/data_quality",
            f"--target={target}", "-p", "no:cacheprovider", "--tb=no", "-r", "f",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    failing = set()
    for line in proc.stdout.splitlines():
        if line.startswith("FAILED "):
            name = line.split(" ")[1].split("::")[-1].split("[")[0]
            failing.add(name)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    passed = int(m.group(1)) if (m := re.search(r"(\d+) passed", tail)) else 0
    failed = int(m.group(1)) if (m := re.search(r"(\d+) failed", tail)) else 0
    return failing, passed, failed


def main() -> int:
    planted = json.loads(PLANTED.read_text(encoding="utf-8"))

    print("Running the suite against the CLEAN fixture ...")
    clean_failing, clean_passed, clean_failed = run_suite("clean")
    print(f"  {clean_passed} passed, {clean_failed} failed")
    if clean_failing:
        print("  WARNING: tests failed on clean data, so the run below proves nothing:")
        for name in sorted(clean_failing):
            print(f"    {name}")

    print("\nRunning the suite against the DEFECT fixture ...")
    failing, passed, failed = run_suite("defects")
    print(f"  {passed} passed, {failed} failed\n")

    rows, caught = [], 0
    for defect in planted:
        hits = sorted(set(defect["expected_failing_tests"]) & failing)
        was_caught = bool(hits)
        caught += was_caught
        rows.append({**defect, "caught": was_caught, "caught_by": hits})
        mark = "CAUGHT  " if was_caught else "MISSED  "
        print(f"  {mark} {defect['id']}  {defect['defect']}")
        for hit in hits:
            print(f"             trips {hit}")
        if not was_caught:
            print(f"             expected one of {defect['expected_failing_tests']}")

    total = len(planted)
    print(f"\n{caught} of {total} planted defects caught")

    unexpected = failing - {t for d in planted for t in d["expected_failing_tests"]}
    if unexpected:
        print("\nAlso failing, not nominated for any planted defect:")
        for name in sorted(unexpected):
            print(f"  {name}")

    write_report(rows, caught, total, clean_passed, passed, failed, sorted(unexpected))
    print(f"\nreport written to {REPORT.relative_to(ROOT)}")
    return 0 if caught == total else 1


def write_report(rows, caught, total, clean_passed, passed, failed, unexpected) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Defect detection report",
        "",
        f"Generated {now} by `scripts/verify_defect_detection.py`. Do not edit by hand.",
        "",
        "## Result",
        "",
        f"- Tests passing on the clean fixture: **{clean_passed}**",
        f"- Running the same tests against the defect fixture: **{failed} failed, {passed} passed**",
        f"- Planted defects caught: **{caught} of {total}**",
        "",
        "## Defect by defect",
        "",
        "| ID | Planted defect | Period | Caught | Tripped |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        hits = "<br>".join(f"`{h}`" for h in r["caught_by"]) or "—"
        lines.append(
            f"| {r['id']} | {r['defect']} | `{r['period']}` | "
            f"{'yes' if r['caught'] else 'NO'} | {hits} |"
        )
    notes = [r for r in rows if r.get("note")]
    if notes:
        lines += ["", "## Notes", ""]
        for r in notes:
            lines.append(f"- **{r['id']} ({r['defect']})** — {r['note']}")
    if unexpected:
        lines += [
            "", "## Additional failures", "",
            "These tests also failed on the defect fixture without being nominated "
            "for a specific planted defect. They are collateral detections, which is "
            "a good sign rather than a bad one.", "",
        ]
        for name in unexpected:
            lines.append(f"- `{name}`")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
