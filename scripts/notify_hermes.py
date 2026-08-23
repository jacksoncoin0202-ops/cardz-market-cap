#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CARDZ -> Hermes Telegram 通報機制。

Subcommands：
  test                                   實測發送（驗證 bot/群/thread 通唔通）；寫 state.last_test
  chain --chain nightly|morning|refresh  每條 chain 跑完報狀態（--notify-on failure|always）
  release --outcome published|no-change|failed
                                         daily_public_release.sh 出口（WSL 入面 call）
  alert --key K --text T [--cooldown-min N]
                                         任意 watchdog 警報，同 key 喺 cooldown 內唔重覆嘈
  digest                                 每日摘要：universe 數、要人手裁決嘅新卡、pop 跌警報

秘密處理：TELEGRAM_BOT_TOKEN 由 Hermes 本尊嘅 ~/.hermes/.env（WSL Ubuntu）讀，
CARDZ 唔持有、唔 commit、唔印落 log。目標群 https://t.me/c/3754625020/2925
→ chat -1003754625020 thread 2925（`hermes send --list telegram` 顯示為
「AI協作群組 / topic 2925 [-1003754625020:2925]」）。

Backend（env CARDZ_NOTIFY_BACKEND）：
  bot-api   （預設）直接 POST api.telegram.org/bot<token>/sendMessage，2026-08-13 實證過
             （morning-20260813T003002Z.log: sent message_id=22191/22192）
  hermes-cli 用 Hermes 自己嘅 `hermes.real send --to telegram:<chat>:<thread>`
             （Windows 經 wsl.exe，WSL 直接 call），CARDZ 完全唔掂 token
任一 backend 失敗會 fallback 去另一個。

設計原則：通報失敗唔准搞紅條 chain（TG 落地失敗只 warn，exit 0）；
`test` 例外，發唔到 exit 1（因為佢存在意義就係驗證發送）。
stdout 只出 ASCII（PowerShell `*>> $log` 用 console codepage，非 ASCII 會變 ????）。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

DEFAULT_CHAT_ID = "-1003754625020"
DEFAULT_THREAD_ID = "2925"
HERMES_ENV_CANDIDATES = [
    Path(r"\\wsl$\Ubuntu\home\jackson0202\.hermes\.env"),
    Path(r"\\wsl.localhost\Ubuntu\home\jackson0202\.hermes\.env"),
    Path("/home/jackson0202/.hermes/.env"),  # 喺 WSL 入面（daily_public_release.sh）call 時
    Path.home() / ".hermes" / ".env",
]
HERMES_REAL_LINUX = "/home/jackson0202/.local/bin/hermes.real"  # 跳過 ensure_local_stack wrapper
STATE_PATH = ROOT / "data" / "runtime" / "notify" / "hermes_notify_state.json"

# 三條 Task Scheduler cron（CARDZ-037-*），digest 會照住呢張表報今日邊條行咗
CHAIN_SCHEDULE = {
    "nightly": "03:30 夜鏈（HTTP collect→discover→accept）",
    "morning": "09:30 朝鏈（HTTP+browser collect→discover→accept→publish）",
    "refresh": "11:30/16:30 補發 slot（accept→publish）",
}


def _log(msg: str) -> None:
    # ASCII only，避免 PowerShell console codepage 變 ????
    print(msg.encode("ascii", "backslashreplace").decode("ascii"))


def _warn(msg: str) -> None:
    print(msg.encode("ascii", "backslashreplace").decode("ascii"), file=sys.stderr)


def _load_token() -> str:
    tok = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if tok:
        return tok
    for p in HERMES_ENV_CANDIDATES:
        try:
            if not p.exists():
                continue
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    value = line.split("=", 1)[1].strip()
                    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                        value = value[1:-1]
                    if value:
                        return value
        except OSError:
            continue
    return ""


def _target() -> tuple[str, str]:
    chat_id = os.environ.get("CARDZ_TG_CHAT_ID", DEFAULT_CHAT_ID)
    thread_id = os.environ.get("CARDZ_TG_THREAD_ID", DEFAULT_THREAD_ID)
    return chat_id, thread_id


