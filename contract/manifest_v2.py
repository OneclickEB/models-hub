"""Contrato verificable de releases (``manifest.json`` schema v2) del Model Hub.

Fuente de verdad del contrato compartido entre NV Exporter, Model Hub, Backend,
SmartClient, HIS QCS8550, AI OCR y YOLO Trainer. Es un validador **puro**: sin red
y sin dependencias fuera de la stdlib, para poder vendorizarse (copiarse tal cual)
en dispositivos edge y en cada consumidor, y probarse contra los mismos fixtures.

El manifest es la unidad de distribucion e instalacion. Reemplaza al campo ambiguo
``format`` del catalogo v1 por artefactos con rol, hash, tamano, compatibilidad y
relaciones explicitas. Un conjunto (``set``) es lo instalable por target: nunca
mezcla artefactos de dos devices o arquitecturas Hexagon.

Dos capas de validacion:

* :func:`validate_manifest_document` — estructura, ids, roles, relaciones y
  coherencia de compatibilidad. No toca el filesystem.
* :func:`validate_release_dir` — ademas verifica los bytes reales (sha256 y
  tamano) de cada artefacto declarado en el directorio del release.

La autenticidad criptografica (firmas) queda reservada para una iteracion
posterior: ``signatures`` debe venir vacio; una firma presente se rechaza en vez
de aceptarse sin verificar.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
MANIFEST_NAME = "manifest.json"

ROLES = {
    "source",       # .pt / .onnx hardware-agnostico
    "weights",      # DLC SNPE target-specific
    "context",      # contexto QNN/HTP target-specific
    "metadata",     # sidecar _meta.json
    "attestation",  # attestation.json de compilacion QNN
    "labels",       # labels.txt
    "report",       # export_report.json u otros reportes
}
# Roles cuyo artefacto es target-specific y exige un bloque ``compat`` con device.
COMPAT_REQUIRED_ROLES = {"weights", "context"}

_TOP_KEYS = {
    "schema_version", "release_id", "producer", "created_at",
    "lineage", "model", "artifacts", "sets", "signatures",
}
_REQUIRED_TOP_KEYS = {
    "schema_version", "release_id", "producer", "created_at", "artifacts", "sets",
}
_MODEL_KEYS = {
    "model_id", "model_family", "model_type", "task", "source_format",
    "model_created_at", "metadata_created_at", "metadata_updated_at",
}
_MODEL_REQUIRED_KEYS = {"model_family", "model_type", "task"}
_ARTIFACT_KEYS = {
    "id", "filename", "role", "size_bytes", "sha256", "transport", "compat",
    "artifact_format", "runtime_family", "target_technical", "qnn_htp_arch",
    "soc_hint", "model_created_at", "exported_at", "metadata_created_at",
    "metadata_updated_at",
}
_REQUIRED_ARTIFACT_KEYS = {"id", "filename", "role", "size_bytes", "sha256"}
_SET_KEYS = {"artifact_set", "entrypoint", "members", "requires", "sidecar_for"}
_REQUIRED_SET_KEYS = {"artifact_set", "entrypoint", "members"}
_LINEAGE_KEYS = {"job_id", "source_release_id"}
_TRANSPORT_KEYS = {"encrypted", "sha256_enc"}
_COMPAT_KEYS = {"device", "hexagon_arch", "qairt", "backend", "precision", "layout"}
_COMPAT_REQUIRED_KEYS = {"device", "backend", "precision"}
_TASKS = {"detect", "ocr", "pose", "segment", "depth"}
_RUNTIME_ARTIFACT_ROLES = {"source", "weights", "context"}
_KNOWN_ARTIFACT_FORMATS = {
    "pytorch_pt": ("source", "pytorch"),
    "onnx": ("source", "onnxruntime"),
    "snpe_dlc": ("weights", "snpe"),
    "qnn_context_bin": ("context", "qnn_net_run_worker"),
    "onnx_qnn_context": ("context", "onnxruntime_qnn"),
}

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SAFE_RELEASE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_ISO8601 = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


class ManifestError(ValueError):
    """El manifest no cumple el contrato v2 del Model Hub."""


def sha256_file(path: str | Path) -> str:
    """SHA-256 por streaming (los contextos pueden ser grandes)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ManifestError(message)


