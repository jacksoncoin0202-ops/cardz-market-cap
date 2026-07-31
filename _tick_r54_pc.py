import json
from pathlib import Path
from collections import Counter
pc_ids = {"5809563","4637112","4637096","7800269","11069056","5809440","5809387"}
root = Path("data/runtime/private-reports/fill/PC-FULL-900")
hits = []
for p in root.glob("results_shard_*.jsonl"):
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        # various id fields
        s = json.dumps(o)
        for pc in pc_ids:
            if pc in s:
                hits.append((pc, p.name, {k:o.get(k) for k in list(o)[:12]}))
print("hits", len(hits))
for h in hits[:20]:
    print(h[0], h[1], h[2])
# progress totals
for p in sorted(root.glob("progress_shard_*.json")):
    print(p.name, p.read_text(encoding="utf-8")[:200])
# sample one result line
for p in sorted(root.glob("results_shard_*.jsonl")):
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if lines:
        o = json.loads(lines[0])
        print("sample keys", sorted(o.keys())[:40])
        print("sample", {k:o.get(k) for k in list(o)[:15]})
        break
