#!/usr/bin/env python3
"""Prove a real `git clone` of this repo carries a runnable deployment.

點解要有呢個腳本
---------------
`scripts/verify_handoff.py` 係**靜態**檢查：佢問「呢啲檔喺唔喺 HEAD」。
呢個腳本係**動態**檢查：佢真係 `git clone` 一份出嚟，喺嗰份 clone 上面驗。
兩者唔重疊 —— clone 仲會暴露靜態檢查睇唔到嘅嘢：

* Git LFS 冇裝／冇 `lfs pull`：`.webp` 會變 ~131 bytes 嘅 pointer 文字檔，
  唔係圖。Pillow 同 `sync-snapshot.mjs` 開嗰陣先爆，而且錯誤訊息完全唔會指向 LFS。
* `deploy/linux/cardz-daily-systemd.sh` 嘅硬 require 清單：缺一個即 `exit 1`，
  一條 timer 都裝唔到。呢度直接由嗰個 shell 檔抽返個清單出嚟核，
  唔係喺呢度另寫一份會腐爛嘅副本。

呢個做法本來寫喺 `docs/SERVER_MIGRATION.md` §1.1 第 3 步（手打 `git clone` +
`test -f`），但手打嗰版只驗兩個檔。呢度驗全套。

用法
----
    python -X utf8 scripts/verify_clean_clone.py            # clone → 驗 → 刪
    python -X utf8 scripts/verify_clean_clone.py --keep     # 留低份 clone 俾人手查
    python -X utf8 scripts/verify_clean_clone.py --json     # CI 用

* `data/public/seed-snapshot.json` **永遠**係 demo 佔位符。git 果份唔准放
  production 數據，所以每個 clone 攞到嘅都係舊 demo（validator 對住佢報過千條
  error 係**正常**）。落手嘅人第一反應會以為 producer 壞咗，實際上係未跑過。
  呢個 check 就係要嘈住講「未生成 production snapshot，唔可以出街」。

exit 0 = clone 攞齊嘢兼可以出街；
exit 1 = clone 缺件（**打包 bug**，要有人 commit 返啲檔）；
exit 2 = 驗唔到（git 唔喺、repo 冇 HEAD），**唔等於冇事**；
exit 3 = 檔齊，但 snapshot 仲係 demo（**預期中嘅 clone 狀態**，喺目標機跑 producer 就得）。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/v1"
# LFS pointer 大約 130 bytes。真 .webp 最細都遠大過呢個數。
LFS_POINTER_MAX_BYTES = 1024


def _load_required_files() -> tuple[str, ...]:
    """由 verify_handoff.py 借個清單，唔喺度另抄一份。

    抄一份出嚟就一定會有一日兩邊唔同步，跟住兩個 gate 講兩個故事。
    """
    spec = importlib.util.spec_from_file_location(
        "cardz_handoff_required", ROOT / "scripts" / "verify_handoff.py"
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise RuntimeError("cannot load scripts/verify_handoff.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return tuple(module.REQUIRED_FILES)


def installer_hard_requirements(root: Path) -> list[str]:
    """抽 deploy/linux/cardz-daily-systemd.sh 嗰個 `for required in ... $repo_root/X` 清單。

    佢係 installer 嘅 fail-closed 閘（缺一即 exit 1）。用 grep 抽而唔係手抄，
    installer 加多一個 require 呢度自動跟到。

    ⚠️ 範圍有限：只抽字面寫住 `$repo_root/...` 嗰啲。systemd unit 嗰批係經
    `$service_template` 之類嘅變數引用，抽唔到 —— 佢哋靠 verify_handoff.py 嘅
    REQUIRED_FILES 兜底。所以呢個數細過 installer 實際 require 嘅總數，
    唔好當佢係完整清單。
    """
    installer = root / "deploy" / "linux" / "cardz-daily-systemd.sh"
    if not installer.is_file():
        return []
    text = installer.read_text(encoding="utf-8", errors="replace")
    return sorted(set(re.findall(r'\$repo_root/([A-Za-z0-9_./-]+)', text)))


def rmtree(path: Path) -> None:
    """刪走個 clone，連 .git 入面啲唯讀檔一齊。

    ⚠️ 唔好改返 `shutil.rmtree(path, ignore_errors=True)`。Git 喺 Windows 將
    `.git/objects/**` 設成唯讀，`ignore_errors=True` 會**靜靜地**刪唔到就算數，
    留低一個得個 `.git` 嘅目錄。下一次跑 `git clone` 落一個非空目錄即 fail，
    而錯誤訊息完全唔會指向「上次冇清乾淨」。2026-07-26 實測踩過。
    """
    def force(func, target, _exc):
        Path(target).chmod(0o700)
        func(target)

    if path.exists():
        shutil.rmtree(path, onerror=force)


def run_clone(source: Path, dest: Path) -> subprocess.CompletedProcess[str]:
    rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["git", "clone", "--depth", "1", source.as_uri(), str(dest)],
        check=False,
        capture_output=True,
        text=True,
    )


def lfs_pointer_survey(clone: Path, pattern: str = "data/public/market-assets/*.webp") -> dict[str, Any]:
    """報告有幾多個 LFS 資產仲係 pointer 未 smudge。

    ⚠️ 呢個 check 唔可以喺原本嘅 working tree 做 —— working tree 嘅檔係
    `cp` / 本機生成，同 LFS 有冇裝完全無關。一定要喺 clone 上面驗。
    """
    files = sorted(clone.glob(pattern))
    pointers = []
    for path in files:
        if path.stat().st_size > LFS_POINTER_MAX_BYTES:
            continue
        if path.read_bytes()[:64].replace(b"\r\n", b"\n").startswith(LFS_POINTER_PREFIX):
            pointers.append(path.relative_to(clone).as_posix())
    return {
        "pattern": pattern,
        "total": len(files),
        "unresolvedPointers": len(pointers),
        "sample": pointers[:5],
    }


def snapshot_readiness(clone: Path) -> dict[str, Any]:
    """`data/public/seed-snapshot.json` 出得街未？

    git 果份**設計上**永遠係 demo（CLAUDE.md 硬規矩：唔准 commit production 數據
    落去），所以呢個 check 對住任何 clone 都一定係 `deployable: false`。
    咁做係故意嘅 —— 佢唔係喺度捉 bug，係喺度提住個部署員仲欠一步。

    落手嘅人見到 validator 對住呢份 snapshot 報成千條 error，第一反應係
    「producer 壞咗」。實際上係 producer 根本未喺呢部機跑過。
    """
    path = clone / "data" / "public" / "seed-snapshot.json"
    if not path.is_file():
        return {"present": False, "deployable": False, "reason": "seed-snapshot.json missing from clone"}

    try:
        generation = json.loads(path.read_text(encoding="utf-8")).get("generation") or {}
    except (json.JSONDecodeError, OSError) as error:
        return {"present": True, "deployable": False, "reason": f"unreadable: {error}"}

    blockers = list(generation.get("blockers") or [])
    eligible = bool(generation.get("productionEligible"))
    return {
        "present": True,
        "deployable": eligible and not blockers,
        "mode": generation.get("mode"),
        "id": generation.get("id"),
        "effectiveAt": generation.get("effectiveAt"),
        "productionEligible": eligible,
        "blockers": blockers,
        "expected": "the committed snapshot is a demo placeholder by design; git must never carry production data",
        "action": (
            "on the target host run: python -X utf8 pipelines/run_daily.py --mode production "
            "(it generates a candidate, gates it, then promotes over data/public/seed-snapshot.json locally). "
            "NEVER commit the promoted file back."
        ),
    }


def verify(source: Path, dest: Path, *, keep: bool) -> tuple[dict[str, Any], int]:
    source = source.resolve()
    report: dict[str, Any] = {"source": str(source), "clone": str(dest)}

    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "--verify", "--quiet", "HEAD"],
        check=False, capture_output=True, text=True,
    )
    if head.returncode != 0:
        report["error"] = "source has no HEAD commit; nothing to clone"
        return report, 2

    report["sourceHead"] = head.stdout.strip()
    cloned = run_clone(source, dest)
    if cloned.returncode != 0:
        report["error"] = f"git clone failed: {cloned.stderr.strip()[:500]}"
        return report, 2

    try:
        required = _load_required_files()
        missing = [name for name in required if not (dest / name).exists()]
        report["requiredFiles"] = {
            "checked": len(required),
            "missing": missing,
        }

        installer_required = installer_hard_requirements(source)
        installer_missing = [name for name in installer_required if not (dest / name).exists()]
        report["installerHardRequires"] = {
            "source": "deploy/linux/cardz-daily-systemd.sh",
            "checked": len(installer_required),
            "missing": installer_missing,
            "consequence": "installer exits 1 before touching systemd; no timer gets installed",
        }

        report["gitLfs"] = lfs_pointer_survey(dest)
        report["snapshotReadiness"] = snapshot_readiness(dest)

        # 打包 bug（缺檔 / LFS 未 pull）比「未跑 producer」嚴重，所以佢贏個 exit code。
        packaging_broken = (
            bool(missing) or bool(installer_missing) or report["gitLfs"]["unresolvedPointers"] > 0
        )
        snapshot_blocked = not report["snapshotReadiness"]["deployable"]
        report["ok"] = not packaging_broken and not snapshot_blocked
        if packaging_broken:
            return report, 1
        return report, (3 if snapshot_blocked else 0)
    finally:
        if not keep:
            rmtree(dest)


def main() -> int:
    parser = argparse.ArgumentParser(description="Clone this repo into a scratch dir and verify the clone is deployable")
    parser.add_argument("--source", type=Path, default=ROOT, help="Repository to clone (default: this repo)")
    parser.add_argument(
        "--dest",
        type=Path,
        default=ROOT / "temp" / "clean-clone-check",
        help="Where to put the throwaway clone (default: temp/clean-clone-check)",
    )
    parser.add_argument("--keep", action="store_true", help="Keep the clone for manual inspection")
    parser.add_argument("--json", action="store_true", help="Machine-readable output only")
    args = parser.parse_args()

    report, code = verify(args.source, args.dest, keep=args.keep)

    if args.json:
        print(json.dumps(report, sort_keys=True))
        return code

    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    if code == 0:
        print("\nPASS: a clean clone carries every required file and is deployable")
    elif code == 2:
        print(f"\nUNVERIFIED (exit 2): {report.get('error')}", file=sys.stderr)
        print("exit 2 does NOT mean the clone is fine - the check never ran.", file=sys.stderr)
    elif code == 3:
        snap = report["snapshotReadiness"]
        print("\nNOT DEPLOYABLE YET (exit 3): every file is present, but the snapshot is still the demo placeholder", file=sys.stderr)
        print(f"  mode={snap.get('mode')} id={snap.get('id')} effectiveAt={snap.get('effectiveAt')}", file=sys.stderr)
        print(f"  productionEligible={snap.get('productionEligible')} blockers={snap.get('blockers')}", file=sys.stderr)
        print(f"  THIS IS EXPECTED: {snap.get('expected')}", file=sys.stderr)
        print(f"  DO THIS: {snap.get('action')}", file=sys.stderr)
    else:
        print("\nFAIL: a clean clone is missing deployment prerequisites", file=sys.stderr)
        for key in ("requiredFiles", "installerHardRequires"):
            for name in report.get(key, {}).get("missing", []):
                print(f"  [{key}] {name}", file=sys.stderr)
        lfs = report.get("gitLfs", {})
        if lfs.get("unresolvedPointers"):
            print(
                f"  [gitLfs] {lfs['unresolvedPointers']}/{lfs['total']} assets are unresolved LFS pointers "
                f"- run `git lfs install && git lfs pull`",
                file=sys.stderr,
            )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
