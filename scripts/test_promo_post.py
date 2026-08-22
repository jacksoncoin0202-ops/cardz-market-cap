#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""promo_post gates. No 9222, no send."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import promo_post as PP  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def expect_fail(label: str, fn) -> str:
    global CHECKS
    CHECKS += 1
    try:
        fn()
    except PP.PostError as error:
        return str(error)
    FAILED.append(f"FAIL {label} did not fire")
    return ""


def main() -> int:
    x_steps = PP.plan_steps("x.com-en")
    check("x-testid", any("tweetTextarea_0" in s for s in x_steps), True)
    check("x-confirm", any("--confirm" in s for s in x_steps), True)
    th = PP.plan_steps("threads-zh")
    check("threads-community", any("CARDZGAME" in s for s in th), True)
    wa = PP.plan_steps("whatsapp-ptcg")
    check("wa-fixed-name", any("whatsapp:#PTCG" in s for s in wa), True)
    check("wa-no-list", any("send --list" in s for s in wa), False)
    check("no-fuzzy-fn", hasattr(PP, "match_hermes_group"), False)
    check("no-needles", hasattr(PP, "CHANNEL_GROUP_NEEDLES"), False)
    check("no-list-fn", hasattr(PP, "hermes_list"), False)
    check(
        "table-ptcg",
        PP.hermes_target_for_channel("whatsapp-ptcg", destinations={}),
        "whatsapp:#PTCG",
    )
    check("wa-ptcg-board", PP.P.CHANNEL_BOARD["whatsapp-ptcg"], "pokemon")
    check(
        "table-tcg-4",
        PP.hermes_target_for_channel("whatsapp-tcg-4", destinations={}),
        "whatsapp:#Yaichi x Cardz.Game TCG 社區｜4號群",
    )
    wa4 = PP.plan_steps("whatsapp-tcg-4")
    check("wa4-fixed-name", any("4號群" in s for s in wa4), True)
    check("wa4-tcg-board", PP.P.CHANNEL_BOARD["whatsapp-tcg-4"], "all")
    check(
        "wa-hermes-keys",
        set(PP.CHANNEL_HERMES_NAME),
        {k for k in PP.P.CHANNEL_SCRIPT if k.startswith("whatsapp-")},
    )
    check(
        "table-op",
        PP.hermes_target_for_channel("whatsapp-op", destinations={}),
        "whatsapp:#海賊王",
    )
    check(
        "placeholder-ignored",
        PP.hermes_target_for_channel(
            "whatsapp-ptcg",
            destinations={"whatsapp-ptcg": "whatsapp:REPLACE_PTCG_CHAT_ID"},
        ),
        "whatsapp:#PTCG",
    )
    check(
        "exact-override",
        PP.hermes_target_for_channel(
            "whatsapp-ptcg",
            destinations={"whatsapp-ptcg": "whatsapp:#PTCG"},
        ),
        "whatsapp:#PTCG",
    )
    expect_fail(
        "unknown-channel",
        lambda: PP.hermes_target_for_channel("x.com-en", destinations={}),
    )

    expect_fail("pack-missing", lambda: PP.load_pack(Path(tempfile.gettempdir()) / "no-such-promo-pack"))
    expect_fail(
        "wa-no-dest",
        lambda: PP.send_whatsapp("whatsapp-ptcg", "hi", Path("x.jpg"), confirm=True, destinations={}),
    )

    with tempfile.TemporaryDirectory() as tmp:
        pack = Path(tmp)
        (pack / "brief.json").write_text(json.dumps({"generation": "g1", "period": "7d", "post": False}), encoding="utf-8")
        err = expect_fail("no-copy", lambda: PP.channel_copy(pack, "x.com-en"))
        check("no-copy-mentions", "missing copy" in err, True)

    if FAILED:
        print("\n".join(FAILED))
        print(f"{len(FAILED)} failed / {CHECKS} checks")
        return 1
    print(f"PASS {CHECKS} checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
