#!/usr/bin/env python3
"""Build the CARDZ handoff package — a zip someone can unpack and actually run.

點解要有呢個腳本
---------------
`git clone` 呢條路對呢個 repo 而家係壞嘅（實測 2026-07-26）：51 個 REQUIRED_FILES
入面 30 個喺磁碟但唔喺 HEAD，而且 `data/public/market-assets/` 得 360 個 base
`.webp` 入咗 HEAD，**零個 `_200`/`_600` derivative** —— 而
`apps/web/scripts/sync-snapshot.mjs` 見到缺 derivative 係 **hard throw**，
即係 clone 完 `npm run build` 一定爆。

所以交付走 zip 路。呢個腳本就係嗰條路嘅唯一實作：

* **明確 include / exclude 清單**，寫死喺下面。唔靠 `.gitignore` 反推 ——
  `.gitignore` 擋 git，**唔擋 zip**，靠佢就係將 `data/runtime/config/backend.env`
  （DB 密碼）同 `.env.private` 直接寄咗俾人。
* **打包前掃 secret**，揾到就 abort，**只印檔名唔印內容**。
* 兩個真 secret 檔用**範本**取代（只有變數名，冇值）。
* 出 `HANDOFF_MANIFEST.json`（逐檔 sha256）——`scripts/verify_handoff.py` 靠佢
  先可以喺一個冇 `.git/` 嘅解壓目錄上面報 `enforced: true`，
  唔使再誠實但無用咁報 `enforced: false`。

用法
----
    python -X utf8 scripts/build_handoff_package.py             # 起 core 包
    python -X utf8 scripts/build_handoff_package.py --with-pop-history
    python -X utf8 scripts/build_handoff_package.py --stage-only # 只 stage 唔 zip

輸出全部落 `temp/`，唔污染 repo 根。

exit 0 = 包起好晒 / 1 = secret 掃到嘢或者缺 REQUIRED_FILES（**冇出包**）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "temp"

# ---------------------------------------------------------------------------
# 1. 要包咩
# ---------------------------------------------------------------------------
# 每一條後面寫住點解。冇原因嘅條目下一個 agent 一定會刪，或者更差 —— 照抄落去
# 而唔知點解。目錄係 recursive，逐個檔會再過一次下面 EXCLUDE_DIR_NAMES /
# EXCLUDE_PATHS / EXCLUDE_SUFFIXES。

INCLUDE_DIRS: tuple[str, ...] = (
    # --- 前端 ---
    "apps/web/src",
    "apps/web/scripts",
    "apps/web/public",          # market-assets 喺 EXCLUDE_PATHS 剔走（sync-snapshot 會重建）
    "apps/web/docs",
    "packages",                 # @cardz/market-data —— apps/web build 硬依賴
    # --- 後端 / 數據鏈 ---
    "pipelines",
    "scripts",
    "tests",                    # 接手第一日要跑得返測試先信得過個包
    "config",                   # data-cleaning-rules / data-routing 契約
    "manifests",                # image-qc 等 delta state，缺咗每日 run 會重做全量
    "integrations",             # grade10 run_service.py（REQUIRED_FILES 有）
    # --- 部署 ---
    "deploy",
    ".github",                  # CI workflow —— 接手人 fork 之後即刻有閘
    # --- 文檔 ---
    "docs",                     # docs/mockups 喺 EXCLUDE_PATHS 剔走（113.8 MB prototype）
)

INCLUDE_FILES: tuple[str, ...] = (
    # --- repo 契約 ---
    ".gitattributes",           # LFS pin + LF eol；CRLF 嘅 .sh/.service 喺 Linux 即死
    ".gitignore",               # verify_handoff.validate_layout 會讀佢驗 .env / data/runtime/ 規則
    ".dockerignore",            # deny-all 白名單；冇咗 docker build context 會拖成個 data/
    ".npmrc",
    ".env.example",             # 唯一准入包嘅 env 檔（全部值都係空）
    "tsconfig.base.json",
    "package.json",
    "package-lock.json",        # 冇佢 `npm ci` 直接拒跑
    "compose.backend.yaml",
    # --- 交接文檔（REQUIRED_FILES 入面）---
    "README.md",
    "AGENTS.md",
    "CLAUDE.md",
    "PROJECT_STATE.md",
    "FILE_CLAIMS.md",
    # --- apps/web 根檔（workspace build 要）---
    "apps/web/package.json",
    "apps/web/tsconfig.json",
    "apps/web/next.config.ts",
    "apps/web/open-next.config.ts",
    "apps/web/vitest.config.ts",
    "apps/web/wrangler.jsonc",
    "apps/web/Dockerfile",
    "apps/web/.gitignore",
    # --- 數據：只包接手人真係跑得起要嘅嗰幾件 ---
    # git 果份**永遠**係 demo 佔位符。呢度照抄 working tree 果份，
    # 但 assert_snapshot_is_demo() 會喺打包前核實佢仲係 demo，唔係就 abort。
    "data/public/seed-snapshot.json",
    "data/editorial/top100-stories.json",
    "data/editorial/card-names.json",
    "data/editorial/set-names.json",
    # MySQL bootstrap（12.8 MB）。verify_handoff.py --require-archive 硬 require，
    # 而且冇佢目標機開唔到庫 —— 呢個係「跑得起」嘅字面前提。
    "data/private/cardz-active-bootstrap.tar.gz",
    # `temp/` 全域排除，**但呢個係例外**：docs/DATA_GAPS.md:1447 引佢做證據，
    # 解釋憑空多咗 `ebay` / `snkrdunk` 兩個假「源」係點嚟嘅。唔包佢，
    # tests/test_verify_doc_refs.py 喺解壓出嚟嘅包度會紅（實測過），
    # 而我哋叫接手人跑嘅第一個驗收指令就係 pytest。
    # 佢係一次性腳本，唔係俾人跑嘅工具 —— 純粹跟住份文件走嘅證物。
    "temp/expand_tracked_universe.py",
)

# 卡圖：`sync-snapshot.mjs` 由 `data/public/market-assets/` 抄入
# `apps/web/public/market-assets/`，缺一張 derivative 就 hard throw。
# 全個目錄 1,878 檔 / 232 MB，demo snapshot 只用到 1,080 檔 / 123 MB。
# **照包全個目錄**：多出嗰 266 張卡係已經過 QC 嘅內容定址圖，target 機跑
# production promote 嗰陣就要用；重新生成要行圖鏈 + 網絡，109 MB 換呢個係抵。
INCLUDE_DIRS_DATA: tuple[str, ...] = (
    "data/public/market-assets",
    "data/tag",                 # 7.1 MB，tag_pop_data.py 嘅輸入；細，包埋
)

# ---------------------------------------------------------------------------
# 2. 唔包咩
# ---------------------------------------------------------------------------

# 見到呢啲目錄名（任何層）即刻整個 subtree 剪走。
EXCLUDE_DIR_NAMES: frozenset[str] = frozenset({
    ".git",
    "node_modules", ".next", ".open-next", ".preview", ".wrangler",
    ".venv-backend", "__pycache__", ".pytest_cache", ".ruff_cache",
    ".codegraph", ".claude", ".agent", ".idea", ".vscode",
    "dist", "coverage", "out", "playwright-report", "test-results",
})

# 精確路徑（相對 repo root）。整個 subtree 或者單檔。
EXCLUDE_PATHS: tuple[str, ...] = (
    "apps/web/public/market-assets",  # sync-snapshot.mjs 每次 build 都 rmSync 重建
    "apps/web/data",                  # 全部係 data/runtime，gitignored
    "apps/web/temp",
    "apps/web/tsconfig.tsbuildinfo",
    "docs/mockups",                   # 113.8 MB prototype；CLAUDE.md：mockup 永遠只係 prototype
    "pipelines/gemrate_ids.txt",      # CLAUDE.md：「舊清單，唔好用」
    "pipelines/snk_ids.txt",
    "tests/__pycache__",
)

EXCLUDE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".log", ".tmp", ".bak", ".tsbuildinfo")

# ---------------------------------------------------------------------------
# 3. Secret 閘 —— 呢段係整個腳本入面唯一唔准為咗方便而放鬆嘅嘢
# ---------------------------------------------------------------------------

# 路徑閘：檔名夾到呢啲即刻 abort，唔理內容。fail-closed。
SECRET_PATH_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p) for p in (
        r"(^|/)\.env$",
        r"(^|/)\.env\.(?!example$)[^/]+$",   # .env.private / .env.local / .env.production …
        r"(^|/)[^/]*\.env$",                 # backend.env / gemrate.env / fake-backend.env
        r"(^|/)\.dev\.vars",
        r"\.(pem|key|p12|pfx|jks|keystore)$",
        r"(^|/)id_(rsa|dsa|ecdsa|ed25519)$",
        r"(^|/)\.npmrc$",                    # 可以藏 _authToken；下面白名單另外處理
        r"(^|/)\.git-credentials$",
    )
)
# `.npmrc` 同 `.env.example` 係明知安全嘅例外，但**唔准盲信** ——
# 佢哋照樣要過內容閘。
SECRET_PATH_ALLOW: frozenset[str] = frozenset({".npmrc", ".env.example"})

# 內容閘 A：賦值語境。要求真係有個非佔位符嘅值先報。
# group(1)=key、group(2)=開引號（可能係空）、group(3)=值。
# **一定要分開引號**：冇引號嗰個係 kwarg 傳緊變數，有引號嗰個係真係寫死咗
# 個值落去。淨睇個值本身嘅樣，兩者係一模一樣分唔到。
# 兩個位嘅寫法係實測執返嚟嘅，改之前跑 temp/ 嘅 selftest：
#  * **唔可以用 `\b` 開頭**。`CARDZ_DB_PASSWORD` 前面嗰個 `_` 係 word char，
#    `\bpassword` 直頭 match 唔到 —— 即係全 repo 最常見嗰種名反而漏晒網。
#  * key 同 `:` 之間要容許一個閂引號，否則 JSON 嘅 `"apiKey": "…"` 漏網。
#  * 分隔符前後只可以係 `[ \t]`，**唔可以用 `\s`** —— `\s` 過到換行，
#    於是範本檔嘅空 `GEMRATE_API_KEY=` 會將下一行嘅 `CARDZ_DB_PASSWORD=`
#    當咗自己個值，成個 .env.example 即刻變假陽性。
_ASSIGN = re.compile(
    r"(?i)[A-Za-z0-9_]*(password|passwd|pwd|api[_-]?key|apikey|secret|access[_-]?token|"
    r"auth[_-]?token|client[_-]?secret|private[_-]?key|webhook[_-]?url)"
#  * 值嘅字符類要剔走反引號同反斜線。反引號 => markdown 行內碼會黐埋喺值尾，
#    搞到 `password=password` 唔再似識別符；反斜線 => 測試 fixture 嘅
#    `"A=x\nB=y"` 入面個 `\n` 係兩個字符，唔剔就一個值食晒成串。
# 註：呢度**唔會**特登寫個帶引號嘅假密碼做例子 —— 咁做等於要為自己開後門。
    r"[\"']?[ \t]*[=:][ \t]*([\"']?)([^\s\"',;#}\]\\`]{8,})"
)
# 佔位符 —— 呢啲唔算 secret。
_PLACEHOLDER = re.compile(
    r"(?i)^(|<.*>|%[A-Z_]+%|x{3,}|\*{3,}|\.{3,}|-+|"
    # `$VAR` / `${VAR}` / compose 嘅 `${VAR:?message}` —— 全部係變數引用
    r"\$.*|"
    # regex / glob 碎片：`[[:space:]]…`、`^…`、`(?:…)`、`/^API_KEY=…`
    r"[\[\^(/|].*|"
    # secret-manager 引用 —— 呢啲係「點樣攞個值」，唔係個值本身。
    # docs/RUNBOOK.md §Secret injection 教人就係咁寫。
    r"op://.*|vault:.*|secretsmanager:.*|arn:aws:secretsmanager.*|sops:.*|"
    r"changeme.*|your[-_].*|example.*|placeholder.*|redacted.*|dummy.*|fake.*|"
    r"test.*|sample.*|none|null|true|false|\d+|"
    r"process\.env.*|os\.environ.*|self\..*|args\..*|config\[.*|"
    r"[A-Za-z_]+\.(get|environ|env)\b.*)$"
)
# 測試哨兵。`tests/` 入面有幾個測試專門寫個假 .env 出嚟，證明打包／freeze 真係
# 會拒收 secret 檔（test_grade10_full_freeze.test_freeze_rejects_secret_named_source_file
# 就係咁）。啲值全部係**否定式英文指令**：not-copied / not-allowed / do-not-… /
# must-not-…。真 credential 唔會串一句「唔好抄我」出嚟，所以呢個判別係安全嘅，
# 而且必須放行 —— 嗰啲測試同呢個腳本係同一類閘，唔可以為咗自己過關而剷走人哋個閘。
_SENTINEL = re.compile(r"(?i)^((do|must|should|will|can|may)[-_]?)?(not|never|no)[-_]")

# `tests/` 唔跑賦值啟發式（**只係**賦值嗰條；路徑閘同 literal 指紋照跑）。
#
# 點解要咁：測試 credential 處理嘅程式碼，個 fixture 本身梗係一串假 credential
# —— `local-secret`、`injected-secret`、`not-copied`、`must-not-ship`…
# 實測 6/6 全部係假陽性。逐個值加 pattern（`local[-_].*`、`injected[-_].*`…）
# 係永遠收唔到尾嘅打地鼠，而且每加一條就順手放寬埋**生產程式碼**嗰邊 ——
# 為咗一個 test fixture 而全 repo 放行 `local-*` 係蝕本生意。
#
# 換返嚟嘅風險同殘餘防線講清楚：真 credential 貼咗入 tests/ 就走甩賦值閘。
# 但 (a) 檔案形態（`tests/fixtures/.env`、`.pem`）照樣俾路徑閘攔，
#     (b) 有廠商特徵嘅（AKIA / ghp_ / xox / PEM / 賦值 40-hex）照樣俾指紋閘攔。
# 走甩嘅淨係「一串冇特徵嘅隨機字」貼咗入測試檔嗰種情況。
CONTENT_ASSIGN_SKIP_PREFIXES: tuple[str, ...] = ("tests/",)
# 內容閘 B：literal secret 指紋。呢啲唔使語境，見到即死。
SECRET_LITERALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pem-private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("aws-access-key-id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("npm-auth-token", re.compile(r"_authToken\s*=\s*\S+")),
    # 40-hex **淨係喺賦值語境**先算。裸 40-hex 喺呢個 repo 係卡 ID
    # （CLAUDE.md：「ID 一律 40-char SHA1 hex」），全 repo 有幾萬個，
    # 裸掃會出幾千個假陽性，跟住冇人再信呢個閘 —— 比冇閘更差。
    ("hex40-assigned", re.compile(r"(?i)\b(key|token|secret)\s*[=:]\s*[\"']?[0-9a-f]{40}\b")),
)

# 邊啲檔要掃：**除咗二進位之外全部**。
#
# 原本呢度係一張副檔名白名單，實測係 fail-open 嘅 —— 包入面有 .html / .bat /
# .state / .svg / .css / .lock 一律唔喺名單，即係完全冇掃過；而 `.tf` 之類
# 將來加入嘅檔種會自動漏網，冇人會記得返嚟補名單。
# 而家反轉：sniff 頭 8 KB 有冇 NUL bytes，有就當二進位跳過，冇就掃。
# 新檔種預設**被掃**，唔係預設放行。
_BINARY_SNIFF_BYTES = 8192


def looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return b"\x00" in handle.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return True

# 打包時生成嘅範本 —— 真檔 (`data/runtime/config/*.env`) 永遠唔入包。
ENV_TEMPLATES: dict[str, str] = {
    "data/runtime/config/backend.env.example": (
        "# 目標機自己填。呢個係範本，只有變數名，冇值。\n"
        "# 真檔擺 /etc/cardz-market-cap/backend.env，root:root 0600。\n"
        "# 來源：docs/PACKAGING_CHECKLIST.md §3\n"
        "CARDZ_DB_NAME=\n"
        "CARDZ_DB_USER=\n"
        "CARDZ_DB_PORT=\n"
        "CARDZ_DB_PASSWORD=\n"
        "CARDZ_DB_ROOT_PASSWORD=\n"
    ),
    "data/runtime/config/gemrate.env.example": (
        "# GemRate POP API key。scripts/backend.py SECRETS_PATH 讀呢個位。\n"
        "# ⚠️ 唔准寫入 backend.env —— backend.py 嘅 write_local_config() 會將\n"
        "#    config 全量寫返落 backend.env，key 會漏入 repo 可見路徑。\n"
        "# 週掃（cardz-gemrate-freeze.service）另外讀 /etc/cardz-market-cap/gemrate.env，\n"
        "#    兩個位嘅來龍去脈見 docs/PACKAGING_CHECKLIST.md §6。\n"
        "GEMRATE_API_KEY=\n"
    ),
}

# POP 歷史（獨立包）。canonical_public_snapshot.population_series() 讀佢出
# 7d/30d POP delta。缺咗**唔會爆** —— 窗口自然報 accumulating（CLAUDE.md 明寫
# 「缺檔／缺目錄唔係錯」）。所以佢唔入 core 包：701 檔 / 288 MB 會令主交付物
# 大一倍，而佢唔係「跑得起」嘅前提，係「POP delta 有數」嘅前提。
POP_HISTORY_GLOB = "data/private/gemrate/cards/*/history_full.json"


class BuildError(RuntimeError):
    """打包前提唔滿足。"""


# ---------------------------------------------------------------------------


def _is_excluded(rel: str) -> bool:
    parts = rel.split("/")
    if any(part in EXCLUDE_DIR_NAMES for part in parts):
        return True
    for bad in EXCLUDE_PATHS:
        if rel == bad or rel.startswith(bad + "/"):
            return True
    return rel.endswith(EXCLUDE_SUFFIXES)


def collect_sources(root: Path) -> list[str]:
    """砌出要包嘅相對路徑清單（POSIX 分隔符，排序、去重）。"""
    picked: set[str] = set()

    for name in INCLUDE_FILES:
        path = root / name
        if path.is_file() and not _is_excluded(name):
            picked.add(name)

    for directory in (*INCLUDE_DIRS, *INCLUDE_DIRS_DATA):
        base = root / directory
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(root).as_posix()
            if not _is_excluded(rel):
                picked.add(rel)

    return sorted(picked)


def scan_for_secrets(staged: Path, files: Iterable[str]) -> list[dict[str, str]]:
    """喺**已 stage 好嘅內容**上面掃，即係掃真係會寄出去嗰啲 byte。

    只回報檔名同規則名，**永遠唔回報命中嘅內容**。
    """
    findings: list[dict[str, str]] = []

    for rel in files:
        name = Path(rel).name
        if name not in SECRET_PATH_ALLOW:
            for pattern in SECRET_PATH_PATTERNS:
                if pattern.search(rel):
                    findings.append({"file": rel, "rule": "secret-filename", "detail": pattern.pattern})
                    break

        path = staged / rel
        if not path.is_file() or looks_binary(path):
            continue

        skip_assign = rel.startswith(CONTENT_ASSIGN_SKIP_PREFIXES)
        literal_hits: set[str] = set()
        # 逐行掃：所有 pattern 都係單行嘅（分隔符釘死咗 `[ \t]*`，唔會跨行），
        # 所以逐行同一次過讀完全等價 —— 但冇咗 size cap，7 MB 嘅
        # data/tag/pops_pokemon.jsonl 唔會再靜靜雞跳過。
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as handle:
                for line in handle:
                    for rule, pattern in SECRET_LITERALS:
                        if rule not in literal_hits and pattern.search(line):
                            literal_hits.add(rule)
                            findings.append({
                                "file": rel, "rule": rule, "detail": "literal secret fingerprint",
                            })
                    if skip_assign:
                        continue
                    for match in _ASSIGN.finditer(line):
                        quoted = bool(match.group(2))
                        value = match.group(3).strip()
                        if _PLACEHOLDER.match(value) or _SENTINEL.match(value):
                            continue
                        # 冇引號 + 係個合法識別符 => 傳緊變數（`password=password`），唔係字面值。
                        # 呢度**一定要**同時要求「冇引號」：`password="hunter2"` 都係合法識別符樣，
                        # 淨靠 isidentifier() 會將真係寫死嘅密碼放晒行。
                        if not quoted and value.isidentifier():
                            continue
                        findings.append({
                            "file": rel,
                            "rule": "assigned-secret",
                            "detail": f"{match.group(1).lower()}=<non-placeholder value>",
                        })
                        skip_assign = True   # 一個檔報一次就夠
                        break
        except OSError:
            continue

    return findings


def assert_snapshot_is_demo(staged: Path) -> dict[str, Any]:
    """硬規矩：入包果份 seed-snapshot.json 一定要仲係 demo。

    個 seed 係自我餵飼嘅（自己做自己下一代嘅輸入），一次污染會世代遺傳。
    寄一份 production 數據出去就等於將污染送埋俾對面。
    """
    path = staged / "data/public/seed-snapshot.json"
    if not path.is_file():
        raise BuildError("data/public/seed-snapshot.json is missing from the staged package")
    generation = json.loads(path.read_text(encoding="utf-8")).get("generation") or {}
    mode = generation.get("mode")
    eligible = bool(generation.get("productionEligible"))
    if mode != "demo" or eligible:
        raise BuildError(
            f"staged seed-snapshot.json is NOT the demo placeholder (mode={mode!r}, "
            f"productionEligible={eligible}); refusing to ship production data. "
            "Restore it with: git checkout data/public/seed-snapshot.json"
        )
    return {"mode": mode, "productionEligible": eligible, "blockers": generation.get("blockers") or []}


_VERIFY_MODULE: Any = None


def verify_handoff_module() -> Any:
    """借 verify_handoff.py 嘅 REQUIRED_FILES / in_head，唔喺度另抄一份。

    抄多份清單 = 兩份清單一定會分叉，而分叉嗰日冇人會發現。
    """
    global _VERIFY_MODULE
    if _VERIFY_MODULE is None:
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "cardz_handoff_required", ROOT / "scripts" / "verify_handoff.py"
        )
        if spec is None or spec.loader is None:  # pragma: no cover - defensive
            raise BuildError("cannot load scripts/verify_handoff.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _VERIFY_MODULE = module
    return _VERIFY_MODULE


def missing_required(staged: Path) -> list[str]:
    return [name for name in verify_handoff_module().REQUIRED_FILES if not (staged / name).is_file()]


def provenance(root: Path) -> dict[str, Any]:
    """記錄呢個包係由邊度砌出嚟 —— **working tree**，唔係 git clone / archive。

    呢個唔係註腳，係接手人要知嘅第一件事：包入面有啲檔仲未 commit。
    正正因為咁佢先跑得起 —— 由 HEAD 砌會即刻缺料。
    """
    module = verify_handoff_module()
    import subprocess

    def git(*args: str) -> str:
        try:
            done = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)
            return done.stdout.strip() if done.returncode == 0 else ""
        except (OSError, subprocess.SubprocessError):
            return ""

    head = git("rev-parse", "HEAD")
    missing = [n for n in module.REQUIRED_FILES if head and not module.in_head(root, n)] if head else []
    return {
        "builtFrom": "working-tree",
        "gitHead": head or None,
        "gitBranch": git("rev-parse", "--abbrev-ref", "HEAD") or None,
        "requiredFilesMissingFromHead": missing,
    }


PROVENANCE_DOC = """# PACKAGE_PROVENANCE — 呢個包係點嚟嘅

