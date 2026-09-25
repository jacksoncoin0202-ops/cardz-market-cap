#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""image_lane_policy：market_canonical_image_acceptance 唯一 writer，全部 fake cursor，唔使連 DB。

背景：One Piece 卡成日出錯圖——SNKRDUNK listing 相有 "SAMPLE" 水印／唔係卡正面，
被 auto lane 收咗，仲蓋過人手揀／人手 approve 嘅圖。四條 INSERT 路（SNK EN
collector、rebuild_036 PC image-bind、人手 pin tool、new_era_db_tidy 026）各有
各嘅規矩。而家規矩只有 pipelines/image_lane_policy.py 一個執行點；呢個檔守：

  R1 人手 head（accepted_by='human'）auto lane 推翻唔到；人手 lane 可以。
  R2 head 嘅 (variant, sha) 喺 market_image_review_approval → auto lane 推翻唔到。
  R3 registry 入面嘅 (variant, sha) 邊條 lane 都唔准收（人手都唔得），而且係 pair，
     唔係 sha：dae7a026ec6c… v260 拒、v661 合法；739a706860ba… v1039 拒、v652 合法。
  R4 One Piece：auto SNK lane 只可以填冇 head 嘅卡，唔准 supersede；冇 tcg_code 當 OP。
  R5 hold 係 typed 結果，唔拋；caller 要先拋（raise_on_hold）。
  R6/R7 同 lineage 已係 head → 唔插；兩個 INSERT 形狀嘅欄位／參數同舊 lane 一樣。
  R9 靜態：pipelines/ 同 scripts/ 除咗 image_lane_policy.py 之外冇任何 raw INSERT。
  R10 接線：SNK EN、PC、new_era 三個 caller 真係行 policy（pin tool 由
      scripts/test_pin_human_card_image.py 守）。

