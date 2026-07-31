"""Regression guard for the handoff packager's secret scan.

點解要有呢個測試：`scripts/build_handoff_package.py` 個掃描器係唯一一道
攔住 DB 密碼同 GemRate key 入 zip 嘅閘。一個冇測試嘅閘，靜靜壞咗都冇人知 ——
而佢壞嘅代價係將 credential 寄上 Google Drive。

下面每一條 case 都係實測執返嚟嘅（2026-07-26）：`MUST_FIRE` 嗰啲係真係試過
會漏網嘅形態，`MUST_STAY_SILENT` 嗰啲係真係喺呢個 repo 出現過嘅假陽性。
放寬任何一條規則之前，呢度會即刻紅畀你睇。
"""

from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# ⚠️ 落面三個常數係**砌返出嚟**，唔係寫死喺源碼。
#
# 唔係為咗靚 —— 呢個檔本身會入交付包，而打包器嘅 literal 指紋閘會掃佢。
# 直接寫 PEM header / AWS key ID 落去（連**呢段註解**都唔可以寫），打包會即刻
# abort，個包出唔到。實測過：頭一版呢段註解自己抄咗個 PEM header 落去解釋，
# 結果就係佢自己整 fail 咗個 build。
#
# 正路修法就係咁：**唔可以**為咗遷就測試而豁免 `tests/` 嘅指紋閘 —— 咁做即係
# 話「有人將真 AWS key 擺入 test fixture 就照寄出去」。閘要繼續嚴，測試自己避開。
# 下次有人想「執靚」佢變返字面值：試下先，你會即刻見到 build abort。
_PEM_HEADER = "-----BEGIN RSA PRIVATE" + " KEY-----"
_PEM_FOOTER = "-----END RSA PRIVATE" + " KEY-----"
_AWS_KEY_ID = "AKIA" + "IOSFODNN7EXAMPLE"
_HEX40 = "a3f9c1d0b7e24f8a" + "9c6d1e0f3b8a7c2d" + "5e4f6a1b"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "cardz_build_handoff_package", ROOT / "scripts" / "build_handoff_package.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


packager = _load()


# 一定要攔到。key = 相對路徑，value = 檔案內容。
MUST_FIRE: dict[str, str] = {
    # 路徑閘：檔名一望就知係 secret 檔，唔使睇內容
    "data/runtime/config/backend.env": "CARDZ_DB_PASSWORD=whatever-it-is\n",
    "data/runtime/config/gemrate.env": "GEMRATE_API_KEY=whatever-it-is\n",
    ".env.private": "CARDZ_STAGING_R2_BUCKET=whatever\n",
    "certs/server.pem": f"{_PEM_HEADER}\nZm9v\n{_PEM_FOOTER}\n",
    # 內容閘：帶引號嘅字面值
    #   `_` 前綴 —— 呢個 repo 最常見嘅命名，用 `\b` 開頭嘅 regex 會全部漏網
    "src/settings.py": 'CARDZ_DB_PASSWORD = "hunter2xyz"\n',
    #   JSON 風格 `"apiKey": "…"` —— key 同冒號之間有個閂引號
    "config/live.json": '{"apiKey": "sk-live-9f8e7d6c5b4a3210zzzz"}\n',
    #   賦值語境嘅 40-hex（裸 40-hex 唔算，嗰啲係卡 ID）
    "src/client.ts": f'const token = "{_HEX40}";\n',
    #   廠商特徵。順帶鎖住 `.tf`：副檔名白名單年代呢個檔完全冇掃過。
    "deploy/aws.tf": f'access_key = "{_AWS_KEY_ID}"\n',
}

