# -*- coding: utf-8 -*-
"""TAG Grading population 數據管道（逆向 API client + 全量 dump）。

逆向細節同認證機制：pipelines/TAG_POP_DATA.md
完整拆解報告：reverse-skill/work/tag-grading/TAG_GRADING_REVERSE.md

用法：
    python pipelines\tag_pop_data.py --dump --out data\tag\pops_pokemon.jsonl
    python pipelines\tag_pop_data.py --match-600 --pop data\tag\pops_pokemon.jsonl --out data\tag\tag_pop_600.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad

BASE = "https://api.taggrading.com"
SALT = "TZY0j76MKF1AA0QK0ppAGySAaCNgKG"
ENC_KEY = hashlib.sha256(b"K5ucGQIf7vigW9ITOXLak5MjSIxxsgixqj").digest()


def _decrypt(body: str) -> dict:
    iv_hex, ct_hex = body.split(":", 1)
    plain = unpad(
        AES.new(ENC_KEY, AES.MODE_CBC, bytes.fromhex(iv_hex)).decrypt(bytes.fromhex(ct_hex)), 16
    )
    return json.loads(plain)


class TagClient:
    """重放 my.taggrading.com 前端嘅簽名 + 解密。公開端點免登入。"""

    def __init__(self, delay: float = 0.15) -> None:
        self.s = requests.Session()
        # 無 Accept/Origin 會 403
        self.s.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://my.taggrading.com",
                "Referer": "https://my.taggrading.com/",
            }
        )
        self.delay = delay
        self._last = 0.0

    def get(self, path: str, key_extra=(), **params):
        # key_extra：path template 值（例如 cert 編號）都計入簽名
        vals = sorted(
            [str(v) for v in key_extra]
            + [str(v) for v in params.values() if v not in (None, "")]
        )
        key = hashlib.sha256(f"{SALT}:{','.join(vals)}".encode()).hexdigest()
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.s.get(BASE + path, params=params, headers={"x-tag-key": key}, timeout=30)
        self._last = time.monotonic()
        r.raise_for_status()
        return _decrypt(r.text)["data"]

    def get_paged(self, path: str, limit: int = 200, **params):
        page, out = 1, []
        while True:
            d = self.get(path, page=page, limit=limit, **params)
            items = d["items"] if isinstance(d, dict) else d
            out.extend(items)
            total = d.get("total", len(out)) if isinstance(d, dict) else len(out)
            if len(out) >= total or not items:
                return out
            page += 1

    # --- pop report 公開端點（2026-07-22 驗證） ---
    def pops_by_year(self, category="Pokémon"):
        return self.get_paged("/pops/year", categoryName=category)

    def pops_by_set(self, category, year):
        return self.get_paged("/pops/set", categoryName=category, year=year)

    def pops_by_card(self, category, year, brand_name, set_name):
        return self.get_paged(
            "/pops/card", category=category, year=year, brandName=brand_name, setName=set_name
        )

    def search(self, keyword):
        return self.get_paged("/pops", keyword=keyword)

    def card_rank(self, **params):
        return self.get_paged("/pops/card/rank", sort="tagGrade:desc", **params)

    def cert_detail(self, cert):
        return self.get(f"/graded-cards/public/detail/{cert}", key_extra=(cert,))

    def cert_score(self, cert):
        return self.get(f"/graded-cards/public/score/{cert}", key_extra=(cert,))


def dump(category: str, out_path: str, delay: float) -> None:
    """year → set → card 三層 crawl，JSONL 輸出，state file 斷點續跑。"""
    state_path = out_path + ".state"
    done = set()
    if os.path.exists(state_path):
        done = set(json.load(open(state_path, encoding="utf-8")))

    c = TagClient(delay=delay)
    years = c.pops_by_year(category)
    rows_new = 0
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "a", encoding="utf-8") as out:
        for y in years:
            year = y["cardYear"]
            for s in c.pops_by_set(category, year):
                key = f"{year}|{s['brandName']}|{s['cardSetName']}"
                if key in done:
                    continue
                cards = c.pops_by_card(category, year, s["brandName"], s["cardSetName"])
                for card in cards:
                    out.write(
                        json.dumps(
                            {
                                "category": category,
                                "year": year,
                                "brandName": s["brandName"],
                                "setName": s["cardSetName"],
                                **card,
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                out.flush()
                done.add(key)
                rows_new += len(cards)
            json.dump(sorted(done), open(state_path, "w", encoding="utf-8"))
        print(f"DONE. {rows_new} new card rows -> {out_path}")


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _row_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(
        str(row.get(field) or "")
        for field in ("category", "year", "brandName", "setName", "cardName", "cardNumber", "variation")
    )


def dump_fresh(category: str, out_path: str | Path, delay: float) -> dict[str, int]:
    """Build one complete daily TAG snapshot with resumable partial state.

    ``dump`` is retained for manual historical work.  The unattended job uses
    this function instead: a completed output is immutable, an interrupted run
    resumes from ``.partial.state``, and the final path is promoted only after
    a non-empty, de-duplicated crawl has completed.
    """

    destination = Path(out_path)
    if destination.is_file():
        rows = sum(1 for line in destination.open(encoding="utf-8") if line.strip())
        if rows < 1_000:
            raise RuntimeError(f"completed TAG snapshot is unexpectedly small: {rows}")
        return {"rows": rows, "totalGraded": 0, "replayed": 1}

    partial = destination.with_suffix(destination.suffix + ".partial")
    state_path = partial.with_suffix(partial.suffix + ".state")
    done: set[str] = set()
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state, list) or not all(isinstance(value, str) for value in state):
            raise RuntimeError(f"invalid TAG partial state: {state_path}")
        done = set(state)

    client = TagClient(delay=delay)
    years = client.pops_by_year(category)
    if not isinstance(years, list) or not years:
        raise RuntimeError("TAG year index returned no rows")
    total_graded = sum(
        int(row.get("totalGraded") or row.get("gradedTotal") or 0)
        for row in years
        if isinstance(row, Mapping)
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with partial.open("a", encoding="utf-8") as output:
        for year_row in years:
            if not isinstance(year_row, Mapping) or not year_row.get("cardYear"):
                raise RuntimeError("TAG year index contains an invalid row")
            year = str(year_row["cardYear"])
            sets = client.pops_by_set(category, year)
            if not isinstance(sets, list):
                raise RuntimeError(f"TAG set index is invalid for {year}")
            for set_row in sets:
                if not isinstance(set_row, Mapping):
                    raise RuntimeError(f"TAG set index contains an invalid row for {year}")
                brand_name = str(set_row.get("brandName") or "")
                set_name = str(set_row.get("cardSetName") or "")
                if not brand_name or not set_name:
                    raise RuntimeError(f"TAG set identity is incomplete for {year}")
                key = f"{year}|{brand_name}|{set_name}"
                if key in done:
                    continue
                cards = client.pops_by_card(category, year, brand_name, set_name)
                if not isinstance(cards, list):
                    raise RuntimeError(f"TAG card index is invalid for {key}")
                for card in cards:
                    if not isinstance(card, Mapping):
                        raise RuntimeError(f"TAG card index contains an invalid row for {key}")
                    output.write(
                        json.dumps(
                            {
                                "category": category,
                                "year": year,
                                "brandName": brand_name,
                                "setName": set_name,
                                **card,
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                        + "\n"
                    )
                output.flush()
                os.fsync(output.fileno())
                done.add(key)
                _atomic_json(state_path, sorted(done))

    rows_by_identity: dict[tuple[str, ...], dict[str, Any]] = {}
    with partial.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError(f"invalid TAG partial row {line_number}") from error
            if not isinstance(row, dict):
                raise RuntimeError(f"invalid TAG partial row {line_number}")
            rows_by_identity[_row_identity(row)] = row
    if len(rows_by_identity) < 1_000:
        raise RuntimeError(f"TAG daily crawl is unexpectedly small: {len(rows_by_identity)}")

    complete = destination.with_name(f".{destination.name}.{os.getpid()}.complete")
    with complete.open("w", encoding="utf-8", newline="\n") as output:
        for identity in sorted(rows_by_identity):
            output.write(json.dumps(rows_by_identity[identity], ensure_ascii=False, sort_keys=True) + "\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(complete, destination)
    partial.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    return {"rows": len(rows_by_identity), "totalGraded": total_graded, "replayed": 0}


def match_600(pop_path: str, manifest_path: str | None, out_path: str) -> None:
    """用 (cardNumber) + set 名稱模糊對照，抽出 600 卡嘅 TAG pop。

    join key 以 cardNumber 為主；set 名 mapping 後續入 source_crosswalk.py。
    """
    rows = [json.loads(l) for l in open(pop_path, encoding="utf-8")]
    by_number: dict[str, list[dict]] = {}
    for r in rows:
        by_number.setdefault(r.get("cardNumber", ""), []).append(r)

    if manifest_path and os.path.exists(manifest_path):
        manifest = json.load(open(manifest_path, encoding="utf-8"))
        targets = manifest if isinstance(manifest, list) else manifest.get("cards", [])
    else:
        targets = []

    matched, unmatched = [], []
    for t in targets:
        num = t.get("cardNumber") or t.get("card_number") or ""
        hits = by_number.get(num, [])
        if hits:
            matched.append({"target": t, "tag_pops": hits})
        else:
            unmatched.append(t)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    json.dump(
        {"matched": len(matched), "unmatched": len(unmatched), "rows": matched},
        open(out_path, "w", encoding="utf-8"),
        ensure_ascii=False,
        indent=1,
    )
    print(f"matched {len(matched)} / {len(targets)} -> {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--match-600", action="store_true")
    ap.add_argument("--category", default="Pokémon")
    ap.add_argument("--pop", default=os.path.join("data", "tag", "pops_pokemon.jsonl"))
    ap.add_argument("--manifest", default=os.path.join("manifests", "600_cards.json"))
    ap.add_argument("--out", default=os.path.join("data", "tag", "pops_pokemon.jsonl"))
    ap.add_argument("--delay", type=float, default=0.15)
    args = ap.parse_args()

    if args.dump:
        dump(args.category, args.out, args.delay)
    elif args.match_600:
        match_600(args.pop, args.manifest, args.out)
    else:
        ap.error("揀一個 mode：--dump 或 --match-600")


if __name__ == "__main__":
    main()