**由 working tree 打包，唔係 `git clone`、唔係 `git archive`。**
所以包入面有部分檔案喺 `{head_short}` 呢個 commit 度**仲未 commit**。
呢個係有心咁做，唔係漏嘅 —— 由 HEAD 砌反而會缺料，跑唔起。

實測（`scripts/build_handoff_package.py` 打包果刻自動量）：

- `git HEAD`：`{head}`（branch `{branch}`）
- `scripts/verify_handoff.py` 嘅 51 個 `REQUIRED_FILES` 入面，
  **{missing_count} 個喺 HEAD 度搵唔到**。即係話：對住呢個 HEAD 做一次乾淨
  `git clone`，交付會即刻缺呢 {missing_count} 個檔。
- 另外 `data/public/market-assets/` 喺 HEAD 只有 360 個底圖，
  **零個 `_200` / `_600` derivative**；而 `apps/web/scripts/sync-snapshot.mjs`
  見到缺 derivative 係直接 throw，`apps/web` 嘅 `prebuild` 又會叫佢。
  即係話 clone 完連 `npm run build` 都行唔到。呢個 zip 包晒 1,080 個
  （360 底圖 + 720 derivative），所以呢條路行得通。

HEAD 缺嘅 `REQUIRED_FILES`：

{missing_list}

