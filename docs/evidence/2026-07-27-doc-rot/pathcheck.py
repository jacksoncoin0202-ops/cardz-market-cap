import re, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
docs = []
docs.append(ROOT / "README.md")
p = ROOT / "apps/web/README.md"
if p.exists():
    docs.append(p)
for f in sorted((ROOT / "docs").glob("*.md")):
    docs.append(f)

# patterns: markdown links, backticked paths, bare path-ish tokens
link_re = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
tick_re = re.compile(r"`([^`\n]+)`")

PATHY = re.compile(r"^[\w./\-]+$")
EXT = re.compile(r"\.(py|ts|tsx|mjs|js|json|md|ps1|sh|sql|yaml|yml|txt|env|service|timer|jsonl|toml|cfg)$", re.I)

results = []
for d in docs:
    text = d.read_text(encoding="utf-8", errors="replace")
    cands = set()
    for m in link_re.finditer(text):
        cands.add(("link", m.group(1)))
    for m in tick_re.finditer(text):
        s = m.group(1).strip()
        if not PATHY.match(s):
            continue
        if not EXT.search(s) and "/" not in s and "\\" not in s:
            continue
        cands.add(("tick", s))
    for kind, raw in sorted(cands):
        ref = raw.split("#")[0].strip()
        if not ref or ref.startswith(("http://", "https://", "mailto:")):
            continue
        # strip :line
        ref_nolines = re.sub(r":\d+(-\d+)?$", "", ref)
        norm = ref_nolines.replace("\\", "/")
        # resolve relative to doc dir first, then root
        c1 = (d.parent / norm)
        c2 = (ROOT / norm.lstrip("./"))
        exists = c1.exists() or c2.exists()
        which = str(c1.relative_to(ROOT)) if c1.exists() else (str(c2.relative_to(ROOT)) if c2.exists() else "")
        results.append({
            "doc": str(d.relative_to(ROOT)).replace("\\","/"),
            "kind": kind,
            "ref": raw,
            "exists": exists,
            "resolved": which.replace("\\","/"),
        })

missing = [r for r in results if not r["exists"]]
print(f"checked={len(results)} missing={len(missing)}")
by_doc = {}
for r in missing:
    by_doc.setdefault(r["doc"], []).append(r["ref"])
for doc in sorted(by_doc):
    print(f"\n## {doc}")
    for ref in sorted(set(by_doc[doc])):
        print(f"  MISSING {ref}")
Path(ROOT/"temp"/"G_pathcheck.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