每條規則都有 plant：將條規則拆走（monkeypatch），個 check 一定要變紅，證明佢會 fire。
"""
from __future__ import annotations

import contextlib
import hashlib
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as CC  # noqa: E402
import image_lane_policy as P  # noqa: E402
import rebuild_036 as R  # noqa: E402

FAILED: list[str] = []
NOW = datetime(2026, 9, 26, 1, 0, 0)
# Built by concatenation so this file's own text never matches the scanner.
TABLE = "market_canonical" + "_image_acceptance"
DAE7 = "dae7a026ec6c3dc13c58235b53aaaa13890a2326d270da72080d7b546324b67b"
S739 = "739a706860ba79a87f68c18c4fd69d50a23f9a3f7dd0f1aeeb730846cf6284f8"
SHA_A = "a" * 64
SHA_B = "b" * 64
LIN = "c" * 64
EVI = "d" * 64
PC_ACTOR = "rebuild_036:image_bind"


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


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


def rule(label: str, ok_fn: Callable[[], bool],
         plants: dict[str, dict[str, Any]]) -> None:
    """ok_fn must be True as written and False under every plant."""

    def safe() -> bool:
        try:
            return bool(ok_fn())
        except Exception as exc:  # noqa: BLE001 - an unexpected raise is a red
            print(f"     ({label}: {type(exc).__name__}: {exc})")
            return False

    check(label, safe())
    for plant_label, attrs in plants.items():
        with patched(P, **attrs):
            fired = not safe()
        check(f"{label} ⟵ plant「{plant_label}」會紅", fired)


# ------------------------------------------------------------- policy fakes


class World:
    """Just enough of the five tables the policy reads, answered by SQL shape."""

    def __init__(self, *, assets: dict | None = None, registry: set | None = None,
                 approvals: set | None = None, heads: dict | None = None,
                 tcg: dict | None = None) -> None:
        self.assets = dict(assets or {})        # (variant, sha) -> asset id
        self.registry = set(registry or ())     # {(variant, sha)}
        self.approvals = set(approvals or ())   # {(variant, sha)}
        self.heads = dict(heads or {})          # variant -> head row (+ content_sha256)
        self.tcg = dict(tcg or {})              # variant -> tcg_code
        self.log: list[tuple[str, Any]] = []
        self.next_id = 1000
        self.lastrowid_override: int | None = None
        self.lineage_rows: dict[str, int] = {}

    def inserts(self) -> list[tuple[str, Any]]:
        return [item for item in self.log if item[0].upper().startswith("INSERT")]

    def respond(self, sql: str, params: Any) -> tuple[Any, int, int]:
        if sql.startswith("SELECT id FROM market_image_asset"):
            found = self.assets.get((params[0], params[1]))
            return ({"id": found} if found else None), 0, 1
        if "FROM market_image_rejection_registry" in sql:
            return ({"1": 1} if (params[0], params[1]) in self.registry else None), 0, 1
        if "FROM market_image_review_approval" in sql:
            for variant, head in self.heads.items():
                if head["id"] == params[0]:
                    hit = (variant, head.get("content_sha256")) in self.approvals
                    return ({"1": 1} if hit else None), 0, 1
            return None, 0, 0
        if "FROM catalog_printing_identity" in sql:
            code = self.tcg.get(params[0])
            return ({"tcg_code": code} if code else None), 0, 1
        if "FROM market_canonical_image_acceptance ca" in sql:
            head = self.heads.get(params[0])
            return (dict(head) if head else None), 0, 1
        if sql.startswith("SELECT id FROM market_canonical_image_acceptance WHERE lineage_sha256"):
            found = self.lineage_rows.get(params[0])
            return ({"id": found} if found else None), 0, 1
        if sql.startswith("INSERT INTO " + TABLE):
            if self.lastrowid_override is not None:
                return None, self.lastrowid_override, 0
            self.next_id += 1
            return None, self.next_id, 1
        raise AssertionError(f"policy ran unexpected SQL: {sql[:120]}")

    def cursor(self) -> "FakeCursor":
        return FakeCursor(self.respond, self.log)


class FakeCursor:
    def __init__(self, respond: Callable[[str, Any], tuple[Any, int, int]],
                 log: list[tuple[str, Any]]) -> None:
        self.respond = respond
        self.log = log
        self._result: Any = None
        self.lastrowid = 0
        self.rowcount = 0

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: Any = None) -> None:
        compact = " ".join(sql.split())
        self.log.append((compact, params))
        self._result, self.lastrowid, self.rowcount = self.respond(compact, params)

    def fetchone(self) -> Any:
        if isinstance(self._result, list):
            return self._result[0] if self._result else None
        return self._result

    def fetchall(self) -> list[Any]:
        if self._result is None:
            return []
        return self._result if isinstance(self._result, list) else [self._result]


PC_LANE = {
    "accepted_by": PC_ACTOR,
    # 'snk' inside a random PC image id must not make this an SNK lane.
    "fallback_source_path": "pricecharting:9:https://storage.googleapis.com/"
                            "images.pricecharting.com/aSnKq7/1600.jpg",
    "fallback_source_version_sha256": LIN,
    "fallback_source_observed_at": NOW,
}
SNK_LANE = {"accepted_by": CC.SNK_EN_ACCEPTED_BY, "storefront_lineage_id": 21}


def human_lane() -> dict[str, Any]:
    return {
        "accepted_by": P.HUMAN_IMAGE_ACCEPTED_BY,
        "fallback_source_path": "human-designated:run:7",
        "fallback_source_version_sha256": LIN,
        "fallback_source_observed_at": NOW,
    }


def accept(world: World, variant: int, sha: str, lane: dict[str, Any], **extra: Any):
    return P.accept_canonical_image(
        world.cursor(), variant_id=variant,
        image_asset_id=world.assets[(variant, sha)], content_sha256=sha,
        lineage_sha256=LIN, evidence_sha256=EVI, accepted_at=NOW,
        **lane, **extra,
    )


def held(decision: Any, reason: str) -> bool:
    return (decision.held and decision.reason == reason
            and decision.acceptance_id is None and not decision.inserted)


def head(ident: int, accepted_by: str, sha: str = SHA_B, lineage: str = "9" * 64) -> dict:
    return {"id": ident, "lineage_sha256": lineage, "accepted_by": accepted_by,
            "content_sha256": sha}


# ------------------------------------------------------------------- rules


def r1_human_head() -> bool:
    world = World(assets={(7, SHA_A): 11}, heads={7: head(50, "human")},
                  tcg={7: "pokemon"})
    pc = accept(world, 7, SHA_A, PC_LANE)
    snk = accept(world, 7, SHA_A, SNK_LANE)
    no_insert = not world.inserts()
    person = accept(world, 7, SHA_A, human_lane())
    return (held(pc, P.HELD_HEAD_IS_HUMAN) and held(snk, P.HELD_HEAD_IS_HUMAN)
            and no_insert and person.status == "accepted"
            and world.inserts()[-1][1][-1] == 50)


def r2_approved_head() -> bool:
    world = World(assets={(7, SHA_A): 11, (8, SHA_A): 12},
                  heads={7: head(50, PC_ACTOR, SHA_B), 8: head(60, PC_ACTOR, SHA_B)},
                  approvals={(7, SHA_B), (9, SHA_B)}, tcg={7: "pokemon", 8: "pokemon"})
    pc = accept(world, 7, SHA_A, PC_LANE)
    snk = accept(world, 7, SHA_A, SNK_LANE)
    no_insert = not world.inserts()
    # Same sha approved only for another variant (9): v8's head is not approved.
    other = accept(world, 8, SHA_A, PC_LANE)
    person = accept(world, 7, SHA_A, human_lane())
    return (held(pc, P.HELD_HEAD_REVIEW_APPROVED)
            and held(snk, P.HELD_HEAD_REVIEW_APPROVED) and no_insert
            and other.status == "accepted" and person.status == "accepted")


REGISTRY = {(260, DAE7), (1039, S739)}


def r3_registry_pair() -> bool:
    world = World(assets={(260, DAE7): 1, (661, DAE7): 2, (1039, S739): 3, (652, S739): 4},
                  registry=set(REGISTRY), tcg={260: "pokemon", 661: "pokemon",
                                               1039: "pokemon", 652: "pokemon"})
    results = {
        "v260 pc": held(accept(world, 260, DAE7, PC_LANE), P.HELD_REJECTED_CONTENT),
        "v260 human": held(accept(world, 260, DAE7, human_lane()), P.HELD_REJECTED_CONTENT),
        "v1039 snk": held(accept(world, 1039, S739, SNK_LANE), P.HELD_REJECTED_CONTENT),
        "v1039 human": held(accept(world, 1039, S739, human_lane()), P.HELD_REJECTED_CONTENT),
    }
    no_insert = not world.inserts()
    results["v661 pc"] = accept(world, 661, DAE7, PC_LANE).status == "accepted"
    results["v652 human"] = accept(world, 652, S739, human_lane()).status == "accepted"
    if not all(results.values()):
        print(f"     (r3 {results})")
    return all(results.values()) and no_insert and len(world.inserts()) == 2


def r4_one_piece_snk() -> bool:
    world = World(
        assets={(v, SHA_A): 100 + v for v in (7, 8, 9, 10, 11)},
        heads={7: head(70, CC.SNK_EN_ACCEPTED_BY), 9: head(90, CC.SNK_EN_ACCEPTED_BY),
               10: head(100, PC_ACTOR), 11: head(110, CC.SNK_EN_ACCEPTED_BY)},
        tcg={7: "one-piece", 8: "one-piece", 9: "pokemon", 10: "one-piece"},
    )
    results = {
        "OP snk over own head held": held(accept(world, 7, SHA_A, SNK_LANE), P.HELD_ONE_PIECE_SNK),
        "OP snk-path lane held": held(accept(world, 7, SHA_A, {
            "accepted_by": "some_future_lane",
            "fallback_source_path": "snkrdunk-en:123:https://cdn.snkrdunk.com/x.jpg",
            "fallback_source_version_sha256": LIN, "fallback_source_observed_at": NOW,
        }), P.HELD_ONE_PIECE_SNK),
        "no tcg_code = OP (fail-closed)": held(accept(world, 11, SHA_A, SNK_LANE), P.HELD_ONE_PIECE_SNK),
    }
    no_insert = not world.inserts()
    fill = accept(world, 8, SHA_A, SNK_LANE)
    results["OP snk fills empty variant"] = fill.status == "accepted" and fill.head_acceptance_id is None
    poke = accept(world, 9, SHA_A, SNK_LANE)
    results["pokemon snk supersedes own head"] = poke.status == "accepted" and poke.head_acceptance_id == 90
    pc = accept(world, 10, SHA_A, PC_LANE)
    results["OP pc lane not an SNK lane"] = pc.status == "accepted" and pc.head_acceptance_id == 100
    if not all(results.values()):
        print(f"     (r4 {results})")
    return all(results.values()) and no_insert


def r5_hold_is_typed() -> bool:
    world = World(assets={(7, SHA_A): 11}, heads={7: head(50, "human")})
    quiet = accept(world, 7, SHA_A, PC_LANE)
    try:
        accept(world, 7, SHA_A, PC_LANE, raise_on_hold=True)
        loud = None
    except P.ImageLaneHeld as exc:
        loud = exc
    return (isinstance(quiet, P.ImageLaneDecision) and quiet.held
            and loud is not None and isinstance(loud, RuntimeError)
            and loud.decision.reason == P.HELD_HEAD_IS_HUMAN
            and loud.decision.head_acceptance_id == 50 and not world.inserts())


def r6_same_lineage_is_current() -> bool:
    world = World(assets={(7, SHA_A): 11, (8, SHA_A): 12},
                  heads={7: head(50, CC.SNK_EN_ACCEPTED_BY, SHA_A, lineage=LIN),
                         8: head(60, CC.SNK_EN_ACCEPTED_BY, SHA_A, lineage=LIN)},
                  registry={(8, SHA_A)}, tcg={7: "one-piece"})
    same = accept(world, 7, SHA_A, SNK_LANE)
    rejected = accept(world, 8, SHA_A, SNK_LANE)
    return (same.status == "current" and same.acceptance_id == 50 and not same.inserted
            and held(rejected, P.HELD_REJECTED_CONTENT) and not world.inserts())


def r7_insert_shapes() -> bool:
    world = World(assets={(7, SHA_A): 11, (8, SHA_A): 12}, heads={7: head(50, PC_ACTOR)},
                  tcg={7: "pokemon", 8: "pokemon"})
    fallback = accept(world, 7, SHA_A, PC_LANE)
    storefront = accept(world, 8, SHA_A, SNK_LANE)
    (fb_sql, fb), (sf_sql, sf) = world.inserts()
    ok = (
        "VALUES (%s,NULL,%s,%s,%s,%s,%s,%s,%s,%s,%s)" in fb_sql
        # legacy pin / PC layout: asset@1, version@3 == lineage@5, actor@7, supersedes@9
        and fb == (7, 11, PC_LANE["fallback_source_path"], LIN, NOW, LIN, EVI,
                   PC_ACTOR, NOW, 50)
        and "(variant_id,storefront_lineage_id,image_asset_id,lineage_sha256," in sf_sql
        # legacy SNK EN layout
        and sf == (8, 21, 12, LIN, EVI, CC.SNK_EN_ACCEPTED_BY, NOW, None)
        and fallback.inserted and storefront.inserted
        and "ON DUPLICATE" not in fb_sql + sf_sql
    )
    # new_era reuse: ON DUPLICATE KEY, id recovered by lineage when lastrowid is 0.
    reuse = World(assets={(7, SHA_A): 11}, tcg={7: "pokemon"})
    reuse.lastrowid_override = 0
    reuse.lineage_rows[LIN] = 4242
    got = accept(reuse, 7, SHA_A, PC_LANE, reuse_existing_lineage=True)
    return (ok and got.acceptance_id == 4242 and not got.inserted
            and reuse.inserts()[0][0].endswith("ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)"))


def r8_contract_errors() -> bool:
    world = World(assets={(7, SHA_A): 11})
    outcomes = []
    for kwargs in (
        {"image_asset_id": 99},                 # asset is not the one holding that sha
        {"content_sha256": "ABC"},
        {"variant_id": 0},
    ):
        base = {"variant_id": 7, "image_asset_id": 11, "content_sha256": SHA_A}
        base.update(kwargs)
        try:
            P.accept_canonical_image(world.cursor(), lineage_sha256=LIN, evidence_sha256=EVI,
                                     accepted_at=NOW, **base, **PC_LANE)
            outcomes.append(False)
        except P.ImageLanePolicyError:
            outcomes.append(True)
    return all(outcomes) and not world.inserts()


# -------------------------------------------------------------- R9 static

_J = (r"""(?:\s|--[^\n]*\n|\#[^\n]*\n|/\*.*?\*/"""
      r"""|["'](?:\s|\\|\+|\#[^\n]*\n)*[rRbBfFuU]{0,2}["'])""")
