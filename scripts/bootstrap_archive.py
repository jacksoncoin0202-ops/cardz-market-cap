#!/usr/bin/env python3
"""Build, verify, and restore a minimal active-universe bootstrap archive.

The archive is intentionally narrower than the private landing tree. It keeps
only the current universe lock, its active provider worklists, and canonical
observations that resolve to a member of that lock.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_POINTER = Path("data/runtime/private-source-map/active-universe.json")
LOCK_DIRECTORY = Path("data/runtime/private-source-map/active-universe-locks")
LANDING_DIRECTORY = Path("data/runtime/private-landing")
MANIFEST_NAME = "bootstrap-manifest.json"
SCHEMA_VERSION = "1.0.0"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SECRET_KEY_PATTERN = re.compile(
    r"(?:^|_)(?:api_?key|authorization|bearer|cookie|password|secret|token)(?:$|_)",
    re.IGNORECASE,
)
SIGNED_URL_PATTERN = re.compile(
    r"(?:x-amz-(?:credential|signature)|x-goog-signature|[?&](?:access_token|token|api_key)=)",
    re.IGNORECASE,
)


class ArchiveError(RuntimeError):
    """Raised when a bootstrap archive is incomplete or unsafe."""


def canonical_json(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return (text + "\n").encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ArchiveError(f"cannot read JSON document: {path}") from exc
    if not isinstance(document, Mapping):
        raise ArchiveError(f"JSON document must be an object: {path}")
    return document


def repo_path(path: Path, root: Path) -> str:
    try:
        relative = path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ArchiveError(f"path is outside repository root: {path}") from exc
    return relative.as_posix()


def validate_member_name(name: str) -> str:
    if not name or "\\" in name or "\x00" in name:
        raise ArchiveError(f"unsafe archive path: {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or name != path.as_posix():
        raise ArchiveError(f"unsafe archive path: {name!r}")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ArchiveError(f"unsafe archive path: {name!r}")
    if path.parts and ":" in path.parts[0]:
        raise ArchiveError(f"unsafe archive path: {name!r}")
    return path.as_posix()


def validate_no_secrets(value: Any, context: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if SECRET_KEY_PATTERN.search(str(key)):
                raise ArchiveError(f"secret-like key found in {context}")
            validate_no_secrets(nested, context)
    elif isinstance(value, list):
        for nested in value:
            validate_no_secrets(nested, context)
    elif isinstance(value, str) and SIGNED_URL_PATTERN.search(value):
        raise ArchiveError(f"signed credential-like URL found in {context}")


def load_lock(root: Path) -> tuple[Mapping[str, Any], Path, Mapping[str, Any], str, str]:
    pointer_path = root / ACTIVE_POINTER
    pointer = read_json(pointer_path)
    lock = pointer.get("lock")
    if not isinstance(lock, Mapping):
        raise ArchiveError("active-universe pointer has no lock metadata")
    lock_id = str(lock.get("lockId") or "")
    lock_hash = str(pointer.get("payloadSha256") or "")
    immutable_name = str(lock.get("immutableFile") or "")
    if not lock_id or not SHA256_PATTERN.fullmatch(lock_hash):
        raise ArchiveError("active-universe lock ID or hash is invalid")
    if not immutable_name or Path(immutable_name).name != immutable_name:
        raise ArchiveError("active-universe immutable filename is invalid")
    immutable_path = root / LOCK_DIRECTORY / immutable_name
    immutable = read_json(immutable_path)
    if immutable != pointer:
        raise ArchiveError("active pointer and immutable lock differ")
    if immutable.get("payloadSha256") != lock_hash:
        raise ArchiveError("immutable lock hash does not match active pointer")
    validate_no_secrets(pointer, ACTIVE_POINTER.as_posix())
    return pointer, immutable_path, immutable, lock_id, lock_hash


def active_references(pointer: Mapping[str, Any]) -> set[tuple[str, str]]:
    cards = pointer.get("cards")
    if not isinstance(cards, list) or not cards:
        raise ArchiveError("active-universe pointer has no cards")
    references: set[tuple[str, str]] = set()
    for card in cards:
        if not isinstance(card, Mapping):
            raise ArchiveError("active-universe card is invalid")
        source = str(card.get("canonicalSourceCode") or "").casefold()
        external_id = str(card.get("canonicalExternalId") or "")
        if not source or not external_id:
            raise ArchiveError("active-universe card has no canonical source identity")
        reference = (source, external_id)
        if reference in references:
            raise ArchiveError("active-universe contains a duplicate canonical source identity")
        references.add(reference)
    return references


def batch_timestamp(batch: Mapping[str, Any]) -> str:
    return str(batch.get("fetchedAt") or batch.get("effectiveAt") or "")


def source_batch_matches_lock(path: Path, lock_hash: str) -> bool:
    manifest_path = path.with_name("manifest.json")
    if not manifest_path.is_file():
        return False
    manifest = read_json(manifest_path)
    return manifest.get("activeUniverseSha256") == lock_hash


def filtered_batch_bytes(
    path: Path,
    batch: Mapping[str, Any],
    references: set[tuple[str, str]],
    lock_hash: str,
) -> tuple[bytes, int, str] | None:
    observations = batch.get("observations")
    if not isinstance(observations, list):
        raise ArchiveError(f"canonical batch has no observations: {path}")
    active = [
        row
        for row in observations
        if isinstance(row, Mapping)
        and (
            str(row.get("sourceCode") or "").casefold(),
            str(row.get("externalEntityId") or ""),
        )
        in references
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(row.get("observedDate") or ""))
    ]
    if not active:
        return None
    validate_no_secrets(active, path.as_posix())
    semantic_hash = sha256_bytes(canonical_json(active))
    filtered = dict(batch)
    filtered["observations"] = active
    filtered["rejected"] = []
    filtered["payloadSha256"] = semantic_hash
    filtered["bootstrapArchive"] = {
        "activeUniverseSha256": lock_hash,
        "activeObservationCount": len(active),
        "originalObservationCount": len(observations),
        "sourceBatchSha256": sha256_bytes(path.read_bytes()),
    }
    return canonical_json(filtered), len(active), semantic_hash


def select_canonical_batches(
    root: Path,
    references: set[tuple[str, str]],
    lock_hash: str,
) -> list[tuple[str, bytes, dict[str, Any]]]:
    landing = root / LANDING_DIRECTORY
    if not landing.is_dir():
        raise ArchiveError(f"canonical landing directory is missing: {landing}")
    candidates: list[tuple[str, str, str, bytes, dict[str, Any]]] = []
    found_current_source_batch = False
    for path in sorted(landing.rglob("canonical-batch.json")):
        relative_landing = path.relative_to(landing)
        stream = relative_landing.parts[0] if relative_landing.parts else ""
        if stream == "sources":
            if not source_batch_matches_lock(path, lock_hash):
                continue
        batch = read_json(path)
        filtered_result = filtered_batch_bytes(path, batch, references, lock_hash)
        if filtered_result is None:
            continue
        if stream == "sources":
            found_current_source_batch = True
        filtered, count, semantic_hash = filtered_result
        metadata = {
            "activeObservationCount": count,
            "originalPath": repo_path(path, root),
            "semanticSha256": semantic_hash,
        }
        candidates.append(
            (
                stream,
                semantic_hash,
                f"{batch_timestamp(batch)}\x00{str(batch.get('runId') or '')}\x00{path.as_posix()}",
                filtered,
                metadata,
            )
        )
    if not found_current_source_batch:
        raise ArchiveError("no canonical source batch matches the current active-universe lock")
    if not candidates:
        raise ArchiveError("no canonical batches are available for active-universe replay")

    # Exact semantic duplicates in the same source stream are redundant. Keep
    # the latest accepted path while retaining every distinct historical batch.
    unique: dict[tuple[str, str], tuple[str, str, str, bytes, dict[str, Any]]] = {}
    for candidate in candidates:
        key = (candidate[0], candidate[1])
        previous = unique.get(key)
        if previous is None or candidate[2] > previous[2]:
            unique[key] = candidate

    selected: list[tuple[str, bytes, dict[str, Any]]] = []
    for _, _, _, data, metadata in sorted(unique.values(), key=lambda row: row[2]):
        archive_path = metadata["originalPath"]
        selected.append((archive_path, data, metadata))
    return selected


def build_entries(root: Path) -> tuple[dict[str, bytes], dict[str, Any]]:
    pointer, immutable_path, _, lock_id, lock_hash = load_lock(root)
    references = active_references(pointer)
    entries: dict[str, bytes] = {}

    pointer_path = root / ACTIVE_POINTER
    entries[ACTIVE_POINTER.as_posix()] = pointer_path.read_bytes()
    immutable_archive_path = repo_path(immutable_path, root)
    entries[immutable_archive_path] = immutable_path.read_bytes()

    worklists: list[str] = []
    source_map = root / ACTIVE_POINTER.parent
    for path in sorted(source_map.glob("active-*-ids.txt")):
        archive_path = repo_path(path, root)
        entries[archive_path] = path.read_bytes()
        worklists.append(archive_path)
    if not worklists:
        raise ArchiveError("no active provider worklists were found")

    batches = select_canonical_batches(root, references, lock_hash)
    batch_paths: list[str] = []
    batch_metadata: list[dict[str, Any]] = []
    for archive_path, data, metadata in batches:
        entries[archive_path] = data
        batch_paths.append(archive_path)
        batch_metadata.append(metadata)

    for name in entries:
        validate_member_name(name)
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "cardz-active-bootstrap",
        "createdAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "lockId": lock_id,
        "lockHash": lock_hash,
        "activeUniversePath": ACTIVE_POINTER.as_posix(),
        "immutableLockPath": immutable_archive_path,
        "providerWorklists": worklists,
        "canonicalBatches": batch_paths,
        "batchSelection": batch_metadata,
        "entries": [
            {"path": name, "size": len(data), "sha256": sha256_bytes(data)}
            for name, data in sorted(entries.items())
        ],
    }
    return entries, manifest


def tar_info(name: str, size: int, timestamp: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mode = 0o600
    info.mtime = timestamp
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def build_archive(root: Path, output: Path, *, overwrite: bool = False) -> Mapping[str, Any]:
    root = root.resolve()
    output = output.resolve()
    if output.exists() and not overwrite:
        raise ArchiveError(f"archive already exists: {output}")
    entries, manifest = build_entries(root)
    manifest_bytes = canonical_json(manifest, pretty=True)
    timestamp = int(datetime.fromisoformat(manifest["createdAt"].replace("Z", "+00:00")).timestamp())
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    try:
        with tarfile.open(temporary, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
            archive.addfile(tar_info(MANIFEST_NAME, len(manifest_bytes), timestamp), io.BytesIO(manifest_bytes))
            for name, data in sorted(entries.items()):
                archive.addfile(tar_info(name, len(data), timestamp), io.BytesIO(data))
        verify_archive(temporary)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return manifest


def read_manifest(archive: tarfile.TarFile, members: Mapping[str, tarfile.TarInfo]) -> Mapping[str, Any]:
    member = members.get(MANIFEST_NAME)
    if member is None or not member.isfile():
        raise ArchiveError("archive manifest is missing")
    extracted = archive.extractfile(member)
    if extracted is None:
        raise ArchiveError("archive manifest cannot be read")
    try:
        document = json.loads(extracted.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArchiveError("archive manifest is invalid") from exc
    if not isinstance(document, Mapping):
        raise ArchiveError("archive manifest must be an object")
    return document


def verify_archive(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise ArchiveError(f"archive does not exist: {path}")
    with tarfile.open(path, mode="r:gz") as archive:
        members: dict[str, tarfile.TarInfo] = {}
        for member in archive.getmembers():
            name = validate_member_name(member.name)
            if name in members:
                raise ArchiveError(f"duplicate archive member: {name}")
            if not member.isfile():
                raise ArchiveError(f"archive contains a non-file member: {name}")
            members[name] = member
        manifest = read_manifest(archive, members)
        if manifest.get("schemaVersion") != SCHEMA_VERSION or manifest.get("kind") != "cardz-active-bootstrap":
            raise ArchiveError("archive manifest schema or kind is unsupported")
        lock_id = str(manifest.get("lockId") or "")
        lock_hash = str(manifest.get("lockHash") or "")
        if not lock_id or not SHA256_PATTERN.fullmatch(lock_hash):
            raise ArchiveError("archive manifest lock identity is invalid")
        manifest_entries = manifest.get("entries")
        if not isinstance(manifest_entries, list) or not manifest_entries:
            raise ArchiveError("archive manifest has no entries")

        expected: dict[str, Mapping[str, Any]] = {}
        for entry in manifest_entries:
            if not isinstance(entry, Mapping):
                raise ArchiveError("archive manifest entry is invalid")
            name = validate_member_name(str(entry.get("path") or ""))
            size = entry.get("size")
            digest = str(entry.get("sha256") or "")
            if name in expected or not isinstance(size, int) or size < 0 or not SHA256_PATTERN.fullmatch(digest):
                raise ArchiveError(f"archive manifest entry is invalid: {name}")
            expected[name] = entry
        if set(members) != set(expected) | {MANIFEST_NAME}:
            raise ArchiveError("archive members do not match the manifest")

        critical_paths = {
            str(manifest.get("activeUniversePath") or ""),
            str(manifest.get("immutableLockPath") or ""),
        }
        worklists = manifest.get("providerWorklists")
        batches = manifest.get("canonicalBatches")
        if not isinstance(worklists, list) or not worklists or not isinstance(batches, list) or not batches:
            raise ArchiveError("archive manifest is missing worklists or canonical batches")
        allowed_paths = critical_paths | {str(value) for value in worklists} | {str(value) for value in batches}
        if set(expected) != allowed_paths:
            raise ArchiveError("archive contains files outside the active bootstrap contract")

        retained: dict[str, bytes] = {}
        for name, entry in expected.items():
            member = members[name]
            if member.size != entry["size"]:
                raise ArchiveError(f"archive member size mismatch: {name}")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ArchiveError(f"archive member cannot be read: {name}")
            digest = hashlib.sha256()
            chunks: list[bytes] = []
            retain = name in critical_paths or name in batches or name in worklists
            while chunk := extracted.read(1024 * 1024):
                digest.update(chunk)
                if retain:
                    chunks.append(chunk)
            if digest.hexdigest() != entry["sha256"]:
                raise ArchiveError(f"archive member checksum mismatch: {name}")
            if retain:
                retained[name] = b"".join(chunks)

        try:
            pointer = json.loads(retained[str(manifest["activeUniversePath"])])
            immutable = json.loads(retained[str(manifest["immutableLockPath"])])
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ArchiveError("archive lock documents are invalid") from exc
        if pointer != immutable:
            raise ArchiveError("archive active pointer and immutable lock differ")
        if pointer.get("payloadSha256") != lock_hash or pointer.get("lock", {}).get("lockId") != lock_id:
            raise ArchiveError("archive lock documents do not match manifest lock identity")
        references = active_references(pointer)
        expected_worklists = {
            "active-gemrate-ids.txt": {
                str(card["gemrateId"])
                for card in pointer["cards"]
                if isinstance(card, Mapping) and card.get("gemrateId")
            },
            "active-snk-ids.txt": {
                str(card["snkItemId"])
                for card in pointer["cards"]
                if isinstance(card, Mapping) and isinstance(card.get("snkItemId"), int)
            },
        }
        for name in worklists:
            basename = PurePosixPath(str(name)).name
            expected_ids = expected_worklists.get(basename)
            if expected_ids is None:
                raise ArchiveError(f"unsupported provider worklist: {name}")
            try:
                lines = retained[str(name)].decode("utf-8").splitlines()
            except (KeyError, UnicodeDecodeError) as exc:
                raise ArchiveError(f"provider worklist is invalid: {name}") from exc
            ids = [line.strip() for line in lines if line.strip()]
            if len(ids) != len(set(ids)) or set(ids) != expected_ids:
                raise ArchiveError(f"provider worklist does not match the active lock: {name}")
        for name in batches:
            try:
                batch = json.loads(retained[str(name)])
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ArchiveError(f"canonical batch is invalid: {name}") from exc
            observations = batch.get("observations")
            if not isinstance(observations, list) or not observations:
                raise ArchiveError(f"canonical batch has no observations: {name}")
            for observation in observations:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(observation.get("observedDate") or "")):
                    raise ArchiveError(f"canonical batch contains an undated observation: {name}")
                reference = (
                    str(observation.get("sourceCode") or "").casefold(),
                    str(observation.get("externalEntityId") or ""),
                )
                if reference not in references:
                    raise ArchiveError(f"canonical batch contains a non-active observation: {name}")
            validate_no_secrets(batch, str(name))
    return manifest


def restore_archive(path: Path, target: Path, *, overwrite: bool = False) -> Mapping[str, Any]:
    manifest = verify_archive(path)
    target = target.resolve()
    entry_names = [str(entry["path"]) for entry in manifest["entries"]]
    destinations: dict[str, Path] = {}
    for name in entry_names:
        destination = target.joinpath(*PurePosixPath(name).parts).resolve(strict=False)
        try:
            destination.relative_to(target)
        except ValueError as exc:
            raise ArchiveError(f"restore destination escapes target through a symlink: {name}") from exc
        destinations[name] = destination
    conflicts = [name for name, destination in destinations.items() if destination.exists()]
    if conflicts and not overwrite:
        raise ArchiveError(f"restore target already contains {len(conflicts)} archive file(s)")
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, mode="r:gz") as archive:
        members = {member.name: member for member in archive.getmembers()}
        for name, destination in destinations.items():
            extracted = archive.extractfile(members[name])
            if extracted is None:
                raise ArchiveError(f"archive member cannot be read: {name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                while chunk := extracted.read(1024 * 1024):
                    temporary.write(chunk)
            try:
                if destination.exists() and not overwrite:
                    raise ArchiveError(f"restore target appeared during extraction: {destination}")
                os.replace(temporary_path, destination)
            finally:
                temporary_path.unlink(missing_ok=True)
    return manifest


def summary(manifest: Mapping[str, Any], **extra: Any) -> str:
    return json.dumps(
        {
            "lockId": manifest["lockId"],
            "lockHash": manifest["lockHash"],
            "files": len(manifest["entries"]),
            "canonicalBatches": len(manifest["canonicalBatches"]),
            **extra,
        },
        sort_keys=True,
    )


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="build and verify an active-only tar.gz archive")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--overwrite", action="store_true")
    verify = commands.add_parser("verify", help="verify archive paths, checksums, and active-only contents")
    verify.add_argument("--archive", type=Path, required=True)
    restore = commands.add_parser("restore", help="verify and restore into an explicit target root")
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    restore.add_argument("--overwrite", action="store_true")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "build":
            manifest = build_archive(args.repo_root, args.output, overwrite=args.overwrite)
            print(summary(manifest, archive=str(args.output.resolve())))
        elif args.command == "verify":
            manifest = verify_archive(args.archive)
            print(summary(manifest, archive=str(args.archive.resolve()), verified=True))
        else:
            manifest = restore_archive(args.archive, args.target, overwrite=args.overwrite)
            print(summary(manifest, target=str(args.target.resolve()), restored=True))
    except (ArchiveError, OSError, tarfile.TarError) as exc:
        print(f"bootstrap archive error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
