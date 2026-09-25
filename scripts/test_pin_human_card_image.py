#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""人手換圖：pin tool 本身 + 兩條 auto lane 唔准推翻人手，全部 fake cursor，唔使連 DB。

DADDY 2026-09-26：圖啱先；錯卡／EN 用 JP 圖／overlay／唔係卡正面要換圖，SAMPLE 水印但卡啱
照出街，有乾淨正確版先換。人手 reject 係 hard authority。一個 reject 等於一個換圖決定，所以 pipelines/pin_human_card_image.py
係唯一執行點；呢個檔守：

  A. SNK EN lane（collect_control._persist_prepared_snk_en）：registry 入面嘅 sha
     唔再寫 QC（佢會變成最新、public_allowed=1 嗰行）亦唔插 acceptance；當前係人手
     pin 嗰陣唔 supersede、唔郁 freeze。
  B. PC lane（rebuild_036._persist_pc_product_image）：當前 acceptance 係人手
     就拋，唔插 acceptance、唔寫 freeze（stage_image_bind 會 rollback 成張卡）。
  C. pin tool：冇替換圖就唔跑；dry-run 開 READ ONLY transaction、一行都唔寫；
     --apply 喺 orchestrator lease + DB-writer lease 入面，逐張卡一個 transaction，
     寫完經 FE predicate 讀返唔係新 sha 就 rollback；stale manifest／新圖本身已 reject
     ／新舊一樣 → 拒絕，一行都唔寫。

每條 case 都有對照組（應該寫嘅真係寫到），證明 fake 真係行到嗰句 SQL。
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as CC  # noqa: E402
import pin_human_card_image as PIN  # noqa: E402
import rebuild_036 as R  # noqa: E402

FAILED: list[str] = []
NOW = datetime(2026, 9, 25, 12, 0, 0)


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


class FakeCursor:
    """Answers SELECTs from a responder; logs every statement."""

    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn
        self._result: Any = None
        self.lastrowid = 0
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        compact = " ".join(sql.split())
        self.conn.log.append((compact, params))
        if not compact.upper().startswith(("SELECT", "SET", "START")):
            self.conn.pending.append((compact, params))
        self._result, self.lastrowid, self.rowcount = self.conn.respond(compact, params)

    def fetchone(self) -> Any:
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def fetchall(self) -> list[Any]:
        if self._result is None:
            return []
        return self._result if isinstance(self._result, list) else [self._result]


class FakeConn:
    def __init__(self, respond: Callable[[str, Any], tuple[Any, int, int]]) -> None:
        self.respond = respond
        self.log: list[tuple[str, Any]] = []
        self.pending: list[tuple[str, Any]] = []
        self.committed: list[tuple[str, Any]] = []
        self.events: list[str] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.events.append("commit")
        self.committed.extend(self.pending)
        self.pending = []

    def rollback(self) -> None:
        self.events.append("rollback")
        self.pending = []

    def close(self) -> None:
        self.events.append("close")


def writes(statements: list[tuple[str, Any]], needle: str) -> list[tuple[str, Any]]:
    return [item for item in statements if needle in item[0]]