def _send_bot_api(text: str) -> bool:
    """直接 Bot API。thread 唔存在會自動 fallback 去主群。永不印 token。"""
    token = _load_token()
    if not token:
        _warn("notify_hermes: no TELEGRAM_BOT_TOKEN reachable for bot-api backend")
        return False
    chat_id, thread_id = _target()
    payloads = []
    base = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if thread_id:
        payloads.append({**base, "message_thread_id": thread_id})
    payloads.append(base)
    for payload in payloads:
        data = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                out = json.load(resp)
            if out.get("ok"):
                _log(f"notify_hermes: sent message_id={out['result']['message_id']} backend=bot-api")
                _remember_last_send(out["result"]["message_id"], "bot-api")
                return True
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8", "replace")).get("description", "")
            except Exception:
                detail = str(exc)
            _warn(f"notify_hermes: bot-api send failed ({detail}); trying fallback payload")
            continue
        except (urllib.error.URLError, OSError) as exc:
            _warn(f"notify_hermes: bot-api network error ({exc})")
            return False
    return False


def _send_hermes_cli(text: str) -> bool:
    """經 Hermes 自己嘅 `hermes send`（讀 ~/.hermes/.env，唔使 gateway）。
    Windows 經 wsl.exe；text 行 stdin 避免 wsl.exe argv 剝字元。"""
    chat_id, thread_id = _target()
    target = f"telegram:{chat_id}" + (f":{thread_id}" if thread_id else "")
    if os.name == "nt":
        cmd = ["wsl.exe", "-d", "Ubuntu", "--", HERMES_REAL_LINUX, "send", "-q", "--to", target]
    else:
        cmd = [HERMES_REAL_LINUX, "send", "-q", "--to", target]
    try:
        # hermes send 唔食 parse_mode；除返 HTML tag 俾佢當純文字
        plain = html.unescape(
            text.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
        )
        proc = subprocess.run(cmd, input=plain.encode("utf-8"), capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _warn(f"notify_hermes: hermes-cli failed to run ({exc})")
        return False
    if proc.returncode == 0:
        _log("notify_hermes: sent backend=hermes-cli")
        _remember_last_send(None, "hermes-cli")
        return True
    _warn(f"notify_hermes: hermes-cli exit={proc.returncode}")
    return False


def send_message(text: str) -> bool:
    # TG 上限 4096；留返 buffer
    if len(text) > 3900:
        text = text[:3860] + "\n…（截咗，全文睇 log）"
    backend = os.environ.get("CARDZ_NOTIFY_BACKEND", "bot-api").strip().lower()
    order = [_send_hermes_cli, _send_bot_api] if backend == "hermes-cli" else [_send_bot_api, _send_hermes_cli]
    for fn in order:
        if fn(text):
            return True
    _warn("notify_hermes: all backends failed; message dropped (chain exit unaffected)")
    return False


def _load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _remember_last_send(message_id: Any, backend: str) -> None:
    try:
        state = _load_state()
        state["last_send"] = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "message_id": message_id,
            "backend": backend,
        }
        _save_state(state)
    except OSError:
        pass


def _record_chain_run(chain: str, exit_code: int, status: str) -> None:
    state = _load_state()
    runs = state.setdefault("chain_runs", {})
    today = datetime.now().strftime("%Y-%m-%d")
    day = runs.setdefault(today, {})
    day[chain] = {"exit": exit_code, "status": status, "at": datetime.now().strftime("%H:%M")}
    # 只留 7 日
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    for key in sorted(runs):
        if key < cutoff:
            runs.pop(key, None)
    _save_state(state)


def _cooldown_ok(state: dict[str, Any], key: str, cooldown_min: int) -> bool:
    """同一 key 喺 cooldown 內只發一次。state['alerts'][key] = {last_sent_at, count, suppressed}"""
    alerts = state.setdefault("alerts", {})
    entry = alerts.setdefault(key, {"last_sent_at": None, "count": 0, "suppressed": 0})
    last = entry.get("last_sent_at")
    if last:
        try:
            if datetime.now() - datetime.fromisoformat(last) < timedelta(minutes=cooldown_min):
                entry["suppressed"] = int(entry.get("suppressed", 0)) + 1
                return False
        except ValueError:
            pass
    return True


def _mark_sent(state: dict[str, Any], key: str) -> None:
    entry = state.setdefault("alerts", {}).setdefault(key, {"last_sent_at": None, "count": 0, "suppressed": 0})
    entry["last_sent_at"] = datetime.now().isoformat(timespec="seconds")
    entry["count"] = int(entry.get("count", 0)) + 1
    entry["suppressed"] = 0


