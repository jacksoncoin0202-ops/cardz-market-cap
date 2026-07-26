# -*- coding: utf-8 -*-
"""Publish 收尾次序 regression（2026-07-26）。

`finalize_local_candidate_then_publish` 原本嘅次序係：

    verify_images（嚴格）→ promote → quarantine → publish

但 `data/public/market-assets` 係 content-addressed 累積目錄——每次卡圖重算都會
留低舊 sha 嘅檔（實測 3415 個檔、snapshot 只引用約 1080 個）。嚴格 verify 見到
未引用檔就 exit 1，`run_checked` 拋錯，於是**清走嗰啲檔嘅 quarantine 步永遠去唔到**。
閘因為垃圾而 fail，掃垃圾嗰步喺閘之後：條 publish 鏈自己鎖死自己。

而家改成兩 pass：

    verify(--allow-unreferenced) → promote → quarantine → verify(嚴格) → publish

第一 pass 嘅職責係喺搬任何檔之前確認 snapshot 本身完好（每張卡圖存在、hash 啱、
尺寸啱、有 QC 記錄）——destructive 動作之前一定要有驗證。第二 pass 保住原本個
不變式：發佈嗰刻目錄唔可以仲有孤兒檔。

呢個檔同時釘住兩個方向：唔准調返一 pass（死鎖翻兜），亦唔准索性刪咗嚴格 pass
（咁等於為咗解死鎖而閹咗檢查，比原本更差）。
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipelines"))

import run_daily  # noqa: E402
from verify_images import verify  # noqa: E402


def webp_bytes(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (width, height), (10, 20, 30, 255)).save(output, format="WEBP", lossless=True)
    return output.getvalue()


class FinalizeOrderTests(unittest.TestCase):
    """用 recorder 換走每個副作用，淨係睇步驟次序。"""

    def setUp(self) -> None:
        self.calls: list[str] = []
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.manifest = self.temp / "image-qc.json"
        self.manifest.write_text("{}", encoding="utf-8")

        def fake_run_checked(command, **_kwargs):
            command = [str(part) for part in command]
            if any(part.endswith("verify_images.py") for part in command):
                self.calls.append("verify:lenient" if "--allow-unreferenced" in command else "verify:strict")
            else:
                self.calls.append("publish")

        for name, replacement in (
            ("run_checked", fake_run_checked),
            ("promote_file", lambda *_a, **_k: self.calls.append("promote")),
            ("read_json", lambda *_a, **_k: {}),
            ("quarantine_unreferenced_assets", lambda *_a, **_k: self.calls.append("quarantine") or 7),
        ):
            original = getattr(run_daily, name)
            setattr(run_daily, name, replacement)
            self.addCleanup(setattr, run_daily, name, original)

    def finalize(self, *, production: bool = False) -> int:
        return run_daily.finalize_local_candidate_then_publish(
            candidate_snapshot=self.temp / "snapshot.json",
            candidate_image_manifest=self.manifest,
            assets_out=self.temp / "market-assets",
            snapshot_destination=self.temp / "out-snapshot.json",
            image_manifest_destination=self.temp / "out-image-qc.json",
            quarantine_root=self.temp / "quarantine",
            publish_command=["node", "publish-snapshot.mjs"],
            production=production,
            timeout=60,
        )

    def test_quarantine_is_sandwiched_between_a_lenient_and_a_strict_verify(self) -> None:
        self.assertEqual(self.finalize(), 7)
        self.assertEqual(
            self.calls,
            ["verify:lenient", "promote", "promote", "quarantine", "verify:strict", "publish"],
        )

    def test_snapshot_is_validated_before_anything_is_moved(self) -> None:
        # destructive 動作之前一定要有驗證，否則 snapshot 壞嘅時候會先搬走好檔。
        self.finalize()
        self.assertLess(self.calls.index("verify:lenient"), self.calls.index("quarantine"))

    def test_strict_pass_still_gates_publish(self) -> None:
        # 唔准為咗解死鎖而索性刪咗嚴格 pass。
        self.finalize()
        self.assertIn("verify:strict", self.calls)
        self.assertLess(self.calls.index("verify:strict"), self.calls.index("publish"))

    def test_lenient_pass_never_leaks_into_the_final_gate(self) -> None:
        self.finalize()
        self.assertEqual(self.calls.count("verify:lenient"), 1)
        self.assertEqual(self.calls.count("verify:strict"), 1)

    def test_a_failing_first_pass_stops_before_quarantine(self) -> None:
        def exploding_run_checked(command, **_kwargs):
            self.calls.append("verify:lenient")
            raise RuntimeError("candidate image is missing")

        run_daily.run_checked = exploding_run_checked
        with self.assertRaises(RuntimeError):
            self.finalize()
        self.assertNotIn("quarantine", self.calls)
        self.assertNotIn("promote", self.calls)

    def test_production_keeps_strict_semantic_on_both_passes(self) -> None:
        seen: list[list[str]] = []

        def capture(command, **_kwargs):
            command = [str(part) for part in command]
            if any(part.endswith("verify_images.py") for part in command):
                seen.append(command)

        run_daily.run_checked = capture
        self.finalize(production=True)
        self.assertEqual(len(seen), 2)
        for command in seen:
            self.assertIn("--strict-semantic", command)


class AllowUnreferencedFlagTests(unittest.TestCase):
    """個 flag 本身淨係關掉孤兒檢查，唔准順手放生卡圖本身嘅問題。"""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.assets = self.temp / "market-assets"
        self.assets.mkdir()
        master = webp_bytes(40, 56)
        self.sha = hashlib.sha256(master).hexdigest()
        (self.assets / f"{self.sha}.webp").write_bytes(master)
        for suffix, size in (("200", 20), ("600", 30)):
            (self.assets / f"{self.sha}_{suffix}.webp").write_bytes(webp_bytes(size, size))
        # 舊 sha 遺留檔：quarantine 未行之前呢個係正常狀態。
        (self.assets / f"{'7' * 64}.webp").write_bytes(webp_bytes(40, 56))
        self.card = {
            "id": "card-1",
            "image": {
                "src": f"/market-assets/{self.sha}.webp",
                "sha256": self.sha,
                "width": 40,
                "height": 56,
                "kind": "raw_front",
                "variants": {
                    "200": f"/market-assets/{self.sha}_200.webp",
                    "600": f"/market-assets/{self.sha}_600.webp",
                },
            },
        }
        self.snapshot_path = self.temp / "snapshot.json"
        self.write_snapshot()

    def write_snapshot(self) -> None:
        self.snapshot_path.write_text(
            json.dumps({"top100": [self.card], "watchlist": []}), encoding="utf-8"
        )

    def errors(self, *, allow_unreferenced: bool) -> list[str]:
        # 固定濾走「top100 唔夠 100 張」——呢個 fixture 得一張卡，同呢度要釘嘅
        # 孤兒檔行為冇關；為咗一個 count 而砌 100 張假卡只會遮住真正嘅斷言。
        raw = verify(self.snapshot_path, self.assets, allow_unreferenced=allow_unreferenced)
        return [error for error in raw if "top100 does not contain" not in error]

    def test_accumulated_stale_files_no_longer_block_the_first_pass(self) -> None:
        self.assertEqual(self.errors(allow_unreferenced=True), [])

    def test_the_same_state_still_fails_the_strict_pass(self) -> None:
        errors = self.errors(allow_unreferenced=False)
        self.assertEqual(len(errors), 1)
        self.assertIn("1 unreferenced", errors[0])

    def test_a_missing_card_image_fails_even_with_the_flag_on(self) -> None:
        (self.assets / f"{self.sha}.webp").unlink()
        self.assertIn("card-1: image is missing", self.errors(allow_unreferenced=True))

    def test_a_hash_mismatch_fails_even_with_the_flag_on(self) -> None:
        (self.assets / f"{self.sha}.webp").write_bytes(webp_bytes(41, 57))
        errors = self.errors(allow_unreferenced=True)
        self.assertTrue(
            any("hash mismatch" in error for error in errors),
            f"flag 唔應該放生 hash 唔啱：{errors}",
        )

    def test_a_dimension_mismatch_fails_even_with_the_flag_on(self) -> None:
        self.card["image"]["width"] = 999
        self.write_snapshot()
        self.assertIn("card-1: image dimensions do not match", self.errors(allow_unreferenced=True))

    def test_the_flag_defaults_to_off(self) -> None:
        # 忘記傳參數嘅呼叫者要攞到嚴格行為。
        default = verify(self.snapshot_path, self.assets)
        self.assertTrue(any("unreferenced" in error for error in default), default)


if __name__ == "__main__":
    unittest.main()