_KW = (rf"""\b(?:INSERT(?:{_J}+(?:LOW_PRIORITY|DELAYED|HIGH_PRIORITY|IGNORE))*"""
       rf"""|REPLACE(?:{_J}+(?:LOW_PRIORITY|DELAYED))*)(?:{_J}+INTO)?""")
_TABLE = r"""[`"]?(?:\w+[`"]?\.[`"]?)?""" + TABLE + r"""(?!\w)[`"]?"""
_CONT = r"""(?:\(|\b(?:VALUES?|SELECT|SET|WITH|TABLE|PARTITION)\b)"""
RAW_INSERT_RE = re.compile(rf"{_KW}{_J}+{_TABLE}{_J}*{_CONT}", re.IGNORECASE | re.DOTALL)
ALLOWED_WRITER = "pipelines/image_lane_policy.py"
SCAN_ROOTS = ("pipelines", "scripts")


def raw_insert_lines(text: str) -> list[int]:
    if TABLE not in text.lower():
        return []
    return [text.count("\n", 0, m.start()) + 1 for m in RAW_INSERT_RE.finditer(text)]


def scan_tree(root: Path) -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for base in SCAN_ROOTS:
        for path in sorted((root / base).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or "node_modules" in path.parts:
                continue
            blob = path.read_bytes()
            if b"\x00" in blob[:8192]:
                continue
            lines = raw_insert_lines(blob.decode("utf-8", errors="replace"))
            if lines:
                found[path.relative_to(root).as_posix()] = lines
    return found


CAUGHT = {
    "SNK EN triple-quoted": f'''cur.execute(
            """
            INSERT INTO {TABLE}
              (variant_id,storefront_lineage_id,image_asset_id,lineage_sha256)
            VALUES (%s,%s,%s,%s)
            """,''',
    "PC adjacent literals": f'''cursor.execute(
            "INSERT INTO {TABLE}"
            " (variant_id,storefront_lineage_id,image_asset_id,"''',
    "lower case + backticks + schema": f"insert into `cardz`.`{TABLE}` (variant_id) values (1)",
    "tabs/newlines + backticks": f"INSERT\n\tINTO\n\t`{TABLE}`\n\t(variant_id)",
    "INSERT IGNORE": f"INSERT IGNORE INTO {TABLE} (variant_id) VALUES (1)",
    "REPLACE INTO": f"REPLACE INTO {TABLE} (variant_id) VALUES (1)",
    "INTO omitted": f"INSERT {TABLE} SET variant_id=1",
    "INSERT ... SELECT": f"INSERT INTO {TABLE}\nSELECT * FROM x",
    "split between INTO and table": f'"INSERT INTO "\n    "{TABLE} (variant_id) VALUES (1)"',
    "comment between literals": f'"INSERT INTO {TABLE}"  # why\n    " (variant_id)"',
    "string + concatenation": f'"INSERT INTO {TABLE}" + " (variant_id)"',
    "shell mysql -e": f'mysql -e "INSERT INTO {TABLE}(variant_id) VALUES (1)"',
}
NOT_CAUGHT = {
    "fake-cursor needle": f'writes(control.pending, "INSERT INTO {TABLE}")',
    "startswith needle": f'if sql.startswith("INSERT INTO {TABLE}"):',
    "SELECT head": f"SELECT ca.id FROM {TABLE} ca WHERE ca.variant_id=%s",
    "other table": f"INSERT INTO {TABLE}_history (variant_id) VALUES (1)",
    "view join": f"INNER JOIN {TABLE} ca ON ca.id=f.canonical_image_acceptance_id",
}


def r9_static() -> None:
    offenders = {path: lines for path, lines in scan_tree(ROOT).items()
                 if path != ALLOWED_WRITER}
    check("R9 static：pipelines/ + scripts/ 除 image_lane_policy.py 外冇 raw INSERT",
          not offenders, str(offenders))
    own = raw_insert_lines((ROOT / ALLOWED_WRITER).read_text(encoding="utf-8"))
    check("R9 static 對照：scanner 認得 image_lane_policy.py 自己兩個 INSERT 形狀",
          len(own) == 2, str(own))
    for label, text in CAUGHT.items():
        check(f"R9 plant「{label}」會紅", bool(raw_insert_lines(text)))
    for label, text in NOT_CAUGHT.items():
        check(f"R9 唔誤報「{label}」", not raw_insert_lines(text))
    with tempfile.TemporaryDirectory() as raw_tmp:
        tmp = Path(raw_tmp)
        (tmp / "pipelines" / "sub").mkdir(parents=True)
        (tmp / "scripts").mkdir()
        (tmp / "pipelines" / "sub" / "rogue_lane.py").write_text(
            CAUGHT["PC adjacent literals"], encoding="utf-8")
        (tmp / "scripts" / "ok.py").write_text(NOT_CAUGHT["fake-cursor needle"], encoding="utf-8")
        planted = scan_tree(tmp)
        check("R9 plant：tree scan 捉到 pipelines/ 子目錄嘅 raw INSERT",
              list(planted) == ["pipelines/sub/rogue_lane.py"], str(planted))
    for name in ("collect_control.py", "rebuild_036.py", "pin_human_card_image.py",
                 "new_era_db_tidy.py"):
        text = (ROOT / "pipelines" / name).read_text(encoding="utf-8")
        check(f"R10 接線：pipelines/{name} call accept_canonical_image(",
              "accept_canonical_image(" in text)


# -------------------------------------------------------------- R10 wiring


def _snk_prepared(variant: int, content_sha: str) -> SimpleNamespace:
    image = SimpleNamespace(
        content=b"x", content_sha256=content_sha, mime_type="image/webp",
        width=429, height=600, transform={"t": 1}, transform_sha256="c" * 64,
        qc_version="snk-en-default-v1",
    )
    return SimpleNamespace(
        variant_id=variant, external_id="123", image=image,
        master_payload_sha256="d" * 64, downloaded_bytes_sha256="e" * 64,
        captured_at=NOW, default_image_url_sha256="f" * 64,
        default_image_url="https://example.invalid/i.png", source_observed_at=NOW,
        product_url="https://snkrdunk.com/en/trading-cards/123",
        product_page_payload_sha256="1" * 64, product_page_observed_at=NOW,
        identity_evidence_sha256="2" * 64,
    )


def run_snk_lane(tcg_code: str) -> tuple[list[tuple[str, Any]], list[str]]:
    """collect_control._persist_prepared_snk_en over its own (SNK-owned) head."""

    log: list[tuple[str, Any]] = []
    calls: list[str] = []

    def respond(sql: str, params: Any) -> tuple[Any, int, int]:
        if sql.startswith("SELECT id FROM market_image_asset"):
            return {"id": 11}, 0, 1
        if "FROM market_image_rejection_registry" in sql:
            return None, 0, 0
        if "FROM market_image_review_approval" in sql:
            return None, 0, 0
        if "FROM catalog_printing_identity" in sql:
            return {"tcg_code": tcg_code}, 0, 1
        if sql.startswith("SELECT id FROM market_snk_en_storefront_lineage"):
            return {"id": 21}, 0, 1
        if sql.startswith("SELECT id FROM market_snk_en_product_page_authority"):
            return {"id": 31}, 0, 1
        if "FROM market_canonical_image_acceptance ca" in sql:
            return {"id": 99, "lineage_sha256": "9" * 64,
                    "accepted_by": CC.SNK_EN_ACCEPTED_BY}, 0, 1
        if sql.startswith("INSERT INTO " + TABLE):
            return None, 41, 1
        return None, 0, 1

    real = P.accept_canonical_image

    def spy(*args: Any, **kwargs: Any):
        calls.append(kwargs["accepted_by"])
        return real(*args, **kwargs)

    with patched(CC, _assert_exact_snk_en_binding=lambda cur, item: "2" * 64,
                 _write_snk_en_asset=lambda image: (Path("x"), "data/runtime/x.webp"),
                 _checkpoint_snk_en_item=lambda cur, **kw: 5), \
            patched(P, accept_canonical_image=spy):
        CC._persist_prepared_snk_en(
            FakeCursor(respond, log), item={"variantId": 7, "externalId": "123"},
            prepared=_snk_prepared(7, SHA_A), mode="incr",
            started_at=NOW, completed_at=NOW,
        )
    return log, calls


def run_pc_lane(approved: bool) -> tuple[list[tuple[str, Any]], Exception | None]:
    log: list[tuple[str, Any]] = []

    def respond(sql: str, params: Any) -> tuple[Any, int, int]:
        if sql.startswith("SELECT id FROM market_image_asset"):
            return {"id": 5}, 0, 1
        if "FROM market_image_review_approval" in sql:
            return ({"1": 1} if approved else None), 0, 1
        if "FROM market_canonical_image_acceptance ca" in sql:
            return {"id": 88, "lineage_sha256": "9" * 64, "accepted_by": PC_ACTOR}, 0, 1
        if sql.startswith("INSERT INTO " + TABLE):
            return None, 77, 1
        return None, 0, 1

    raised: Exception | None = None
    with patched(R, _write_pc_image_asset=lambda processed: "data/runtime/x.webp"):
        try:
            R._persist_pc_product_image(
                FakeCursor(respond, log), variant_id=7, pid="555",
                image_url="https://storage.googleapis.com/images.pricecharting.com/q/1600.jpg",
                page_sha="1" * 64, canonical_url="https://www.pricecharting.com/game/x",
                downloaded_sha="2" * 64, identity_evidence="3" * 64,
                processed={"contentSha256": SHA_A, "width": 429, "height": 600,
                           "transformSha256": "4" * 64, "qcVersion": "pc-product-v1"},
                observed_at=NOW, completed_at=NOW,
            )
        except Exception as exc:  # noqa: BLE001
            raised = exc
    return log, raised


def run_new_era(human_head_variant: int | None) -> tuple[list[tuple[str, Any]], Any]:
    import new_era_db_tidy as N

    log: list[tuple[str, Any]] = []
    shas = {v: hashlib.sha256(str(v).encode()).hexdigest() for v in range(1, 763)}

    def respond(sql: str, params: Any) -> tuple[Any, int, int]:
        if "FROM market_universe_member" in sql:
            return [{"variant_id": v} for v in shas], 0, len(shas)
        if "FROM market_snk_en_storefront_lineage l" in sql:
            return [{"variant_id": v, "id": v, "processed_image_asset_id": 10000 + v,
                     "lineage_sha256": f"{v:064x}", "exact_item_id": str(v),
                     "processed_content_sha256": shas[v]} for v in shas], 0, len(shas)
        if sql.startswith("SELECT id FROM market_image_asset"):
            return {"id": 10000 + params[0]}, 0, 1
        if "FROM catalog_printing_identity" in sql:
            return {"tcg_code": "pokemon"}, 0, 1
        if "FROM market_canonical_image_acceptance ca" in sql:
            if params[0] == human_head_variant:
                return {"id": 1, "lineage_sha256": "f" * 64, "accepted_by": "human"}, 0, 1
            return None, 0, 0
        if sql.startswith("INSERT INTO " + TABLE):
            return None, 20000 + params[0], 1
        if "FROM market_image_rejection_registry" in sql or "FROM market_image_review_approval" in sql:
            return None, 0, 0
        return None, 0, 1

    try:
        return log, N.sync_026_canonical_image_acceptances(FakeCursor(respond, log))
    except Exception as exc:  # noqa: BLE001
        return log, exc


def r10_wiring() -> None:
    op_log, op_calls = run_snk_lane("one-piece")
    check("R10 SNK EN lane 行 policy（spy 見到 call）", op_calls == [CC.SNK_EN_ACCEPTED_BY], str(op_calls))
    check("R10 SNK EN lane：OP 卡自己個 head 都唔 supersede（冇 acceptance INSERT）",
          not [s for s, _ in op_log if s.startswith("INSERT INTO " + TABLE)])
    check("R10 SNK EN lane：OP hold → 唔郁 freeze",
          not [s for s, _ in op_log if s.startswith("INSERT INTO operator_binding_freeze")])
    poke_log, _ = run_snk_lane("pokemon")
    inserts = [p for s, p in poke_log if s.startswith("INSERT INTO " + TABLE)]
    freezes = [p for s, p in poke_log if s.startswith("INSERT INTO operator_binding_freeze")]
    check("R10 SNK EN 對照：Pokémon 照 supersede 自己 head 99、freeze accepted",
          len(inserts) == 1 and inserts[0][-1] == 99
          and [p[6] for p in freezes] == ["accepted"], f"{inserts} {freezes}")

    log, raised = run_pc_lane(approved=True)
    check("R10 PC lane：head 已 approve → ImageLaneHeld（stage_image_bind rollback 成張卡）",
          isinstance(raised, P.ImageLaneHeld)
          and raised.decision.reason == P.HELD_HEAD_REVIEW_APPROVED, repr(raised))
    check("R10 PC lane：approved head → 冇 acceptance、冇 freeze",
          not [s for s, _ in log if s.startswith(("INSERT INTO " + TABLE,
                                                  "INSERT INTO operator_binding_freeze"))])
    log, raised = run_pc_lane(approved=False)
    inserts = [p for s, p in log if s.startswith("INSERT INTO " + TABLE)]
    check("R10 PC 對照：未 approve → supersede head 88、actor 同舊一樣",
          raised is None and len(inserts) == 1 and inserts[0][-1] == 88
          and inserts[0][7] == PC_ACTOR, f"{raised!r} {inserts}")

    log, outcome = run_new_era(human_head_variant=5)
    check("R10 new_era 026：human head → ImageLaneHeld（成個 sync 唔完成）",
          isinstance(outcome, P.ImageLaneHeld)
          and outcome.decision.reason == P.HELD_HEAD_IS_HUMAN, repr(outcome))
    log, outcome = run_new_era(human_head_variant=None)
    inserts = [(s, p) for s, p in log if s.startswith("INSERT INTO " + TABLE)]
    check("R10 new_era 對照：762 張經 policy、storefront 形狀、ON DUPLICATE 保留",
          isinstance(outcome, dict) and outcome.get("accepted") == 762 and len(inserts) == 762
          and inserts[0][1][1] == 1 and inserts[0][0].endswith("id=LAST_INSERT_ID(id)"),
          f"{outcome!r} {len(inserts)}")


def main() -> int:
    rule("R1 人手 head：auto lane（PC／SNK）hold、人手 lane 照 supersede", r1_human_head,
         {"唔認 human": {"_is_human": lambda accepted_by: accepted_by == "__nobody__"}})
    rule("R2 approved head：auto lane hold；approval 係 pair 唔係 sha", r2_approved_head,
         {"唔睇 approval": {"_head_review_approved": lambda cursor, head_id: False}})
    rule("R3 registry pair：人手都唔准收；v661／v652 同 sha 照收", r3_registry_pair, {
        "淨係睇 sha": {"_rejected": lambda cursor, variant_id, sha:
                        any(sha == s for _, s in REGISTRY)},
        "唔睇 registry": {"_rejected": lambda cursor, variant_id, sha: False},
    })
    rule("R4 One Piece：SNK lane 只填空、唔 supersede；冇 tcg 當 OP", r4_one_piece_snk, {
        "唔睇 tcg": {"_tcg_code": lambda cursor, variant_id: "pokemon"},
        "唔認 SNK lane": {"_is_snk_lane": lambda **kwargs: False},
    })
    rule("R5 hold 係 typed、預設唔拋；raise_on_hold 先拋", r5_hold_is_typed,
         {"hold 變 accept": {"_is_human": lambda accepted_by: False}})
    rule("R6 同 lineage 已係 head → current、唔插；被拒就 hold", r6_same_lineage_is_current,
         {"唔睇 registry": {"_rejected": lambda cursor, variant_id, sha: False}})
    rule("R7 INSERT 形狀／參數同舊 lane 一樣；new_era ON DUPLICATE 保留", r7_insert_shapes,
         {"fallback 形狀走樣": {"INSERT_FALLBACK_SQL": P.INSERT_STOREFRONT_SQL}})
    rule("R8 asset／sha 對唔上、壞 input → ImageLanePolicyError", r8_contract_errors,
         {"唔對 asset": {"_asset_id": lambda cursor, variant_id, sha: 99}})
    r9_static()
    r10_wiring()
    print()
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("image lane policy 契約成立：唯一 writer，四條規則齊、plant 全部會紅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
