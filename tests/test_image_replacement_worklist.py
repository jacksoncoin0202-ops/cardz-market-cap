from pipelines.image_replacement_worklist import build_worklist


def test_worklist_matches_local_candidates_by_collector_and_language() -> None:
    canonical = {
        "runId": "canonical",
        "cards": [
            {
                "id": "cmc_one",
                "variantId": 1,
                "facts": {
                    "identity": {
                        "collectorNumber": "ST01-012",
                        "cardLanguage": "ja",
                        "set": "Set",
                        "parallel": "sr-p",
                    }
                },
            }
        ],
    }
    prefilter = {
        "runId": "prefilter",
        "cards": [
            {
                "variantId": 1,
                "assetId": 11,
                "tcg": "one-piece",
                "decision": "reject_language_mismatch",
                "reason": "expected_ja:source_en",
            }
        ],
    }
    candidates = [
        {
            "source": "snkrdunk",
            "sourceId": "135441",
            "collectorNumber": "ST01-012",
            "language": "jp",
        },
        {
            "source": "snkrdunk",
            "sourceId": "wrong-language",
            "collectorNumber": "ST01-012",
            "language": "en",
        },
    ]
    worklist = build_worklist(canonical, prefilter, candidates)
    item = worklist["items"][0]
    assert item["rejectDecision"] == "reject_language_mismatch"
    assert [
        row["sourceId"]
        for row in item["localExactCollectorLanguageCandidates"]
    ] == ["135441"]