@contextlib.contextmanager
def patched(target: Any, **attrs: Any):
    saved = {name: getattr(target, name) for name in attrs}
    for name, value in attrs.items():
        setattr(target, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(target, name, value)


# ------------------------------------------------------------ A. SNK EN lane

SHA_OLD = "a" * 64
SHA_NEW = "b" * 64


def _snk_prepared(content_sha: str) -> SimpleNamespace:
    image = SimpleNamespace(
        content=b"x", content_sha256=content_sha, mime_type="image/webp",
        width=429, height=600, transform={"t": 1}, transform_sha256="c" * 64,
        qc_version="snk-en-default-v1",
    )
    return SimpleNamespace(
        variant_id=7, external_id="123", image=image,
        master_payload_sha256="d" * 64, downloaded_bytes_sha256="e" * 64,
        captured_at=NOW, default_image_url_sha256="f" * 64,
        default_image_url="https://example.invalid/i.png", source_observed_at=NOW,
        product_url="https://snkrdunk.com/en/trading-cards/123",
        product_page_payload_sha256="1" * 64, product_page_observed_at=NOW,
        identity_evidence_sha256="2" * 64,
    )


def run_snk(*, registry_hit: bool, current: str | None) -> FakeConn:
    """current: None | 'snk_other' | 'snk_same' | 'human'."""

    seen: dict[str, str] = {}

    def respond(sql: str, params: Any) -> tuple[Any, int, int]:
        if sql.startswith("SELECT id FROM market_image_asset"):
            return {"id": 11}, 0, 1
        if "FROM market_image_rejection_registry" in sql:
            return ({"1": 1} if registry_hit else None), 0, 0
        if sql.startswith("SELECT id FROM market_snk_en_storefront_lineage"):
            seen["lineage"] = params[0]
            return {"id": 21}, 0, 1
        if sql.startswith("SELECT id FROM market_snk_en_product_page_authority"):
            return {"id": 31}, 0, 1
        if "FROM market_canonical_image_acceptance ca" in sql:
            if current is None:
                return None, 0, 0
            return {
                "id": 99,
                "lineage_sha256": seen["lineage"] if current == "snk_same" else "9" * 64,
                "accepted_by": "human" if current == "human" else CC.SNK_EN_ACCEPTED_BY,
            }, 0, 1
        if sql.startswith("INSERT INTO market_canonical_image_acceptance"):
            return None, 41, 1
        return None, 0, 1

    conn = FakeConn(respond)
    with patched(
        CC,
        _assert_exact_snk_en_binding=lambda cur, item: "2" * 64,
        _write_snk_en_asset=lambda image: (Path("x"), "data/runtime/x.webp"),
        _checkpoint_snk_en_item=lambda cur, **kw: 5,
    ):
        CC._persist_prepared_snk_en(
            conn.cursor(), item={"variantId": 7, "externalId": "123"},
            prepared=_snk_prepared(SHA_OLD), mode="incr",
            started_at=NOW, completed_at=NOW,
        )
    return conn


def run_snk_checks() -> None:
    control = run_snk(registry_hit=False, current=None)
    check("SNK 對照：未 reject 嘅新圖照寫 QC",
          len(writes(control.pending, "INSERT INTO market_image_qc")) == 1)
    check("SNK 對照：未 reject 嘅新圖照插 acceptance",
          len(writes(control.pending, "INSERT INTO market_canonical_image_acceptance")) == 1)
    check("SNK 對照：freeze 係 accepted",
          [p[6] for _, p in writes(control.pending, "INSERT INTO operator_binding_freeze")] == ["accepted"])

    hit = run_snk(registry_hit=True, current="snk_other")
    check("SNK：registry sha 唔再寫 QC（唔會變返最新 public_allowed=1）",
          not writes(hit.pending, "INSERT INTO market_image_qc"))
    check("SNK：registry sha 唔插 acceptance（唔會 supersede 替換圖）",
          not writes(hit.pending, "INSERT INTO market_canonical_image_acceptance"))
    check("SNK：registry sha 唔郁 freeze",
          not writes(hit.pending, "INSERT INTO operator_binding_freeze"))
    check("SNK：registry sha 照寫 asset／lineage（照收貨）",
          bool(writes(hit.pending, "INSERT INTO market_image_asset"))
          and bool(writes(hit.pending, "INSERT IGNORE INTO market_snk_en_storefront_lineage")))

    first = run_snk(registry_hit=True, current=None)
    check("SNK：registry sha 就算冇 canonical 都唔插 acceptance",
          not writes(first.pending, "INSERT INTO market_canonical_image_acceptance"))

    same = run_snk(registry_hit=True, current="snk_same")
    freezes = writes(same.pending, "INSERT INTO operator_binding_freeze")
    check("SNK：同 lineage 已係 canonical → freeze 講真話 'rejected'",
          [p[6] for _, p in freezes] == ["rejected"], str(freezes))
    check("SNK：同 lineage 都唔寫 QC", not writes(same.pending, "INSERT INTO market_image_qc"))

    human = run_snk(registry_hit=False, current="human")
    check("SNK：當前係人手 pin → 唔插 acceptance（推翻唔到人手）",
          not writes(human.pending, "INSERT INTO market_canonical_image_acceptance"))
    check("SNK：當前係人手 pin → 唔郁 freeze（排唔到人手前面）",
          not writes(human.pending, "INSERT INTO operator_binding_freeze"))


# ---------------------------------------------------------------- B. PC lane


def run_pc(accepted_by: str) -> tuple[FakeConn, Exception | None]:
    def respond(sql: str, params: Any) -> tuple[Any, int, int]:
        if sql.startswith("SELECT id FROM market_image_asset"):
            return {"id": 5}, 0, 1
        if "FROM market_canonical_image_acceptance ca" in sql:
            return {"id": 88, "lineage_sha256": "9" * 64, "accepted_by": accepted_by}, 0, 1
        if sql.startswith("INSERT INTO market_canonical_image_acceptance"):
            return None, 77, 1
        return None, 0, 1

    conn = FakeConn(respond)
    raised: Exception | None = None
    with patched(R, _write_pc_image_asset=lambda processed: "data/runtime/x.webp"):
        try:
            R._persist_pc_product_image(
                conn.cursor(), variant_id=7, pid="555",
                image_url="https://storage.googleapis.com/images.pricecharting.com/q/1600.jpg",
                page_sha="1" * 64, canonical_url="https://www.pricecharting.com/game/x",
                downloaded_sha="2" * 64, identity_evidence="3" * 64,
                processed={"contentSha256": SHA_NEW, "width": 429, "height": 600,
                           "transformSha256": "4" * 64, "qcVersion": "pc-product-v1"},
                observed_at=NOW, completed_at=NOW,
            )
        except Exception as exc:  # noqa: BLE001
            raised = exc
    return conn, raised


def run_pc_checks() -> None:
    control, raised = run_pc("rebuild_036:image_bind")
    check("PC 對照：自己嘅 acceptance 照 supersede", raised is None
          and len(writes(control.pending, "INSERT INTO market_canonical_image_acceptance")) == 1,
          str(raised))
    human, raised = run_pc(R.HUMAN_IMAGE_ACCEPTED_BY)
    check("PC：當前係人手 pin → 拋（stage_image_bind rollback 成張卡）", raised is not None)
    check("PC：當前係人手 pin → 唔插 acceptance",
          not writes(human.pending, "INSERT INTO market_canonical_image_acceptance"))
    check("PC：當前係人手 pin → 唔寫 freeze",
          not writes(human.pending, "INSERT INTO operator_binding_freeze"))
    check("人手 accepted_by 同 2026-08-11 嗰張 pin 一樣係 'human'",
          R.HUMAN_IMAGE_ACCEPTED_BY == "human" == PIN.HUMAN_IMAGE_ACCEPTED_BY)


# -------------------------------------------------------------- C. pin tool


def _png(path: Path, color: tuple[int, int, int]) -> None:
    from PIL import Image

    Image.new("RGB", (300, 420), color).save(path, format="PNG")


class PinWorld:
    """Just enough DB for the tool: FE state flips to the new sha once the
    acceptance insert is in the (committed or pending) statements."""

    def __init__(self, *, published: str | None, old_asset: bool = True,
                 rejected: set[str] | None = None, fe_follows_write: bool = True,
                 extra_accepted_freeze: bool = False) -> None:
        self.published = published
        self.old_asset = old_asset
        self.rejected = set(rejected or ())
        self.fe_follows_write = fe_follows_write
        self.extra_accepted_freeze = extra_accepted_freeze
        self.new_sha: str | None = None
        self.leases: list[str] = []
        self.conn = FakeConn(self.respond)

    def written(self) -> bool:
        return bool(writes(self.conn.pending + self.conn.committed,
                           "INSERT INTO market_canonical_image_acceptance"))

    def respond(self, sql: str, params: Any) -> tuple[Any, int, int]:
        if not sql.upper().startswith(("SELECT", "SET", "START")) and not self.leases:
            self.conn.events.append("WRITE_OUTSIDE_LEASE")
        if sql.startswith("SELECT id FROM market_image_asset"):
            if params[1] == self.new_sha and writes(self.conn.pending, "INSERT INTO market_image_asset"):
                return {"id": 900}, 0, 1
            return ({"id": 500} if self.old_asset else None), 0, 1
        if sql.startswith("SELECT ca.id, ca.lineage_sha256, ca.accepted_by"):
            return {"id": 300, "lineage_sha256": "9" * 64,
                    "accepted_by": CC.SNK_EN_ACCEPTED_BY}, 0, 1
        if sql.startswith("SELECT source_code FROM operator_binding_freeze"):
            rows = [{"source_code": "human"}]
            if self.extra_accepted_freeze:
                rows.append({"source_code": "snkrdunk_en"})
            return rows, 0, len(rows)
        if sql.startswith("INSERT INTO market_canonical_image_acceptance"):
            return None, 700, 1
        return None, 0, 1

    def fe_state(self, conn: Any, ids: list[int]) -> dict[int, dict[str, Any]]:
        sha = self.new_sha if (self.fe_follows_write and self.written()) else self.published
        return {ids[0]: {"via": "projection", "contentSha256": sha}} if sha else {}

    def rejections(self, conn: Any, ids: list[int]) -> dict[int, set[str]]:
        return {ids[0]: set(self.rejected)}

    @contextlib.contextmanager
    def lease(self):
        self.leases.append("held")
        self.conn.events.append("lease-enter")
        try:
            yield
        finally:
            self.leases.pop()
            self.conn.events.append("lease-exit")


def run_pin(world: PinWorld, tmp: Path, *, apply: bool, row_patch: dict | None = None,
            same_image_as_old: bool = False) -> tuple[int, dict]:
    image = tmp / "front.png"
    _png(image, (200, 30, 30))
    rendered_sha = PIN.render_replacement(image.read_bytes())["contentSha256"]
    world.new_sha = rendered_sha
    row = {
        "variantId": 7,
        "oldSha256": rendered_sha if same_image_as_old else SHA_OLD,
        "newImagePath": "front.png",
        "sourceUrl": "https://example.invalid/front",
        "sourceLineage": "test lineage",
        "rejectionReason": "sample_watermark",
        "reviewNote": "SAMPLE watermark across the art",
    }
    row.update(row_patch or {})
    manifest = tmp / "manifest.json"
    manifest.write_text(json.dumps([row]), encoding="utf-8")
    trio_calls: list[str] = []
    out = io.StringIO()
    with patched(R, _fe_image_state=world.fe_state, _image_rejections=world.rejections), \
            patched(PIN, db=lambda: world.conn, load_env=lambda: None,
                    _chain_leases=world.lease, RECEIPT_DIR=tmp / "receipts",
                    _write_public_trio=lambda sha, content: trio_calls.append(sha)), \
            contextlib.redirect_stdout(out):
        code = PIN.main([str(manifest)] + (["--apply"] if apply else []))
    world.conn.events.append(f"trio:{len(trio_calls)}")
    text = out.getvalue()
    report = json.loads(text[text.index("{\n"):]) if "{\n" in text else {}
    return code, report


def all_writes(world: PinWorld) -> list[tuple[str, Any]]:
    return [
        item for item in world.conn.log
        if not item[0].upper().startswith(("SELECT", "SET", "START"))
    ]


def run_pin_checks() -> None:
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)

        # --- 冇替換圖就唔跑
        manifest = tmp / "missing.json"
        manifest.write_text(json.dumps([{
            "variantId": 7, "oldSha256": SHA_OLD, "newImagePath": "nope.png",
            "sourceUrl": "u", "sourceLineage": "l", "rejectionReason": "overlay",
            "reviewNote": "n",
        }]), encoding="utf-8")
        opened: list[str] = []
        with patched(PIN, db=lambda: opened.append("db"), load_env=lambda: None), \
                contextlib.redirect_stdout(io.StringIO()):
            code = PIN.main([str(manifest), "--apply"])
        check("冇替換圖：拒絕（exit 2）", code == 2, str(code))
        check("冇替換圖：連 DB 都唔開", not opened)
        # low_resolution：啱卡但細圖，係真實理由（v501），唔使再借 wrong_printing 做假 label
        _png(tmp / "ok.png", (1, 2, 3))
        lowres = dict(json.loads(manifest.read_text(encoding="utf-8"))[0],
                      newImagePath="ok.png", rejectionReason="low_resolution")
        (tmp / "lowres.json").write_text(json.dumps([lowres]), encoding="utf-8")
        try:
            rows = PIN.load_manifest(tmp / "lowres.json")
            check("manifest：low_resolution → 接受",
                  rows[0]["rejectionReason"] == "low_resolution", str(rows))
        except PIN.ManifestError as exc:
            check("manifest：low_resolution → 接受", False, str(exc))
        for label, patch in {
            "reason 唔喺清單之內": {"rejectionReason": "ugly"},
            "oldSha256 唔係 64 hex": {"oldSha256": "ABC"},
            "冇 reviewNote": {"reviewNote": ""},
        }.items():
            try:
                bad = dict(json.loads(manifest.read_text(encoding="utf-8"))[0])
                _png(tmp / "ok.png", (1, 2, 3))
                bad.update({"newImagePath": "ok.png"}, **patch)
                (tmp / "bad.json").write_text(json.dumps([bad]), encoding="utf-8")
                PIN.load_manifest(tmp / "bad.json")
                check(f"manifest：{label} → 拒絕", False, "accepted")
            except PIN.ManifestError:
                check(f"manifest：{label} → 拒絕", True)
        undecodable = tmp / "broken.png"
        undecodable.write_bytes(b"not an image")
        bad = dict(json.loads(manifest.read_text(encoding="utf-8"))[0], newImagePath="broken.png")
        (tmp / "broken.json").write_text(json.dumps([bad]), encoding="utf-8")
        with patched(PIN, db=lambda: opened.append("db"), load_env=lambda: None), \
                contextlib.redirect_stdout(io.StringIO()):
            code = PIN.main([str(tmp / "broken.json")])
        check("替換圖 decode 唔到：拒絕（exit 2）、唔開 DB", code == 2 and not opened, str(code))

        # --- dry-run：READ ONLY，一行都唔寫
        world = PinWorld(published=SHA_OLD)
        code, report = run_pin(world, tmp, apply=False)
        check("dry-run：exit 0、計劃係 replace", code == 0
              and [c["action"] for c in report.get("cards", [])] == ["replace"], str(report))
        check("dry-run：開 READ ONLY transaction",
              [s for s, _ in world.conn.log[:2]] == ["SET SESSION TRANSACTION READ ONLY",
                                                     "START TRANSACTION READ ONLY"])
        check("dry-run：一句寫都冇", not all_writes(world), str(all_writes(world)[:1]))
        check("dry-run：唔攞 lease、唔寫 webp、唔 commit",
              "lease-enter" not in world.conn.events and "trio:0" in world.conn.events
              and "commit" not in world.conn.events, str(world.conn.events))

        # --- apply：happy path
        world = PinWorld(published=SHA_OLD)
        code, report = run_pin(world, tmp, apply=True)
        committed = world.conn.committed
        check("apply：exit 0、replaced", code == 0
              and [c["action"] for c in report.get("cards", [])] == ["replaced"], str(report))
        check("apply：冇任何一句喺 lease 外面寫", "WRITE_OUTSIDE_LEASE" not in world.conn.events)
        events = world.conn.events
        check("apply：寫之前先攞 lease", "lease-enter" in events and "commit" in events
              and events.index("lease-enter") < events.index("commit"), str(events))
        registry = writes(committed, "INSERT INTO market_image_rejection_registry")
        check("apply：舊 sha 入 registry，reason 用現有字彙",
              len(registry) == 1 and registry[0][1][1] == SHA_OLD
              and registry[0][1][3] == "human_review_rejected:sample_watermark"
              and registry[0][1][2] == 500, str(registry))
        qc = writes(committed, "INSERT INTO market_image_qc")
        check("apply：新 asset QC = human_or_vision_confirmed / human-pin-v1",
              len(qc) == 1 and "'human_or_vision_confirmed',1,1,1,1,1" in qc[0][0]
              and qc[0][1][0] == 900 and qc[0][1][2] == "human-pin-v1", str(qc))
        acceptance = writes(committed, "INSERT INTO market_canonical_image_acceptance")
        check("apply：acceptance 係 'human'、supersede 當前 head 300",
              len(acceptance) == 1 and acceptance[0][1][7] == "human"
              and acceptance[0][1][9] == 300 and acceptance[0][1][1] == 900
              and acceptance[0][1][3] == acceptance[0][1][5], str(acceptance))
        check("apply：fallback path 冇 'snkrdunk'（view fallback 分支收到）",
              "snkrdunk" not in acceptance[0][1][2].lower() if acceptance else False)
        demote = writes(committed, "UPDATE operator_binding_freeze")
        freeze = writes(committed, "INSERT INTO operator_binding_freeze")
        check("apply：其他 accepted freeze 只改 status（冇 DELETE）",
              len(demote) == 1 and "SET acceptance_status='rejected'" in demote[0][0]
              and not writes(world.conn.log, "DELETE"), str(demote))
        check("apply：freeze source 'human' 指住新 acceptance 700",
              len(freeze) == 1 and freeze[0][1][1] == "human" and freeze[0][1][4] == 700
              and freeze[0][1][3] == world.new_sha, str(freeze))
        check("apply：寫咗 public webp 三件套", "trio:1" in world.conn.events)
        receipts = list((tmp / "receipts").glob("*.json"))
        check("apply：receipt 保留 lineage 原文（sourceUrl／sourceLineage）",
              len(receipts) == 1 and "https://example.invalid/front"
              in receipts[0].read_text(encoding="utf-8"), str(receipts))

        # --- apply：FE 讀返唔係新 sha → rollback
        world = PinWorld(published=SHA_OLD, fe_follows_write=False)
        code, report = run_pin(world, tmp, apply=True)
        check("apply：FE 讀返唔係新 sha → exit 1", code == 1, str(report))
        check("apply：FE 讀返唔係新 sha → rollback、冇 commit",
              not world.conn.committed and "commit" not in world.conn.events,
              str(world.conn.events))
        world = PinWorld(published=SHA_OLD, extra_accepted_freeze=True)
        code, _ = run_pin(world, tmp, apply=True)
        check("apply：仲有第二行 accepted freeze → rollback", code == 1
              and not world.conn.committed, str(world.conn.events))

        # --- 拒絕：一行都唔寫
        refusals = {
            "stale manifest（FE 而家顯示另一張）": (PinWorld(published="c" * 64), {}, False),
            "新圖本身已經 reject 過": (None, {}, False),
            "新舊一樣": (PinWorld(published=SHA_OLD), {}, True),
            "舊 sha 唔係呢張卡嘅 asset": (PinWorld(published=SHA_OLD, old_asset=False), {}, False),
        }
        for label, (world, patch, same) in refusals.items():
            if world is None:
                world = PinWorld(published=SHA_OLD)
                image = tmp / "front.png"
                _png(image, (200, 30, 30))
                world.rejected = {PIN.render_replacement(image.read_bytes())["contentSha256"]}
            code, report = run_pin(world, tmp, apply=True, row_patch=patch, same_image_as_old=same)
            check(f"拒絕「{label}」：exit 1、零寫入", code == 1 and not all_writes(world),
                  f"code={code} writes={all_writes(world)[:1]}")

        # --- 已經換咗：skip，唔再寫
        world = PinWorld(published=None)
        image = tmp / "front.png"
        _png(image, (200, 30, 30))
        world.published = PIN.render_replacement(image.read_bytes())["contentSha256"]
        code, report = run_pin(world, tmp, apply=True)
        check("已經換咗：skip、零寫入", code == 0 and not all_writes(world)
              and [c["action"] for c in report.get("cards", [])] == ["skip"], str(report))

        # --- 卡而家冇圖（published None）都可以換
        world = PinWorld(published=None)
        code, report = run_pin(world, tmp, apply=True)
        check("卡而家冇圖：照換", code == 0 and bool(world.conn.committed), str(report))


def run_lease_wiring_check() -> None:
    import operator_control

    order: list[str] = []

    @contextlib.contextmanager
    def fake_e2e(owner: str):
        order.append(f"e2e:{owner}")
        yield

    @contextlib.contextmanager
    def fake_writer(*args: Any, **kwargs: Any):
        order.append("db-writer")
        yield

    with patched(operator_control, operator_e2e_lease=fake_e2e), \
            patched(CC, _db_writer_lease=fake_writer):
        with PIN._chain_leases():
            order.append("body")
    check("pin tool 用 chain 同一對 lease、同一個次序",
          order == [f"e2e:{PIN.LEASE_OWNER}", "db-writer", "body"], str(order))


def main() -> int:
    run_snk_checks()
    run_pc_checks()
    run_pin_checks()
    run_lease_wiring_check()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("human image replacement 契約成立")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