def _require_known_keys(obj: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = set(obj) - allowed
    _require(not unknown, f"{label}: claves desconocidas {sorted(unknown)}")


def _require_iso_timestamp(value: Any, label: str) -> None:
    _require(
        isinstance(value, str) and bool(_ISO8601.match(value)),
        f"{label} debe ser timestamp ISO-8601",
    )


def _require_optional_safe_string(obj: dict[str, Any], key: str, label: str) -> None:
    if key not in obj:
        return
    value = obj[key]
    _require(
        isinstance(value, str) and bool(_SAFE_ID.fullmatch(value)),
        f"{label}.{key} debe ser string seguro no vacio",
    )


def _validate_model(model: Any) -> None:
    _require(isinstance(model, dict), "manifest.model debe ser objeto")
    _require_known_keys(model, _MODEL_KEYS, "manifest.model")
    missing = _MODEL_REQUIRED_KEYS - set(model)
    _require(not missing, f"manifest.model: faltan {sorted(missing)}")
    for key in ("model_family", "model_type", "source_format"):
        _require_optional_safe_string(model, key, "manifest.model")
    if "model_id" in model:
        _require(
            isinstance(model["model_id"], str) and bool(_SAFE_RELEASE_ID.fullmatch(model["model_id"])),
            "manifest.model.model_id invalido",
        )
    task = model["task"]
    _require(task in _TASKS, f"manifest.model.task invalido {task!r}")
    for key in ("model_created_at", "metadata_created_at", "metadata_updated_at"):
        if key in model:
            _require_iso_timestamp(model[key], f"manifest.model.{key}")


def _validate_compat(compat: Any, artifact_id: str) -> None:
    _require(isinstance(compat, dict), f"artifact {artifact_id}: compat debe ser objeto")
    _require_known_keys(compat, _COMPAT_KEYS, f"artifact {artifact_id}.compat")
    missing = _COMPAT_REQUIRED_KEYS - set(compat)
    _require(not missing, f"artifact {artifact_id}.compat: faltan {sorted(missing)}")
    for key in ("device", "backend", "precision"):
        value = compat.get(key)
        _require(
            isinstance(value, str) and value != "",
            f"artifact {artifact_id}.compat.{key} debe ser string no vacio",
        )
    for key in ("hexagon_arch", "qairt", "layout"):
        value = compat.get(key, None)
        _require(
            value is None or (isinstance(value, str) and value != ""),
            f"artifact {artifact_id}.compat.{key} debe ser string no vacio o null",
        )


def _validate_artifact(artifact: Any) -> dict[str, Any]:
    _require(isinstance(artifact, dict), "artifacts[]: cada artefacto debe ser objeto")
    _require_known_keys(artifact, _ARTIFACT_KEYS, "artifact")
    missing = _REQUIRED_ARTIFACT_KEYS - set(artifact)
    _require(not missing, f"artifact: faltan {sorted(missing)}")

    artifact_id = artifact["id"]
    _require(
        isinstance(artifact_id, str) and bool(_SAFE_ID.fullmatch(artifact_id)),
        f"artifact.id invalido: {artifact_id!r}",
    )
    filename = artifact["filename"]
    _require(
        isinstance(filename, str) and filename != "" and Path(filename).name == filename,
        f"artifact {artifact_id}: filename debe ser un nombre simple sin ruta",
    )
    role = artifact["role"]
    _require(role in ROLES, f"artifact {artifact_id}: role invalido {role!r}")
    size_bytes = artifact["size_bytes"]
    _require(
        isinstance(size_bytes, int) and not isinstance(size_bytes, bool) and size_bytes >= 0,
        f"artifact {artifact_id}: size_bytes debe ser entero >= 0",
    )
    sha256 = artifact["sha256"]
    _require(
        isinstance(sha256, str) and bool(_SHA256.fullmatch(sha256)),
        f"artifact {artifact_id}: sha256 invalido",
    )

    transport = artifact.get("transport", {"encrypted": False})
    _require(isinstance(transport, dict), f"artifact {artifact_id}: transport debe ser objeto")
    _require_known_keys(transport, _TRANSPORT_KEYS, f"artifact {artifact_id}.transport")
    encrypted = transport.get("encrypted", False)
    _require(isinstance(encrypted, bool), f"artifact {artifact_id}.transport.encrypted debe ser bool")
    sha256_enc = transport.get("sha256_enc", None)
    if encrypted:
        _require(
            isinstance(sha256_enc, str) and bool(_SHA256.fullmatch(sha256_enc)),
            f"artifact {artifact_id}: cifrado requiere transport.sha256_enc valido",
        )
    else:
        _require(
            sha256_enc is None,
            f"artifact {artifact_id}: sha256_enc solo aplica a artefactos cifrados",
        )

    compat = artifact.get("compat", None)
    if role in COMPAT_REQUIRED_ROLES:
        _validate_compat(compat, artifact_id)
    else:
        _require(
            compat is None,
            f"artifact {artifact_id}: role {role} no admite compat (debe ser null/ausente)",
        )

    has_artifact_format = "artifact_format" in artifact
    has_runtime_family = "runtime_family" in artifact
    if role in _RUNTIME_ARTIFACT_ROLES:
        _require(
            has_artifact_format == has_runtime_family,
            f"artifact {artifact_id}: artifact_format y runtime_family deben declararse juntos",
        )
    for key in ("artifact_format", "runtime_family", "target_technical", "qnn_htp_arch", "soc_hint"):
        _require_optional_safe_string(artifact, key, f"artifact {artifact_id}")
    if "qnn_htp_arch" in artifact:
        qnn_htp_arch = artifact["qnn_htp_arch"]
        _require(
            qnn_htp_arch.startswith("v") and qnn_htp_arch[1:].isdigit(),
            f"artifact {artifact_id}: qnn_htp_arch invalido",
        )
    artifact_format = artifact.get("artifact_format")
    runtime_family = artifact.get("runtime_family")
    if artifact_format in _KNOWN_ARTIFACT_FORMATS:
        expected_role, expected_runtime = _KNOWN_ARTIFACT_FORMATS[artifact_format]
        _require(
            role == expected_role,
            f"artifact {artifact_id}: artifact_format {artifact_format!r} requiere role {expected_role!r}",
        )
        _require(
            runtime_family == expected_runtime,
            f"artifact {artifact_id}: artifact_format {artifact_format!r} requiere runtime_family {expected_runtime!r}",
        )
    for key in ("model_created_at", "exported_at", "metadata_created_at", "metadata_updated_at"):
        if key in artifact:
            _require_iso_timestamp(artifact[key], f"artifact {artifact_id}.{key}")
    return artifact


def _validate_set(
    dev_set: Any, artifacts_by_id: dict[str, dict[str, Any]]
) -> tuple[str, list[str]]:
    _require(isinstance(dev_set, dict), "sets[]: cada set debe ser objeto")
    _require_known_keys(dev_set, _SET_KEYS, "set")
    missing = _REQUIRED_SET_KEYS - set(dev_set)
    _require(not missing, f"set: faltan {sorted(missing)}")

    set_id = dev_set["artifact_set"]
    _require(
        isinstance(set_id, str) and bool(_SAFE_ID.fullmatch(set_id)),
        f"set.artifact_set invalido: {set_id!r}",
    )
    members = dev_set["members"]
    _require(
        isinstance(members, list) and len(members) >= 1,
        f"set {set_id}: members debe ser lista no vacia",
    )
    for member in members:
        _require(member in artifacts_by_id, f"set {set_id}: member desconocido {member!r}")
    _require(len(set(members)) == len(members), f"set {set_id}: members duplicados")

    entrypoint = dev_set["entrypoint"]
    _require(entrypoint in members, f"set {set_id}: entrypoint {entrypoint!r} no esta en members")

    requires = dev_set.get("requires", [])
    _require(isinstance(requires, list), f"set {set_id}: requires debe ser lista")
    for dep in requires:
        _require(dep in artifacts_by_id, f"set {set_id}: requires desconocido {dep!r}")

    sidecar_for = dev_set.get("sidecar_for", {})
    _require(isinstance(sidecar_for, dict), f"set {set_id}: sidecar_for debe ser objeto")
    for sidecar, principal in sidecar_for.items():
        _require(sidecar in members, f"set {set_id}: sidecar {sidecar!r} no esta en members")
        _require(principal in members, f"set {set_id}: principal {principal!r} no esta en members")
        _require(sidecar != principal, f"set {set_id}: sidecar_for no puede apuntarse a si mismo")
    metadata_without_sidecar = [
        member
        for member in members
        if artifacts_by_id[member].get("role") == "metadata" and member not in sidecar_for
    ]
    _require(
        not metadata_without_sidecar,
        f"set {set_id}: metadata sin sidecar_for {metadata_without_sidecar}",
    )

    # Coherencia target: un set no mezcla devices ni arquitecturas Hexagon.
    devices = set()
    arches = set()
    for member in members:
        compat = artifacts_by_id[member].get("compat")
        if isinstance(compat, dict):
            devices.add(compat.get("device"))
            arch = compat.get("hexagon_arch")
            if arch is not None:
                arches.add(arch)
    _require(len(devices) <= 1, f"set {set_id}: mezcla artefactos de devices {sorted(devices)}")
    _require(len(arches) <= 1, f"set {set_id}: mezcla arquitecturas Hexagon {sorted(arches)}")
    return set_id, members


def validate_manifest_document(manifest: Any) -> dict[str, Any]:
    """Valida estructura, ids, roles, relaciones y compatibilidad. Sin filesystem."""
    _require(isinstance(manifest, dict), "manifest debe ser un objeto JSON")
    _require_known_keys(manifest, _TOP_KEYS, "manifest")
    missing = _REQUIRED_TOP_KEYS - set(manifest)
    _require(not missing, f"manifest: faltan {sorted(missing)}")

    _require(manifest["schema_version"] == SCHEMA_VERSION, "manifest: schema_version debe ser 2")
    release_id = manifest["release_id"]
    _require(
        isinstance(release_id, str) and bool(_SAFE_RELEASE_ID.fullmatch(release_id)),
        f"manifest: release_id invalido {release_id!r}",
    )
    _require(
        isinstance(manifest["producer"], str) and manifest["producer"] != "",
        "manifest: producer debe ser string no vacio",
    )
    _require(
        isinstance(manifest["created_at"], str) and bool(_ISO8601.match(manifest["created_at"])),
        "manifest: created_at debe ser timestamp ISO-8601",
    )

    if "model" in manifest:
        _validate_model(manifest["model"])

    lineage = manifest.get("lineage", {})
    _require(isinstance(lineage, dict), "manifest: lineage debe ser objeto")
    _require_known_keys(lineage, _LINEAGE_KEYS, "manifest.lineage")
    job_id = lineage.get("job_id", None)
    if job_id is not None:
        try:
            uuid.UUID(str(job_id))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ManifestError("manifest.lineage.job_id invalido") from exc
    source_release_id = lineage.get("source_release_id", None)
    _require(
        source_release_id is None
        or (isinstance(source_release_id, str) and bool(_SAFE_RELEASE_ID.fullmatch(source_release_id))),
        "manifest.lineage.source_release_id invalido",
    )

    artifacts = manifest["artifacts"]
    _require(isinstance(artifacts, list) and len(artifacts) >= 1, "manifest: artifacts no vacio")
    artifacts_by_id: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        validated = _validate_artifact(artifact)
        artifact_id = validated["id"]
        _require(artifact_id not in artifacts_by_id, f"artifact.id duplicado: {artifact_id}")
        artifacts_by_id[artifact_id] = validated
    _require(
        len({artifact["filename"] for artifact in artifacts}) == len(artifacts),
        "manifest: filenames duplicados entre artefactos",
    )

    sets = manifest["sets"]
    _require(isinstance(sets, list) and len(sets) >= 1, "manifest: sets no vacio")
    referenced: set[str] = set()
    seen_sets: set[str] = set()
    for dev_set in sets:
        set_id, members = _validate_set(dev_set, artifacts_by_id)
        _require(set_id not in seen_sets, f"artifact_set duplicado: {set_id}")
        seen_sets.add(set_id)
        referenced.update(members)
    orphans = set(artifacts_by_id) - referenced
    _require(not orphans, f"manifest: artefactos huerfanos sin set {sorted(orphans)}")

    signatures = manifest.get("signatures", [])
    _require(isinstance(signatures, list), "manifest: signatures debe ser lista")
    _require(
        len(signatures) == 0,
        "manifest: firmas no aceptadas en esta iteracion (signatures debe venir vacio)",
    )
    return manifest


def validate_release_dir(release_dir: str | Path) -> dict[str, Any]:
    """Valida el documento y ademas los bytes reales de cada artefacto."""
    root = Path(release_dir)
    _require(root.is_dir(), f"directorio de release no encontrado: {root}")
    manifest_path = root / MANIFEST_NAME
    _require(manifest_path.is_file(), f"{MANIFEST_NAME} no encontrado en {root}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"{MANIFEST_NAME} invalido: {exc}") from exc

    validate_manifest_document(manifest)
    for artifact in manifest["artifacts"]:
        path = root / artifact["filename"]
        _require(
            path.is_file() and not path.is_symlink(),
            f"artifact {artifact['id']}: archivo faltante o symlink: {artifact['filename']}",
        )
        actual_size = path.stat().st_size
        _require(
            actual_size == artifact["size_bytes"],
            f"artifact {artifact['id']}: size {actual_size} != {artifact['size_bytes']}",
        )
        transport = artifact.get("transport", {"encrypted": False})
        digest = sha256_file(path)
        if transport.get("encrypted", False):
            expected = transport["sha256_enc"]
            _require(digest == expected, f"artifact {artifact['id']}: sha256_enc no coincide")
        else:
            _require(digest == artifact["sha256"], f"artifact {artifact['id']}: sha256 no coincide")
    return manifest


def _main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", required=True)
    args = parser.parse_args(argv)
    try:
        manifest = validate_release_dir(args.release_dir)
    except ManifestError as exc:
        print(f"ERROR: {exc}")
        return 2
    print(f"OK: {manifest['release_id']} ({len(manifest['artifacts'])} artefactos)")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
