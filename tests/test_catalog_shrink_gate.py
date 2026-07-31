# -*- coding: utf-8 -*-
"""Catalog 縮水閘 + immutable pointer regression。

每日鏈以 runtime latest.json指向上一代immutable generation，tracked demo seed永不覆寫。

Day N 嘅輸出就係 Day N+1 嘅輸入。實測跑出嚟：舊 pack 360 張卡 export 出 192 張（70 張
因為冇合格圖被剔），promote + quarantine 搬走 3667 個檔；跟住攞返嗰個 360 張嘅舊 pack
再 export，只出到 16 張——因為佢哋張圖已經俾 quarantine 搬走咗。

即係話呢條鏈係**單向棘輪**：catalog 淨係可以維持或者跌，永遠唔會自己升返，一跌即刻
不可逆。而 360 → 192（-46.7%）嗰次冇任何 gate 攔過，成個 run exit 0「成功」。

呢個檔釘住兩件事：

1. 縮水閘一定要喺 **promote 同 quarantine 之前**。擺後面等於冇用——quarantine 搬完
   檔先發現跌得太多，已經返唔到轉頭。
2. quarantine 要留審計記錄。搬走 3667 個檔而只 log 一個 `3667`，事後查唔到「邊個 run
   搬走咗邊啲檔」，亦搬唔返。

同時釘住反方向：唔准為咗令個閘容易過而將門檻設到形同虛設（預設值必須真係會攔到
實測嗰單 -46.7%），亦唔准索性令批准變成永久放行。
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from inspect import getsource
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipelines"))

import run_daily  # noqa: E402
from g10_public_snapshot import (  # noqa: E402
    QUARANTINE_MANIFEST_NAME,
    quarantine_unreferenced_assets,
)


def snapshot_with(card_count: int) -> dict:
    """top100 頂 100 張，其餘落 watchlist——同真實 snapshot 一樣嘅分佈。"""

    top100 = [{"id": f"top-{index}"} for index in range(min(card_count, 100))]
    watchlist = [{"id": f"watch-{index}"} for index in range(max(card_count - 100, 0))]
    return {"schemaVersion": "2.0.0", "top100": top100, "watchlist": watchlist}


class CatalogShrinkGateTests(unittest.TestCase):
    """用 recorder 換走每個副作用，睇個閘有冇喺副作用發生之前攔到。"""

    def setUp(self) -> None:
        self.calls: list[str] = []
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.manifest = self.temp / "image-qc.json"
        self.manifest.write_text("{}", encoding="utf-8")
        self.candidate = self.temp / "candidate-snapshot.json"
        self.published = self.temp / "seed-snapshot.json"

        def fake_run_checked(command, **_kwargs):
            command = [str(part) for part in command]
            if any(part.endswith("verify_images.py") for part in command):
                self.calls.append("verify:lenient" if "--allow-unreferenced" in command else "verify:strict")
            else:
                self.calls.append("publish")

        original = run_daily.run_checked
        run_daily.run_checked = fake_run_checked
        self.addCleanup(setattr, run_daily, "run_checked", original)

    def write_snapshots(self, *, before: int | None, after: int) -> None:
        if before is not None:
            self.published.write_text(json.dumps(snapshot_with(before)), encoding="utf-8")
        self.candidate.write_text(json.dumps(snapshot_with(after)), encoding="utf-8")

    def finalize(self, **overrides):
        return run_daily.finalize_local_candidate_then_publish(
            candidate_snapshot=self.candidate,
            candidate_image_manifest=self.manifest,
            assets_out=self.temp / "market-assets",
            published_snapshot=self.published if self.published.is_file() else None,
            publish_command=["node", "publish-snapshot.mjs"],
            production=False,
            timeout=60,
            **overrides,
        )

    def test_the_observed_collapse_now_stops_the_run(self) -> None:
        # 實測嗰單：360 → 192，-46.7%。呢個係個閘存在嘅唯一理由。
        self.write_snapshots(before=360, after=192)
        with self.assertRaises(run_daily.CatalogShrinkError):
            self.finalize()

    def test_nothing_is_promoted_or_quarantined_when_the_gate_fires(self) -> None:
        # quarantine 係不可逆嘅：閘擺喺佢後面等於冇閘。
        self.write_snapshots(before=360, after=192)
        with self.assertRaises(run_daily.CatalogShrinkError):
            self.finalize()
        self.assertNotIn("quarantine", self.calls)
        self.assertNotIn("promote", self.calls)
        self.assertNotIn("publish", self.calls)

    def test_the_gate_runs_after_the_snapshot_itself_is_verified(self) -> None:
        # 第一 pass 係非破壞性嘅，行咗先報縮水，錯誤訊息先至可信。
        self.write_snapshots(before=360, after=192)
        with self.assertRaises(run_daily.CatalogShrinkError):
            self.finalize()
        self.assertEqual(self.calls, ["verify:lenient"])

    def test_the_error_names_the_numbers_and_how_to_approve(self) -> None:
        self.write_snapshots(before=360, after=192)
        with self.assertRaises(run_daily.CatalogShrinkError) as caught:
            self.finalize()
        message = str(caught.exception)
        self.assertIn("360", message)
        self.assertIn("192", message)
        self.assertIn("46.7%", message)
        self.assertIn("5.0%", message)
        self.assertIn("--max-catalog-shrink-pct", message)

    def test_a_shrink_inside_the_threshold_still_publishes(self) -> None:
        # 真係有卡落榜嘅日子唔應該攔——閘唔可以嚴到冇人用得。
        self.write_snapshots(before=200, after=195)
        self.assertEqual(self.finalize(), 0)
        self.assertEqual(self.calls, ["verify:lenient", "publish"])

    def test_an_explicit_approval_lets_a_large_shrink_through(self) -> None:
        # 合法大收縮（例如一次過清走一批落榜卡）要行得，但要人明確講。
        self.write_snapshots(before=360, after=192)
        self.assertEqual(self.finalize(max_catalog_shrink_pct=50), 0)
        self.assertIn("publish", self.calls)

    def test_the_suggested_approval_value_actually_works(self) -> None:
        # 錯誤訊息叫人用嘅數字，照抄落去要真係過到，唔好叫人試極都唔啱。
        self.write_snapshots(before=360, after=192)
        with self.assertRaises(run_daily.CatalogShrinkError) as caught:
            self.finalize()
        suggested = float(re.search(r"--max-catalog-shrink-pct (\d+(?:\.\d+)?)", str(caught.exception)).group(1))
        self.assertEqual(self.finalize(max_catalog_shrink_pct=suggested), 0)

    def test_approval_is_per_run_and_never_persisted(self) -> None:
        # 批准一次唔等於永久放行：下一次唔帶 flag 要照攔。
        self.write_snapshots(before=360, after=192)
        self.finalize(max_catalog_shrink_pct=50)
        self.calls.clear()
        with self.assertRaises(run_daily.CatalogShrinkError):
            self.finalize()

    def test_growth_is_never_blocked(self) -> None:
        self.write_snapshots(before=192, after=360)
        self.assertEqual(self.finalize(), 0)

    def test_an_unchanged_catalog_is_never_blocked(self) -> None:
        self.write_snapshots(before=192, after=192)
        self.assertEqual(self.finalize(), 0)

    def test_the_first_publish_has_no_baseline_to_compare(self) -> None:
        # 未有 seed-snapshot.json 嘅時候冇嘢比較，唔應該攔死首次發佈。
        self.write_snapshots(before=None, after=192)
        self.assertEqual(self.finalize(), 0)

    def test_a_total_wipe_is_blocked(self) -> None:
        # 最惡劣嘅一種：export 出零張卡，跟住 quarantine 清晒全部圖。
        self.write_snapshots(before=192, after=0)
        with self.assertRaises(run_daily.CatalogShrinkError):
            self.finalize()
        self.assertNotIn("quarantine", self.calls)

    def test_the_default_threshold_is_not_a_rubber_stamp(self) -> None:
        # 唔准為咗令個閘容易過而將預設調到形同虛設。
        self.assertGreater(run_daily.DEFAULT_MAX_CATALOG_SHRINK_PCT, 0)
        self.assertLess(run_daily.DEFAULT_MAX_CATALOG_SHRINK_PCT, 46.7)

    def test_a_caller_that_forgets_the_argument_still_gets_the_gate(self) -> None:
        # 預設值要 fail-closed：漏傳參數唔可以等於關閘。
        self.write_snapshots(before=360, after=192)
        with self.assertRaises(run_daily.CatalogShrinkError):
            self.finalize()


class CatalogShrinkCliWiringTests(unittest.TestCase):
    """個閘唔接到 CLI 就等於冇得批准；接錯咗就等於冇閘。"""

    def source(self) -> str:
        return re.sub(r"\s+", " ", getsource(run_daily.main))

    def test_the_cli_exposes_the_override(self) -> None:
        self.assertIn('"--max-catalog-shrink-pct"', self.source())

    def test_the_cli_value_reaches_the_gate(self) -> None:
        self.assertIn("max_catalog_shrink_pct=args.max_catalog_shrink_pct", self.source())

    def test_the_cli_default_is_the_shared_constant(self) -> None:
        # 兩邊各寫一個數字遲早會分叉，到時 --help 講嘅同實際行為唔同。
        self.assertIn("default=DEFAULT_MAX_CATALOG_SHRINK_PCT", self.source())


class QuarantineManifestTests(unittest.TestCase):
    """搬走 3667 個檔而只回傳一個數字係查無可查，manifest 係唯一可以搬返嘅根據。"""

    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        # quarantine_unreferenced_assets 只肯處理 .../public/market-assets。
        self.assets = self.temp / "public" / "market-assets"
        self.assets.mkdir(parents=True)
        self.quarantine = self.temp / "quarantine" / "run-20260726"
        live = "1" * 64
        (self.assets / f"{live}.webp").write_bytes(b"live")
        self.snapshot = {
            "top100": [{"id": "card-1", "image": {"src": f"/market-assets/{live}.webp"}}],
            "watchlist": [],
        }
        self.stale_names = [f"{'8' * 64}_200.webp", f"{'9' * 64}.webp"]
        for name in self.stale_names:
            (self.assets / name).write_bytes(b"stale")

    def manifest(self) -> dict:
        return json.loads((self.quarantine / QUARANTINE_MANIFEST_NAME).read_text(encoding="utf-8"))

    def test_every_moved_file_is_named_in_the_manifest(self) -> None:
        self.assertEqual(quarantine_unreferenced_assets(self.assets, self.snapshot, self.quarantine), 2)
        recorded = {entry["name"] for entry in self.manifest()["files"]}
        self.assertEqual(recorded, set(self.stale_names))

    def test_the_manifest_records_where_each_file_came_from(self) -> None:
        # 搬返嘅時候要知返原本喺邊，唔可以淨得個檔名。
        quarantine_unreferenced_assets(self.assets, self.snapshot, self.quarantine)
        for entry in self.manifest()["files"]:
            self.assertEqual(Path(entry["originalPath"]), self.assets / entry["name"])

    def test_the_manifest_records_when_and_which_run(self) -> None:
        quarantine_unreferenced_assets(self.assets, self.snapshot, self.quarantine)
        document = self.manifest()
        self.assertTrue(document["quarantinedAt"].endswith("Z"), document["quarantinedAt"])
        self.assertEqual(document["run"], "run-20260726")
        self.assertEqual(document["count"], 2)

    def test_the_manifest_matches_what_actually_landed_in_quarantine(self) -> None:
        quarantine_unreferenced_assets(self.assets, self.snapshot, self.quarantine)
        landed = {path.name for path in self.quarantine.iterdir()} - {QUARANTINE_MANIFEST_NAME}
        self.assertEqual(landed, {entry["name"] for entry in self.manifest()["files"]})

    def test_live_assets_are_neither_moved_nor_listed(self) -> None:
        # manifest 唔准撈埋仲用緊嘅檔——照住佢搬返會蓋掉生效中嘅卡圖。
        quarantine_unreferenced_assets(self.assets, self.snapshot, self.quarantine)
        self.assertTrue((self.assets / f"{'1' * 64}.webp").is_file())
        self.assertNotIn(f"{'1' * 64}.webp", {entry["name"] for entry in self.manifest()["files"]})

    def test_no_manifest_is_written_when_nothing_moves(self) -> None:
        # 冇動作就唔應該留低一個空記錄，否則審計時分唔清邊個 run 真係搬過嘢。
        for name in self.stale_names:
            (self.assets / name).unlink()
        self.assertEqual(quarantine_unreferenced_assets(self.assets, self.snapshot, self.quarantine), 0)
        self.assertFalse(self.quarantine.exists())

    def test_the_manifest_never_counts_as_a_quarantined_asset(self) -> None:
        # manifest 寫入 quarantine 目錄，唔准反過來影響回傳嘅搬檔數。
        self.assertEqual(quarantine_unreferenced_assets(self.assets, self.snapshot, self.quarantine), 2)
        self.assertEqual(self.manifest()["count"], 2)


if __name__ == "__main__":
    unittest.main()