def cmd_chain(args: argparse.Namespace) -> int:
    exit_code = int(args.exit_code)
    _record_chain_run(args.chain, exit_code, args.status)
    if args.notify_on == "failure" and exit_code == 0:
        _log("notify_hermes: chain ok, notify_on=failure -> silent")
        return 0
    icon = "✅" if exit_code == 0 else "🔴"
    label = CHAIN_SCHEDULE.get(args.chain, args.chain)
    lines = [f"{icon} <b>CARDZ {args.chain}</b> {label}", f"步驟：<code>{html.escape(args.status)}</code>"]
    if exit_code != 0:
        log_name = Path(args.log).name if args.log else "(無log)"
        lines.append(f"⚠️ 有步驟紅咗，要人手睇：<code>data/runtime/logs/{html.escape(log_name)}</code>")
        lines.append("DADDY 你話點搞？（回覆呢條 thread 或者叫 agent 查 log）")
    send_message("\n".join(lines))
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    """daily_public_release.sh 出口。published/failed 一定報；no-change 預設只記 state。"""
    state = _load_state()
    releases = state.setdefault("release_runs", {})
    today = datetime.now().strftime("%Y-%m-%d")
    day = releases.setdefault(today, [])
    day.append(
        {
            "at": datetime.now().strftime("%H:%M"),
            "outcome": args.outcome,
            "generation": args.generation,
            "stage": args.stage,
            "exit": int(args.exit_code),
        }
    )
    day[:] = day[-20:]
    cutoff = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    for key in sorted(releases):
        if key < cutoff:
            releases.pop(key, None)

    if args.outcome == "no-change" and args.notify_on != "always":
        _save_state(state)
        _log("notify_hermes: release no-change -> recorded, silent")
        return 0

    if args.outcome == "failed":
        key = f"release-failed:{args.stage or 'unknown'}"
        if not _cooldown_ok(state, key, int(args.cooldown_min)):
            _save_state(state)
            _log(f"notify_hermes: release failed but within cooldown ({key}) -> silent")
            return 0
        text = (
            f"🔴 <b>CARDZ daily_public_release 失敗</b> stage=<code>{html.escape(args.stage or '?')}</code>"
            f" exit={int(args.exit_code)}\n"
            f"generation=<code>{html.escape(args.generation or '-')}</code>\n"
            "同一 slot 會自動重試 3 次；11:30/16:30 有 retry slot。持續紅就叫 agent 查 refresh-/morning- log。"
        )
        if send_message(text):
            _mark_sent(state, key)
        _save_state(state)
        return 0

    icon = "🚀" if args.outcome == "published" else "⏸"
    text = f"{icon} <b>CARDZ {args.outcome}</b> generation=<code>{html.escape(args.generation or '-')}</code>"
    send_message(text)
    _save_state(state)
    return 0


def cmd_alert(args: argparse.Namespace) -> int:
    """Watchdog／任意警報。同一 --key 喺 --cooldown-min 內只嘈一次。"""
    state = _load_state()
    if not _cooldown_ok(state, args.key, int(args.cooldown_min)):
        _save_state(state)
        _log(f"notify_hermes: alert {args.key} within cooldown -> silent")
        return 0
    icon = {"info": "ℹ️", "warn": "⚠️", "error": "🔴"}.get(args.level, "⚠️")
    text = f"{icon} <b>CARDZ {html.escape(args.key)}</b>\n{html.escape(args.text)}"
    if send_message(text):
        _mark_sent(state, args.key)
    _save_state(state)
    return 0


def _fmt_variant(row: dict[str, Any]) -> str:
    return (
        f"v{row['variant_id']} pop={row.get('pop') or '?'} "
        f"{row.get('tcg_code') or '?'}/{row.get('card_language') or '?'} "
        f"{row.get('set_code') or ''} #{row.get('collector_number') or '?'}"
    )


