"""Prove five-locale defect fires. Plant a blank ko, expect reject, then a good row."""

from __future__ import annotations

from editorial_locale_sync import defect_five

def _pad(text: str) -> str:
    return text if len(text) >= 80 else (text + "。") * 4


GOOD = {
    "en": _pad("This English printing still trades as a high-population art object rather than a scarce trophy. The market reads verified supply."),
    "zhTW": _pad("此英文版仍以高存量藝術物件的方式交易，而不是稀缺獎盃。"),
    "zhCN": _pad("此英文版仍以高存量艺术物件的方式交易，而不是稀缺奖杯。"),
    "ja": _pad("この英語版は希少なトロフィーではなく、高枚数のアート作品として取引されています。"),
    "ko": _pad("이 영문판은 희귀 트로피가 아니라 높은 개체 수의 아트 오브제로 거래됩니다."),
}


def test_blank_ko_fires() -> None:
    bad = {**GOOD, "ko": ""}
    reason = defect_five(1, bad)
    assert reason == "ko 空白", reason


def test_good_passes() -> None:
    assert defect_five(1, GOOD) is None


if __name__ == "__main__":
    test_blank_ko_fires()
    test_good_passes()
    print("test_story_rewrite_gate: ok")