# 一定要靜。全部係呢個 repo 真實出現過而被誤報嘅形態。
MUST_STAY_SILENT: dict[str, str] = {
    # kwarg 傳變數，唔係字面值（pipelines/db_runtime.py 果種）
    "pipelines/db.py": "connect(host=host, password=password, port=port)\n",
    # 讀環境變數
    "pipelines/env.py": 'key = os.environ.get("GEMRATE_API_KEY", "")\n',
    # 只有變數名冇值嘅範本 —— 空值唔可以食到下一行去
    ".env.example": "GEMRATE_API_KEY=\nCARDZ_DB_PASSWORD=\n",
    # 1Password 引用（docs/RUNBOOK.md §Secret injection 教嘅正路寫法）
    "docs/RUNBOOK.md": "GEMRATE_API_KEY=op://Private/CARDZ Market Data/GemRate API Key\n",
    # docker compose 嘅必填變數語法
    "compose.backend.yaml": "  MYSQL_PASSWORD: ${CARDZ_DB_PASSWORD:?CARDZ_DB_PASSWORD is required}\n",
    # shell / js 入面嘅 regex 字面值
    "deploy/linux/install.sh": "grep -qhE '^[[:space:]]*GEMRATE_API_KEY=[[:space:]]*[^[:space:]]' \"$f\"\n",
    "scripts/check.mjs": "const gemRateKey = envExample.match(/^GEMRATE_API_KEY=$/m);\n",
    # 40-hex 卡 ID（CLAUDE.md：ID 一律 40-char SHA1 hex）—— 全 repo 幾萬個
    "data/roster.json": f'{{"variantId": "{_HEX40}", "name": "Charizard"}}\n',
}


class HandoffSecretScanTest(unittest.TestCase):
    def _scan(self, files: dict[str, str]) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as temporary:
            staged = Path(temporary)
            for rel, body in files.items():
                target = staged / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(body, encoding="utf-8")
            names = sorted(files)
            return {f["file"]: f["rule"] for f in packager.scan_for_secrets(staged, names)}

    def test_every_known_secret_shape_is_caught(self) -> None:
        flagged = self._scan(MUST_FIRE)
        for rel in MUST_FIRE:
            with self.subTest(rel):
                self.assertIn(rel, flagged, f"secret scan MISSED {rel} - the package would ship it")

    def test_known_false_positives_stay_silent(self) -> None:
        flagged = self._scan(MUST_STAY_SILENT)
        for rel in MUST_STAY_SILENT:
            with self.subTest(rel):
                self.assertNotIn(rel, flagged, f"secret scan false-positived on {rel}")

    def test_tests_directory_skips_the_assignment_heuristic_only(self) -> None:
        """`tests/` 唔跑賦值啟發式，但路徑閘同 literal 指紋照跑。

        呢個係有意識嘅取捨（見 build_handoff_package.CONTENT_ASSIGN_SKIP_PREFIXES）。
        鎖住佢，因為「tests/ 完全唔掃」同「tests/ 只係唔跑賦值閘」差好遠。
        """
        flagged = self._scan({
            "tests/test_thing.py": 'CARDZ_DB_PASSWORD = "injected-secret"\n',   # 賦值閘 -> 放行
            "tests/fixtures/.env": "CARDZ_DB_PASSWORD=anything\n",              # 路徑閘 -> 照攔
            "tests/test_aws.py": f'key = "{_AWS_KEY_ID}"\n',                    # 指紋閘 -> 照攔
        })
        self.assertNotIn("tests/test_thing.py", flagged)
        self.assertIn("tests/fixtures/.env", flagged)
        self.assertIn("tests/test_aws.py", flagged)

    def test_findings_never_carry_file_contents(self) -> None:
        """報告只可以有檔名同規則名。呢個閘印出嚟嘅嘢本身唔可以係洩漏。"""
        with tempfile.TemporaryDirectory() as temporary:
            staged = Path(temporary)
            fake_value = "sk-live-" + "topsecretvalue1234"
            fake_assignment = "API" + "_KEY = " + repr(fake_value) + "\n"
            (staged / "app.py").write_text(
                fake_assignment,
                encoding="utf-8",
            )
            findings = packager.scan_for_secrets(staged, ["app.py"])
        self.assertTrue(findings)
        for finding in findings:
            for value in finding.values():
                self.assertNotIn(fake_value, value)

    def test_required_files_list_is_not_duplicated(self) -> None:
        """打包器一定要問返 verify_handoff 攞 REQUIRED_FILES，唔可以自己抄一份。"""
        module = packager.verify_handoff_module()
        self.assertGreater(len(module.REQUIRED_FILES), 0)
        source = (ROOT / "scripts" / "build_handoff_package.py").read_text(encoding="utf-8")
        self.assertNotIn("REQUIRED_FILES: tuple", source, "packager must not keep its own copy of the list")


if __name__ == "__main__":
    unittest.main()