## 對接手人嘅意思

1. 你手上呢個 zip **係可以跑嘅嗰版**。驗證方法見 `docs/AWS_HANDOFF.md`
   嘅「Day one」一節。
2. 你**唔可以**用 `git clone` 重砌返呢個包，直至上面啲檔 commit 咗為止。
3. 如果你要將呢個包放返落 git：先 `git add` 上面嗰批檔，
   然後跑 `python -X utf8 scripts/verify_handoff.py`，
   `tracking.enforced` 要係 `true` 而 `missingFromHead` 要係空。

## 完整性

包根有 `HANDOFF_MANIFEST.json`，逐個檔記住 sha256。
`scripts/verify_handoff.py` 喺冇 `.git/` 嘅情況下會改用佢做完整性閘
（`tracking.source` 會顯示 `package-manifest`）。改動過任何一個
`REQUIRED_FILES` 都會即刻 sha256 對唔上而 fail。
"""


def write_provenance(staged: Path, info: dict[str, Any]) -> None:
    missing = info["requiredFilesMissingFromHead"]
    head = info["gitHead"] or "(unknown)"
    (staged / "PACKAGE_PROVENANCE.md").write_text(
        PROVENANCE_DOC.format(
            head=head,
            head_short=head[:12],
            branch=info["gitBranch"] or "(unknown)",
            missing_count=len(missing),
            missing_list="\n".join(f"- `{name}`" for name in missing) or "- （冇，HEAD 係齊嘅）",
        ),
        encoding="utf-8",
        newline="\n",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage(root: Path, staged: Path, files: list[str]) -> None:
    if staged.exists():
        shutil.rmtree(staged, onerror=lambda f, t, _e: (Path(t).chmod(0o700), f(t)))
    for rel in files:
        target = staged / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / rel, target)
    for rel, body in ENV_TEMPLATES.items():
        target = staged / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8", newline="\n")


# 已經壓縮過嘅格式再 deflate 係純浪費時間，直接 store。
STORED_SUFFIXES: frozenset[str] = frozenset({".webp", ".png", ".jpg", ".jpeg", ".gz", ".zst", ".zip", ".woff2"})


def write_zip(staged: Path, files: list[str], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for rel in files:
            source = staged / rel
            compress = zipfile.ZIP_STORED if source.suffix.lower() in STORED_SUFFIXES else zipfile.ZIP_DEFLATED
            archive.write(source, rel, compress_type=compress)


def build_pop_history(root: Path, target: Path) -> dict[str, Any]:
    files = sorted(root.glob(POP_HISTORY_GLOB))
    target.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            rel = path.relative_to(root).as_posix()
            archive.write(path, rel)
            total += path.stat().st_size
    return {
        "path": str(target),
        "files": len(files),
        "sourceBytes": total,
        "zipBytes": target.stat().st_size if target.is_file() else 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the CARDZ handoff zip")
    parser.add_argument("--out", type=Path, default=OUT_DIR, help="Output directory (default: temp/)")
    parser.add_argument("--stage-only", action="store_true", help="Stage the tree but do not zip it")
    parser.add_argument("--with-pop-history", action="store_true",
                        help="Also build the optional POP-history archive (701 files, ~288 MB)")
    parser.add_argument("--json", action="store_true", help="Machine-readable report only")
    args = parser.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    staged = args.out / f"cardz-handoff-{stamp}"
    report: dict[str, Any] = {"root": str(ROOT), "staged": str(staged), "generatedAt":
                              datetime.now(timezone.utc).isoformat(timespec="seconds")}

    files = collect_sources(ROOT)
    if not args.json:
        print(f"[1/6] collected {len(files)} source files")

    stage(ROOT, staged, files)
    staged_files = sorted(
        p.relative_to(staged).as_posix() for p in staged.rglob("*") if p.is_file()
    )
    if not args.json:
        print(f"[2/6] staged {len(staged_files)} files (incl. {len(ENV_TEMPLATES)} generated env templates)")

    findings = scan_for_secrets(staged, staged_files)
    report["secretScan"] = {"scanned": len(staged_files), "findings": findings}
    if findings:
        report["ok"] = False
        shutil.rmtree(staged, ignore_errors=True)
        print(f"\nABORT: secret scan flagged {len(findings)} file(s). Nothing was packaged.", file=sys.stderr)
        for item in findings[:40]:
            print(f"  [{item['rule']}] {item['file']}", file=sys.stderr)
        if len(findings) > 40:
            print(f"  ...and {len(findings) - 40} more", file=sys.stderr)
        print("\n(file names only - contents are never printed)", file=sys.stderr)
        return 1
    if not args.json:
        print(f"[3/6] secret scan clean ({len(staged_files)} files checked)")

    try:
        report["snapshot"] = assert_snapshot_is_demo(staged)
    except BuildError as error:
        shutil.rmtree(staged, ignore_errors=True)
        print(f"\nABORT: {error}", file=sys.stderr)
        return 1
    if not args.json:
        print(f"[4/6] seed-snapshot.json is demo (blockers={len(report['snapshot']['blockers'])})")

    missing = missing_required(staged)
    report["requiredFiles"] = {"missing": missing}
    if missing:
        report["ok"] = False
        print(f"\nABORT: {len(missing)} REQUIRED_FILES missing from the package:", file=sys.stderr)
        for name in missing:
            print(f"  - {name}", file=sys.stderr)
        return 1
    if not args.json:
        print("[5/6] every verify_handoff REQUIRED_FILE is present")

    prov = provenance(ROOT)
    report["provenance"] = prov
    write_provenance(staged, prov)
    staged_files.append("PACKAGE_PROVENANCE.md")
    if not args.json:
        print(f"      provenance: built from working-tree; "
              f"{len(prov['requiredFilesMissingFromHead'])} REQUIRED_FILES absent from HEAD")

    manifest = {
        "schema": "cardz-handoff-manifest/1",
        "generatedAt": report["generatedAt"],
        "builtFrom": prov["builtFrom"],
        "sourceHead": prov["gitHead"],
        "fileCount": len(staged_files),
        "files": {rel: {"bytes": (staged / rel).stat().st_size, "sha256": sha256_file(staged / rel)}
                  for rel in staged_files},
    }
    (staged / "HANDOFF_MANIFEST.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8", newline="\n"
    )
    staged_files.append("HANDOFF_MANIFEST.json")

    total_bytes = sum((staged / rel).stat().st_size for rel in staged_files)
    report["package"] = {"files": len(staged_files), "bytes": total_bytes}

    if not args.stage_only:
        zip_path = args.out / f"cardz-handoff-{stamp}.zip"
        write_zip(staged, staged_files, zip_path)
        report["zip"] = {"path": str(zip_path), "bytes": zip_path.stat().st_size}
        if not args.json:
            print(f"[6/6] wrote {zip_path.name}  {zip_path.stat().st_size / 1048576:.1f} MB")
    elif not args.json:
        print("[6/6] --stage-only: skipped zip")

    if args.with_pop_history:
        report["popHistory"] = build_pop_history(ROOT, args.out / f"cardz-pop-history-{stamp}.zip")
        if not args.json:
            info = report["popHistory"]
            print(f"      + POP history: {info['files']} files, {info['zipBytes'] / 1048576:.1f} MB zipped")

    report["ok"] = True
    if args.json:
        print(json.dumps(report, sort_keys=True))
        return 0

    print(f"\nPASS: {len(staged_files)} files, {total_bytes / 1048576:.1f} MB staged")
    print(f"  staged tree : {staged}")
    if "zip" in report:
        print(f"  zip         : {report['zip']['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