def _collect_digest(state: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    from rebuild_036 import DAILY_CREDENTIALS_ENV, connect  # noqa: E402

    conn = connect(DAILY_CREDENTIALS_ENV)
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT COUNT(*) n FROM market_universe_member am
               INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1"""
        )
        universe = int(cur.fetchone()["n"])

        # ledger 分類（只讀，唔 rebuild）
        cur.execute(
            """SELECT l.discovery_status, l.blocker_code, COUNT(*) n
               FROM market_identity_discovery_ledger l
               GROUP BY l.discovery_status, l.blocker_code"""
        )
        ledger_counts = [
            (r["discovery_status"], r["blocker_code"], int(r["n"])) for r in cur.fetchall()
        ]

        # 要人手裁決嘅卡（identity_ambiguous / manual_review / multiple_exact_bindings）
        cur.execute(
            """SELECT l.variant_id, l.discovery_status, l.blocker_code,
                      pi.tcg_code, pi.card_language, pi.set_code, pi.collector_number,
                      rm.latest_psa10_population AS pop
               FROM market_identity_discovery_ledger l
               LEFT JOIN catalog_printing_identity pi ON pi.variant_id=l.variant_id
               LEFT JOIN catalog_rebuild_member rm ON rm.variant_id=l.variant_id
                AND rm.generation_id=(SELECT generation_id FROM catalog_rebuild_member
                                      ORDER BY computed_at DESC LIMIT 1)
               WHERE l.discovery_status='identity_ambiguous'
                  OR l.blocker_code IN ('manual_review','multiple_exact_bindings')
               ORDER BY rm.latest_psa10_population DESC"""
        )
        manual_rows = [dict(r) for r in cur.fetchall()]

        # pop 跌警報：最新一日 PSA10 pop 細過上一日 → 一定有問題（DADDY 訂嘅 invariant）
        cur.execute(
            """WITH ranked AS (
                 SELECT variant_id, observed_date, top_grade_population,
                        ROW_NUMBER() OVER (PARTITION BY variant_id ORDER BY observed_date DESC) rn
                 FROM market_grader_population_observation
                 WHERE UPPER(grader_code)='PSA' AND top_grade_label='10'
               )
               SELECT a.variant_id, a.top_grade_population cur_pop, a.observed_date cur_date,
                      b.top_grade_population prev_pop, b.observed_date prev_date,
                      pi.tcg_code, pi.card_language, pi.set_code, pi.collector_number
               FROM ranked a
               INNER JOIN ranked b ON b.variant_id=a.variant_id AND b.rn=2
               LEFT JOIN catalog_printing_identity pi ON pi.variant_id=a.variant_id
               WHERE a.rn=1 AND a.top_grade_population < b.top_grade_population"""
        )
        pop_drops = [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()

    today = datetime.now().strftime("%Y-%m-%d")
    lines: list[str] = [f"📊 <b>CARDZ 每日 digest</b> {today}"]

    runs_today = state.get("chain_runs", {}).get(today, {})
    chain_bits = []
    for chain in ("nightly", "morning", "refresh"):
        run = runs_today.get(chain)
        if run is None:
            chain_bits.append(f"{chain}=未行")
        else:
            chain_bits.append(f"{chain}={'✅' if run['exit'] == 0 else '🔴'}({run['at']})")
    lines.append("鏈：" + " ".join(chain_bits))
    lines.append(f"Universe：{universe} 張")

    interesting = [
        (status, blocker, n)
        for status, blocker, n in ledger_counts
        if status not in ("active",)
    ]
    if interesting:
        lines.append(
            "Ledger："
            + "，".join(f"{status or '?'}[{blocker or '-'}]×{n}" for status, blocker, n in interesting)
        )

    seen_manual = set(state.get("seen_manual_review", []))
    new_manual = [r for r in manual_rows if int(r["variant_id"]) not in seen_manual]
    if new_manual:
        lines.append(f"🙋 <b>新增 {len(new_manual)} 張要人手裁決</b>（DADDY 你話點搞？）：")
        for row in new_manual[:8]:
            lines.append(f"  • {_fmt_variant(row)} [{row.get('blocker_code') or row.get('discovery_status')}]")
        if len(new_manual) > 8:
            lines.append(f"  …仲有 {len(new_manual) - 8} 張，叫 agent 攞全名單")
    lines.append(f"人手裁決 backlog 總數：{len(manual_rows)} 張")

    seen_drops = set(state.get("seen_pop_drops", []))
    new_drops = [
        r for r in pop_drops if f"{r['variant_id']}:{r['cur_date']}" not in seen_drops
    ]
    if new_drops:
        lines.append(f"🚨 <b>POP 跌咗 {len(new_drops)} 張</b>（POP 只會多不會小，跌=出事）：")
        for row in new_drops[:8]:
            lines.append(
                f"  • {_fmt_variant(row)} {row['prev_pop']}({row['prev_date']}) → {row['cur_pop']}({row['cur_date']})"
            )
    else:
        lines.append("POP 跌警報：0 ✅")

    # 更新 state（先寄先記，寄失敗都記低，避免同一批嘢日日重複嘈）
    state["seen_manual_review"] = sorted(
        seen_manual | {int(r["variant_id"]) for r in manual_rows}
    )
    state["seen_pop_drops"] = sorted(
        seen_drops | {f"{r['variant_id']}:{r['cur_date']}" for r in pop_drops}
    )[-500:]
    return "\n".join(lines), state


def cmd_identity_brief(args: argparse.Namespace) -> int:
    """早朝身份日報：純讀 render，經 send_message 出 HTML（link 生存）。

    Dedupe 用 identity_brief 自己嘅 key，永遠唔掂 legacy 037 digest 嗰個
    `seen_manual_review`：兩份報告講唔同嘢，共用一個 key 會令其中一份靜咗。
    """

    sys.path.insert(0, str(ROOT / "pipelines"))
    import identity_brief

    state = _load_state()
    seen = state.get(identity_brief.SEEN_STATE_KEY) or {}
    result = identity_brief.build(business_date=args.business_date or None, seen=seen)
    message = str(result.get("message") or "")
    if args.dry_run:
        print(message)
        return 0
    if not send_message(message):
        return 1
    # Only a delivered message may mark its rows as shown; a dropped send that
    # stamped the state would silence tomorrow's report as well.
    state[identity_brief.SEEN_STATE_KEY] = result.get("seen") or {}
    _save_state(state)
    return 0


def cmd_digest(_args: argparse.Namespace) -> int:
    state = _load_state()
    try:
        text, state = _collect_digest(state)
    except Exception as exc:  # digest 失敗都要通知，唔准靜靜死
        send_message(
            f"🔴 CARDZ digest 產生失敗：<code>{html.escape(type(exc).__name__)}: {html.escape(str(exc))}</code>\nDADDY 叫 agent 查下。"
        )
        _warn(f"notify_hermes: digest failed: {exc}")
        return 0
    send_message(text)
    _save_state(state)
    return 0


def cmd_test(_args: argparse.Namespace) -> int:
    ok = send_message(
        "🔔 CARDZ 通報機制上線測試：呢條 thread 以後會收到\n"
        "① 每條 chain 跑完嘅狀態（紅先嘈）\n"
        "② daily_public_release 結果（published / failed）\n"
        "③ 每日 digest（universe 數／要人手裁決嘅新卡／POP 跌警報）"
    )
    state = _load_state()
    state["last_test"] = {"at": datetime.now().isoformat(timespec="seconds"), "ok": ok}
    _save_state(state)
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_chain = sub.add_parser("chain", help="chain 完成通報")
    p_chain.add_argument("--chain", required=True, choices=sorted(CHAIN_SCHEDULE))
    p_chain.add_argument("--status", required=True, help="例如 collect=0 discover=0 accept=0")
    p_chain.add_argument("--exit-code", required=True)
    p_chain.add_argument("--log", default="")
    p_chain.add_argument("--notify-on", choices=("always", "failure"), default="always")
    p_chain.set_defaults(func=cmd_chain)

    p_rel = sub.add_parser("release", help="daily_public_release 出口通報")
    p_rel.add_argument("--outcome", required=True, choices=("published", "no-change", "failed"))
    p_rel.add_argument("--generation", default="")
    p_rel.add_argument("--stage", default="")
    p_rel.add_argument("--exit-code", default="0")
    p_rel.add_argument("--notify-on", choices=("always", "failure"), default="failure",
                       help="no-change 預設唔嘈；always 先報 no-change")
    p_rel.add_argument("--cooldown-min", default="20", help="failed 同一 stage 幾多分鐘內唔重覆嘈")
    p_rel.set_defaults(func=cmd_release)

    p_alert = sub.add_parser("alert", help="watchdog／任意警報（cooldown dedupe）")
    p_alert.add_argument("--key", required=True)
    p_alert.add_argument("--text", required=True)
    p_alert.add_argument("--level", choices=("info", "warn", "error"), default="warn")
    p_alert.add_argument("--cooldown-min", default="60")
    p_alert.set_defaults(func=cmd_alert)

    p_identity = sub.add_parser("identity-brief", help="早朝身份日報")
    p_identity.add_argument("--business-date", default="")
    p_identity.add_argument("--dry-run", action="store_true", help="只印，唔發")
    p_identity.set_defaults(func=cmd_identity_brief)

    p_digest = sub.add_parser("digest", help="每日摘要")
    p_digest.set_defaults(func=cmd_digest)

    p_test = sub.add_parser("test", help="實測發送")
    p_test.set_defaults(func=cmd_test)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
