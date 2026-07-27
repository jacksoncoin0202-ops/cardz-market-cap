"""審計輔助：抽出 in-scope 文檔入面每條命令，驗證被調用嘅 script / npm target 存唔存在。

只讀。輸出 temp/G_cmdcheck.json。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

DOCS: list[Path] = [ROOT / "README.md"]
DOCS += sorted(p for p in (ROOT / "docs").glob("*.md"))

# npm 可用 target（root + apps/web + packages/market-data）
npm_targets: set[str] = set()
for pkg in ("package.json", "apps/web/package.json", "packages/market-data/package.json"):
    p = ROOT / pkg
    if p.exists():
        npm_targets |= set(json.loads(p.read_text(encoding="utf-8")).get("scripts", {}))

# python xxx.py / python -m x / node xxx.mjs / npm run xxx
RE_PY = re.compile(r"python(?:3(?:\.\d+)?)?(?:\.exe)?\s+(?:-X\s+utf8\s+)?(?:-m\s+)?([A-Za-z0-9_./\\-]+\.py)")
RE_NODE = re.compile(r"node\s+([A-Za-z0-9_./\\-]+\.(?:mjs|js|cjs))")
RE_NPM = re.compile(r"npm\s+run\s+([A-Za-z0-9:_-]+)")
RE_PS1 = re.compile(r"([A-Za-z0-9_./\\-]+\.ps1)")

rows: list[dict] = []
for doc in DOCS:
    text = doc.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        seen: list[tuple[str, str]] = []
        seen += [("python", m) for m in RE_PY.findall(line)]
        seen += [("node", m) for m in RE_NODE.findall(line)]
        seen += [("ps1", m) for m in RE_PS1.findall(line)]
        for kind, target in seen:
            rel = target.replace("\\", "/").lstrip("./")
            exists = (ROOT / rel).exists()
            if not exists:
                # 有啲文檔用 repo 相對以外嘅寫法，試埋 docs/ 同裸檔名
                exists = any((ROOT / c / Path(rel).name).exists() for c in ("scripts", "pipelines", "deploy/windows", "tools"))
            if not exists:
                rows.append({"doc": doc.relative_to(ROOT).as_posix(), "line": lineno,
                             "kind": kind, "target": target, "status": "MISSING"})
        for tgt in RE_NPM.findall(line):
            if tgt not in npm_targets:
                rows.append({"doc": doc.relative_to(ROOT).as_posix(), "line": lineno,
                             "kind": "npm", "target": f"npm run {tgt}", "status": "NO_SUCH_SCRIPT"})

out = {"docs_scanned": len(DOCS), "npm_targets_known": sorted(npm_targets), "problems": rows}
(ROOT / "temp" / "G_cmdcheck.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"docs={len(DOCS)} problems={len(rows)}")
for r in rows:
    print(f"  {r['status']:15} {r['doc']}:{r['line']}  {r['target']}")
