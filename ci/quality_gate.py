"""Ranking quality gate: the INSTALLED package must not rank worse than the recorded baseline on a fixed sample of
hard held-out test steps (sites never seen in training). Needs the repo's data/ (test split and pages).

    python ci/quality_gate.py [--n 120] [--record]     (--record: write the result as the new baseline)
"""
import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "ci" / "baseline.json"
TOLERANCE = 0.02  # allowed drop in hit@1 / hit@10 (sampling noise on ~120 steps is about ±4 points; same sample each run)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--record", action="store_true")
    a = ap.parse_args()
    import elem_rank
    from elem_rank.common import doc
    from elem_rank.lite import LiteRanker
    from elem_rank.snapshot import demote_orphans
    try:
        from elem_rank.locate import rank_step  # 0.1.5+: the same step scoring find() uses
    except ImportError:
        rank_step = lambda rk, q, docs, key: rk.rank(q, docs, key)
    assert "site-packages" in elem_rank.__file__, "run with the installed wheel, not the source tree"

    rows = [json.loads(x) for x in open(REPO / "data/split/test.jsonl")]
    rows = [r for r in rows if r["qtype"] == "paraphrase" or r["source"] == "m2w"]  # the hard set
    random.Random(7).shuffle(rows)
    rows = sorted(rows[: a.n], key=lambda r: r["page"])
    need = {r["page"] for r in rows}
    pages = {}
    for f in ("pages_crawl.jsonl", "pages_m2w.jsonl"):
        for line in open(REPO / "data" / f):
            if any(f'"page": "{p}"' in line[:200] for p in need):
                d = json.loads(line)
                if d["page"] in need:
                    pages[d["page"]] = d["elements"]
    rk, ranks, t0 = LiteRanker(), [], time.time()
    for r in rows:
        els = pages[r["page"]]
        docs = [doc(e) for e in els]
        s, _ = rank_step(rk, r["query"], docs, r["page"])
        s = demote_orphans(np.array(s, dtype=float), els)
        order = np.argsort(-s, kind="stable")
        pos = {int(j): k + 1 for k, j in enumerate(order)}
        ranks.append(min(pos[j] for j in r["equiv"] if j in pos))
    r = np.array(ranks)
    res = {"n": len(r), "hit@1": round(float((r <= 1).mean()), 4), "hit@10": round(float((r <= 10).mean()), 4),
           "mrr": round(float((1 / r).mean()), 4), "secs": round(time.time() - t0)}
    print(json.dumps(res))
    if a.record:
        BASELINE.write_text(json.dumps(res, indent=1))
        print("recorded baseline")
        return
    base = json.loads(BASELINE.read_text())
    bad = [k for k in ("hit@1", "hit@10") if res[k] < base[k] - TOLERANCE]
    verdict = "FAIL " + ",".join(bad) if bad else "OK"
    print(f"baseline {base['hit@1']}/{base['hit@10']} -> now {res['hit@1']}/{res['hit@10']}: {verdict}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
