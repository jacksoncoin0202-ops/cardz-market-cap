"""Archived one-time native-image migration.

This tool used to resolve ``publish-staging/latest.json`` and then overwrite the
snapshot referenced by that pointer. Published generations are immutable and
only ``publish-snapshot.mjs`` may advance the atomic pointer, so every invocation
now fails before reading or writing files.

Use the maintained candidate pipeline instead:
``canonical_public_snapshot.py`` -> ``ensure_std_card_images.py`` -> strict QC
-> ``publish-snapshot.mjs``.
"""
from __future__ import annotations

import sys


ARCHIVED_REASON = (
    "native_image_refetch.py is archived: it must not mutate a pointed "
    "generation. Build a new candidate and publish it through the official "
    "publisher."
)


def main() -> int:
    print(ARCHIVED_REASON, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
