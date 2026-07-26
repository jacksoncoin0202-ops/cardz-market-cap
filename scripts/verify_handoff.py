#!/usr/bin/env python3
"""Fail-closed portability checks for a CARDZ backend handoff.

This is deliberately a repository check, not a deployment command.  It proves
that a clone has the tracked release archive materialized by Git LFS and that
the platform launchers/units are present before an operator bootstraps MySQL.
It never reads an environment file, connects to a database, or starts a job.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_RELATIVE = Path("data/private/cardz-active-bootstrap.tar.gz")

# 呢個清單係「clean clone / zip 解開之後跑唔跑得起」嘅定義，唔係「repo 有咩檔」。
# 每一組後面寫住點解缺咗會死 —— 冇原因嘅條目下一個 agent 一定會刪。
REQUIRED_FILES = (
    # --- repo 契約 ---
    ".gitattributes",  # LFS pin + LF eol；CRLF 嘅 .sh/.service 喺 Linux 即死
    ".gitignore",      # .env / data/runtime/ 唔准入版本控制
    # --- 跨平台 backend bootstrap ---
    "scripts/backend.py",
    "scripts/backend.sh",
    "scripts/backend.ps1",
    "scripts/bootstrap_archive.py",
    # --- 每日鏈本體 ---
    # deploy/linux/cardz-daily-systemd.sh:63-68 硬 require 呢三個，缺一即 exit 1，
    # 一條 timer 都裝唔到。
    "pipelines/run_daily.py",
    "scripts/verify_daily_run.py",   # 唯一 outcome gate（分辨「行完」同「真係有新數據」）
    "scripts/notify_alert.py",       # OnFailure 通知層
    # publish 鏈喺 run_daily 入面直接 import／spawn，缺咗係 ImportError 唔係 warning
    "pipelines/canonical_public_snapshot.py",
    "pipelines/ensure_std_card_images.py",
    "pipelines/native_image_resolver.py",
    "pipelines/verify_images.py",
    # --- systemd units（installer 會逐個搵，缺咗就靜靜地唔裝嗰條 timer）---
    "deploy/systemd/cardz-market-cap-bootstrap.service",
    "deploy/systemd/cardz-market-cap-daily.service",
    "deploy/systemd/cardz-market-cap-daily.timer",
    "deploy/systemd/run-cardz-daily.sh",
    "deploy/systemd/cardz-market-cap-watchdog.service",
    "deploy/systemd/cardz-market-cap-watchdog.timer",
    "deploy/systemd/run-cardz-watchdog.sh",
    # alert template 唔喺 /etc/systemd/system 嘅話，systemd 只會喺 journal 留一行
    # "Unit not found" —— 即係加咗通報但實際靜音，比冇加更誤導。
    "deploy/systemd/cardz-market-cap-alert@.service",
    "deploy/systemd/run-cardz-alert.sh",
    # 週掃：GemRate key 一死就冇 population 歷史，所以佢屬數據鏈唔屬 optional 工具
    "deploy/systemd/cardz-gemrate-freeze.service",
    "deploy/systemd/cardz-gemrate-freeze.timer",
    "deploy/systemd/run-cardz-gemrate-freeze.sh",
    # 按需 `systemctl start`（冇 timer），但缺咗就補唔到圖
    "deploy/systemd/cardz-image-backfill.service",
    "deploy/systemd/run-cardz-image-backfill.sh",
    "deploy/systemd/README.md",
    # --- Linux installer / 狀態面板 ---
    "deploy/linux/cardz-daily-systemd.sh",
    "deploy/linux/cardz-status.sh",
    # --- Windows 排程（legacy 生產機仲行緊，未遷 Linux 之前唔可以少）---
    # 2026-07-26 由 pipelines/ 同 temp/ 搬入嚟。tests/test_daily_scheduler_contract.py
    # 直接讀 install_daily_task.ps1 嘅字串做合約測試，路徑一郁測試即紅。
    "deploy/windows/install_daily_task.ps1",
    "deploy/windows/run-cardz-daily.ps1",
    "deploy/windows/run-cardz-watchdog.ps1",
    "deploy/windows/cardz-status.ps1",
    "deploy/windows/freeze-sweep-guard.ps1",
    # --- AWS / Node container 路徑（唔使 DB、唔使 R2）---
    "apps/web/Dockerfile",
    ".dockerignore",                        # deny-all 白名單；冇咗會將 6.8 GB data/ 同 .env.private 抄入 context
    "apps/web/src/app/api/health/route.ts",  # ALB / target group health check（唔准用 `/`）
    "apps/web/src/lib/cloudflare-env.ts",    # 冇呢層 guard，Node 上跑會靜靜雞起個 workerd 子進程
    # ⚠️ git 果份**永遠**係 demo 佔位符，唔准放 production 數據落去。
    # 呢度淨係驗「有冇呢個檔」；驗「出唔出得街」係 scripts/verify_clean_clone.py 嘅
    # snapshotReadiness（一份實作，唔好喺呢度再寫多一套）。
    "data/public/seed-snapshot.json",
    "data/editorial/top100-stories.json",
    # --- 交接文檔 ---
    "PROJECT_STATE.md",          # 單一真相來源，接手第一份
    "docs/HANDOFF.md",
    "docs/AWS_HANDOFF.md",
    "docs/AWS_DEPLOY.md",
    "docs/SERVER_MIGRATION.md",
    "docs/SOAK_RUNBOOK.md",
    "docs/PACKAGING_CHECKLIST.md",   # 交付次序；冇佢就冇人知邊步先
    # --- 交付驗證閘（自己驗自己）---
    "scripts/verify_clean_clone.py",  # 動態閘：真 clone 一次，驗 LFS + snapshot 出唔出得街
    # --- Grade10 整合 ---
    "integrations/grade10/run_service.py",
    "integrations/grade10/OPERATOR_GUIDE.md",
)


class HandoffError(RuntimeError):
    """A portable handoff prerequisite is missing or unsafe."""


def is_lfs_pointer(path: Path) -> bool:
    if not path.is_file():
        return False
    prefix = path.read_bytes()[:128].replace(b"\r\n", b"\n")
    return prefix.startswith(b"version https://git-lfs.github.com/spec/v1\n")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def in_head(root: Path, relative: str) -> bool:
    """True only when the path is committed to HEAD.

    ⚠️ 唔准改返用 `git ls-files --error-unmatch` —— 嗰個 match 嘅係 **index**，
    即係 `git add` 咗但未 commit 都會報 tracked=true。2026-07-26 實測
    `data/private/cardz-active-bootstrap.tar.gz` 淨係 staged，舊版 gate 照報綠燈，
    但 `git clone` 出嚟根本冇呢個檔。clean clone 只攞得到 HEAD，所以驗 HEAD 先算數。
    """
    return _git(root, "cat-file", "-e", f"HEAD:{relative}").returncode == 0


def has_git_head(root: Path) -> bool:
    """False for a non-repo (zip 解開嘅交付包) or a repo with no commits."""
    if _git(root, "rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
        return False
    return _git(root, "rev-parse", "--verify", "--quiet", "HEAD").returncode == 0


def archive_is_tracked(root: Path, relative: Path = ARCHIVE_RELATIVE) -> bool:
    return in_head(root, relative.as_posix())


def validate_layout(root: Path) -> dict[str, Any]:
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise HandoffError(f"handoff files are missing: {', '.join(missing)}")

    attributes = (root / ".gitattributes").read_text(encoding="utf-8")
    expected = "data/private/cardz-active-bootstrap.tar.gz filter=lfs diff=lfs merge=lfs -text"
    if expected not in attributes:
        raise HandoffError("bootstrap archive is not pinned to Git LFS")

    ignored = (root / ".gitignore").read_text(encoding="utf-8")
    for required in (".env", "data/runtime/"):
        if required not in ignored:
            raise HandoffError(f"gitignore is missing required private rule: {required}")

    daily_unit = (root / "deploy/systemd/cardz-market-cap-daily.service").read_text(encoding="utf-8")
    bootstrap_unit = (root / "deploy/systemd/cardz-market-cap-bootstrap.service").read_text(encoding="utf-8")
    if "run-cardz-daily.sh" not in daily_unit or "backend.sh bootstrap --external-db" not in bootstrap_unit:
        raise HandoffError("systemd units do not invoke the portable backend launchers")

    return {"requiredFiles": len(REQUIRED_FILES), "lfsRule": "pinned", "privateRules": "present"}


PACKAGE_MANIFEST_RELATIVE = Path("HANDOFF_MANIFEST.json")


def validate_package_manifest(root: Path) -> dict[str, Any]:
    """Zip 交付路嘅完整性閘，用嚟頂替用唔到嘅 git 追蹤閘。

    `scripts/build_handoff_package.py` 打包嗰陣會逐個檔計 sha256 寫入
    `HANDOFF_MANIFEST.json`。呢度做返兩件事：

    1. 每個 `REQUIRED_FILES` 都要喺 manifest 入面（＝打包果刻真係入咗包，
       唔係解壓之後有人隨手掟返個檔入去扮齊）；
    2. 每個都要**重新計一次 sha256 同 manifest 夾返**（＝由打包到解壓之間
       冇爛過、冇被人換過）。

    呢個唔係扮 git 追蹤 —— 佢答緊另一條問題（「呢個包完唔完整」），
    但對 zip 交付嚟講，嗰條問題先係真正要答嘅嗰條。
    """
    manifest_path = root / PACKAGE_MANIFEST_RELATIVE
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entries = manifest.get("files") or {}

    missing = [name for name in REQUIRED_FILES if name not in entries]
    if missing:
        listing = "\n  - ".join(missing)
        raise HandoffError(
            f"{len(missing)} of {len(REQUIRED_FILES)} required files are NOT in "
            f"{PACKAGE_MANIFEST_RELATIVE}; the package was built incomplete:\n  - {listing}"
        )

    corrupted: list[str] = []
    for name in REQUIRED_FILES:
        target = root / name
        if not target.is_file():
            corrupted.append(f"{name} (listed in manifest but absent on disk)")
            continue
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != entries[name].get("sha256"):
            corrupted.append(f"{name} (sha256 mismatch)")
    if corrupted:
        listing = "\n  - ".join(corrupted)
        raise HandoffError(
            f"{len(corrupted)} required file(s) do not match {PACKAGE_MANIFEST_RELATIVE}; "
            f"the package is corrupt or was tampered with:\n  - {listing}"
        )

    return {
        "enforced": True,
        "source": "package-manifest",
        "checked": len(REQUIRED_FILES),
        "manifestFiles": manifest.get("fileCount", len(entries)),
        "builtAt": manifest.get("generatedAt"),
        "missingFromHead": [],
    }


def validate_tracking(root: Path) -> dict[str, Any]:
    """Prove a `git clone` would actually carry every required file.

    存在喺 working tree ≠ clone 攞得到。呢個 check 就係 `validate_layout` 同
    「真 clean clone」之間嗰道差 —— 未 commit 嘅檔喺本機睇落一切正常，
    但對面 clone 完會即刻缺件。

    唔喺 git work tree（例如 zip 上 Google Drive 嗰個交付包）就轉用
    `HANDOFF_MANIFEST.json` 做 sha256 完整性閘。**連 manifest 都冇**先至報
    `enforced: false` —— 一個既冇 git 又冇 manifest 嘅目錄，我哋確實乜都驗唔到，
    嗰陣報 false 係老實，報綠燈就係講大話。
    """
    if not has_git_head(root):
        if (root / PACKAGE_MANIFEST_RELATIVE).is_file():
            return validate_package_manifest(root)
        return {
            "enforced": False,
            "reason": (
                "not a git work tree with a HEAD commit, and no "
                f"{PACKAGE_MANIFEST_RELATIVE} to verify against"
            ),
        }

    missing = [name for name in REQUIRED_FILES if not in_head(root, name)]
    result = {"enforced": True, "checked": len(REQUIRED_FILES), "missingFromHead": missing}
    if missing:
        listing = "\n  - ".join(missing)
        raise HandoffError(
            f"{len(missing)} of {len(REQUIRED_FILES)} required files exist on disk but are NOT in HEAD; "
            f"a clean clone would be missing:\n  - {listing}"
        )
    return result


def validate_archive(root: Path, *, require_archive: bool, require_tracked: bool, verify_archive: bool) -> dict[str, Any]:
    archive = root / ARCHIVE_RELATIVE
    if not archive.is_file():
        if require_archive:
            raise HandoffError(f"required bootstrap archive is missing: {ARCHIVE_RELATIVE.as_posix()}")
        return {"present": False, "tracked": archive_is_tracked(root), "verified": False}
    if is_lfs_pointer(archive):
        raise HandoffError("bootstrap archive is an unresolved Git LFS pointer; run git lfs pull")
    tracked = archive_is_tracked(root)
    if require_tracked and not tracked:
        raise HandoffError("bootstrap archive is not Git-tracked; a clean clone cannot restore it")
    result: dict[str, Any] = {"present": True, "tracked": tracked, "verified": False, "bytes": archive.stat().st_size}
    if verify_archive:
        sys.path.insert(0, str(root / "scripts"))
        import bootstrap_archive

        manifest = bootstrap_archive.verify_archive(archive)
        result.update({"verified": True, "lockId": manifest["lockId"], "entries": len(manifest["entries"])})
    return result


def verify_handoff(root: Path, *, require_archive: bool, require_tracked: bool, verify_archive: bool) -> dict[str, Any]:
    """Run every stage and report all of them.

    刻意唔喺第一個 failure 就停 —— 交付整備要一次過睇晒所有窿，
    唔係修一個再跑一次先發現下一個。
    """
    root = root.resolve()
    stages: tuple[tuple[str, Any], ...] = (
        ("layout", lambda: validate_layout(root)),
        ("tracking", lambda: validate_tracking(root)),
        (
            "archive",
            lambda: validate_archive(
                root,
                require_archive=require_archive,
                require_tracked=require_tracked,
                verify_archive=verify_archive,
            ),
        ),
    )
    report: dict[str, Any] = {"root": str(root), "failures": []}
    for name, check in stages:
        try:
            report[name] = check()
        except HandoffError as error:
            report[name] = {"ok": False, "error": str(error)}
            report["failures"].append(name)
    report["ok"] = not report["failures"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify CARDZ clean-clone/AWS handoff prerequisites")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--require-archive", action="store_true", help="Fail if the Git LFS bootstrap archive is absent")
    parser.add_argument("--require-tracked", action="store_true", help="Fail if the archive is not tracked by Git")
    parser.add_argument("--verify-archive", action="store_true", help="Verify archive paths, lock hash and per-entry checksums")
    parser.add_argument("--json", action="store_true", help="Print the machine-readable report only")
    args = parser.parse_args()
    report = verify_handoff(
        args.root,
        require_archive=args.require_archive,
        require_tracked=args.require_tracked,
        verify_archive=args.verify_archive,
    )
    if args.json:
        print(json.dumps(report, sort_keys=True))
        return 0 if report["ok"] else 1

    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    if report["ok"]:
        print("\nPASS: handoff prerequisites satisfied")
        return 0
    print(f"\nFAIL: {len(report['failures'])} stage(s) failed: {', '.join(report['failures'])}", file=sys.stderr)
    for stage in report["failures"]:
        print(f"\n[{stage}] {report[stage]['error']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
