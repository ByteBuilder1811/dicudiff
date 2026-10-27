"""End-to-end demo against a running backend: uploads the sample PDFs, compares them, prints the findings.
Usage: python scripts/demo.py [http://127.0.0.1:8000]"""
import sys
import time
from pathlib import Path

import httpx

base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
S = Path(__file__).resolve().parents[2] / "data" / "samples"
c = httpx.Client(base_url=base, timeout=120)


def up(name, label):
    r = c.post("/api/documents/upload", files={"file": (name, (S / name).read_bytes(), "application/pdf")}, data={"label": label})
    r.raise_for_status()
    return r.json()["document"]["id"]


def compare(a, b, la, lb):
    cid = c.post("/api/comparisons", json={"doc_a_id": a, "doc_b_id": b, "label_a": la, "label_b": lb}).json()["id"]
    while (d := c.get(f"/api/comparisons/{cid}").json())["status"] in ("queued", "running"):
        time.sleep(0.5)
    print(f"\n{la} -> {lb}: {d['status']} {d['stats'].get('counts')}")
    for ch in c.get(f"/api/comparisons/{cid}/changes", params={"category": "MODIFIED,ADDED,REMOVED,NEEDS_REVIEW,POSSIBLY_RELOCATED"}).json()["items"]:
        print(f"  {ch['category']:<18} old p.{ch['old_page']} §{ch['old_section']} | new p.{ch['new_page']} §{ch['new_section']} | {ch['flags']}")
    return cid


c1 = compare(up("kyc_circular_v1.pdf", "KYC 2023"), up("kyc_circular_v2.pdf", "KYC 2024"), "KYC 2023", "KYC 2024")
c2 = compare(up("northwind_standard_v3.pdf", "NW v3"), up("northwind_standard_v4.pdf", "NW v4"), "NW v3", "NW v4")
r = c.post("/api/common-removals", json={"comparison_a_id": c1, "comparison_b_id": c2}).json()
print("\nCommon removals:", r["direction"], "| confirmed:", r["confirmed"])
for it in r["items"]:
    print("  -", it["status"], it["match_method"], it["evidence_a"]["text"][:80])
print("\nComparison ids:", c1, c2)
