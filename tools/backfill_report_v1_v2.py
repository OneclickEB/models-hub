#!/usr/bin/env python3
"""Backfill v1->v2: reporte de asociabilidad de los releases v1 existentes.

Fase 1 (este script, SIN red ni key): lee ``catalog.json`` v1 y, por cada
modelo/version, reconstruye la propuesta de ``manifest.json`` v2 a partir de
los datos ya presentes en el catalogo (``filename``, ``sha256_plain``,
``sha256_enc``, ``format``, ``encrypted``). Clasifica cada release en:

* ``automatic`` — todo el mapeo v1->v2 es derivable de forma deterministica y
  el manifest propuesto valida contra el contrato (solo releases sin bytes
  cifrados; hoy ninguno, todos los pesos v1 van cifrados).
* ``verify``    — asociable, pero requiere la fase 2 (descargar + desencriptar
  con la key para confirmar ``sha256_plain`` y conocer el ``size_bytes`` del
  archivo PLANO, que el catalogo v1 no guarda para artifacts cifrados).
* ``blocked``   — no se puede asociar sin intervencion humana (motivo exacto).

La fase 2 (aplicacion) es un script separado que usa la key y red para subir
``manifest.json`` al release EXISTENTE y actualizar ``catalog.json`` in-place.

Solo stdlib + el contrato vendorizado de este mismo repo.
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contract.manifest_v2 import (  # noqa: E402
    ManifestError,
    validate_manifest_document,
)

CATALOG_PATH = ROOT / "catalog.json"
OUTPUT_DIR = ROOT / "tools" / "reports"

DEVICE_ALIASES = {"6490": "qcs6490", "8550": "qcs8550"}
BACKEND = "snpe_adreno"
PRECISION = "fp32"

# mapeo format v1 -> (id, role, compat) determinista
_ROLE_BY_FORMAT = {
    "pt": ("pt", "source", None),
    "onnx": ("onnx", "source", None),
    "meta": ("src_meta", "metadata", None),
}
_META_DEVICE = {"meta_snpe_qcs6490_fp32": ("meta6490", "qcs6490"),
                "meta_snpe_qcs8550_fp32": ("meta8550", "qcs8550")}
_DLC_DEVICE = {"dlc_6490_fp32": "qcs6490", "dlc_8550_fp32": "qcs8550"}


def _plain_filename(artifact: dict[str, Any]) -> str:
    name = artifact["filename"]
    if artifact.get("encrypted") and name.endswith(".enc"):
        return name[: -len(".enc")]
    return name


def _device_from_model_id(model_id: str) -> str | None:
    for code, device in DEVICE_ALIASES.items():
        if code in model_id or device in model_id:
            return device
    return None


def _device_from_filename(filename: str) -> str | None:
    text = filename.lower()
    for code, device in DEVICE_ALIASES.items():
        if device in text or f"snpe_{code}" in text or f"_{code}_" in text:
            return device
    return None


def map_artifact(
    artifact: dict[str, Any], model_id: str
) -> tuple[dict[str, Any], list[str]]:
    """Devuelve (entrada de artifact v2 o None, razones de bloqueo)."""
    fmt = artifact.get("format")
    problems: list[str] = []
    if not fmt:
        problems.append(f"artifact '{artifact['filename']}' sin format")

    sha256 = artifact.get("sha256_plain")
    if not sha256 or not re.fullmatch(r"[a-f0-9]{64}", str(sha256)):
        problems.append(f"artifact '{artifact['filename']}': sha256_plain invalido")

    encrypted = bool(artifact.get("encrypted"))
    sha_enc = artifact.get("sha256_enc")
    if encrypted and (not sha_enc or not re.fullmatch(r"[a-f0-9]{64}", str(sha_enc))):
        problems.append(f"artifact '{artifact['filename']}': cifrado sin sha256_enc valido")

    filename = _plain_filename(artifact)
    if Path(filename).name != filename:
        problems.append(f"artifact '{artifact['filename']}': filename con ruta")

    if problems:
        return None, problems

    entry: dict[str, Any] = {
        "id": None,
        "filename": filename,
        "role": None,
        "sha256": sha256,
        "transport": {"encrypted": encrypted},
    }
    if encrypted:
        entry["transport"]["sha256_enc"] = sha_enc

    compat: dict[str, Any] | None = None
    if fmt in _ROLE_BY_FORMAT:
        entry["id"], entry["role"], compat = _ROLE_BY_FORMAT[fmt]
        # meta plano que en realidad es sidecar de un weights (lleva el device
        # en el nombre real, ej. precintos-s_snpe_qcs6490_fp32_meta.json):
        # se re-clasifica como metadata del device, no como src_meta.
        if fmt == "meta" and entry["id"] == "src_meta":
            device = _device_from_filename(filename)
            if device is not None:
                entry["id"] = f"meta{device.removeprefix('qcs')}"
    elif fmt in _META_DEVICE:
        entry["id"], device = _META_DEVICE[fmt]
        entry["role"] = "metadata"
    elif fmt in _DLC_DEVICE:
        code = fmt.split("_")[1]
        entry["id"] = f"dlc{code}"
        entry["role"] = "weights"
        compat = {"device": _DLC_DEVICE[fmt], "backend": BACKEND, "precision": PRECISION}
    elif fmt == "dlc":
        device = _device_from_model_id(model_id)
        if device is None:
            problems.append(
                f"artifact '{artifact['filename']}': format 'dlc' sin device derivable"
            )
            return None, problems
        entry["id"] = f"dlc{device.removeprefix('qcs')}"
        entry["role"] = "weights"
        compat = {"device": device, "backend": BACKEND, "precision": PRECISION}
    else:
        problems.append(f"format v1 desconocido: {fmt!r}")

    if problems:
        return None, problems

    entry["compat"] = compat
    entry["size_bytes"] = None  # fase 2: solo con descarga/desencriptado
    return entry, []


def build_sets(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {e["id"]: e for e in entries}
    sets: list[dict[str, Any]] = []

    source_members = [i for i in ("pt", "onnx") if i in by_id]
    if source_members:
        entrypoint = "pt" if "pt" in by_id else "onnx"
        sidecar_for = {}
        if "src_meta" in by_id:
            source_members.append("src_meta")
            sidecar_for["src_meta"] = entrypoint
        sets.append({
            "artifact_set": "source",
            "entrypoint": entrypoint,
            "members": source_members,
            "requires": [],
            "sidecar_for": sidecar_for,
        })

    for device in sorted({e["compat"]["device"] for e in by_id.values() if e["role"] == "weights"}):
        code = device.removeprefix("qcs")
        principal = f"dlc{code}"
        meta_id = f"meta{code}"
        members = [principal]
        sidecar_for = {}
        if meta_id in by_id:
            members.append(meta_id)
            sidecar_for[meta_id] = principal
        sets.append({
            "artifact_set": f"snpe-{device}",
            "entrypoint": principal,
            "members": members,
            "requires": [],
            "sidecar_for": sidecar_for,
        })
    return sets


def build_proposal(
    model_id: str, version: str, artifacts: list[dict[str, Any]], released_at: str
) -> dict[str, Any]:
    entry = {
        "model_id": model_id,
        "version": version,
        "tag": f"{model_id}-{version}",
        "release_id": f"{model_id}-{version}",
        "created_at": released_at,
        "status": "automatic",
        "reasons": [],
        "pending_artifacts": [],
        "artifacts": [],
        "sets": [],
        "proposed_manifest": None,
    }
    mapped: list[dict[str, Any]] = []
    for artifact in artifacts:
        v2_entry, problems = map_artifact(artifact, model_id)
        if v2_entry is None:
            entry["status"] = "blocked"
            entry["reasons"].extend(problems)
            continue
        if v2_entry["size_bytes"] is None:
            entry["status"] = "verify"
            entry["pending_artifacts"].append(v2_entry["id"])
            v2_entry["size_bytes"] = artifact.get("size_bytes")
        mapped.append(v2_entry)
        entry["artifacts"].append({
            "v1_format": artifact.get("format"),
            "v2_id": v2_entry["id"],
            "role": v2_entry["role"],
            "filename_plain": v2_entry["filename"],
            "encrypted": v2_entry["transport"]["encrypted"],
        })

    ids = [e["id"] for e in mapped]
    dupes = [i for i, n in Counter(ids).items() if n > 1]
    if dupes:
        entry["status"] = "blocked"
        entry["reasons"].append(f"ids duplicados tras el mapeo: {sorted(dupes)}")

    if entry["status"] != "blocked":
        entry["sets"] = build_sets(mapped)
        manifest = {
            "schema_version": 2,
            "release_id": entry["release_id"],
            "producer": "models-hub-backfill@1.0",
            "created_at": released_at,
            "lineage": {"job_id": None, "source_release_id": entry["release_id"]},
            "artifacts": mapped,
            "sets": entry["sets"],
            "signatures": [],
        }
        try:
            validate_manifest_document(manifest)
            if entry["status"] == "automatic":
                entry["proposed_manifest"] = manifest
        except ManifestError as exc:
            entry["status"] = "blocked"
            entry["reasons"].append(f"manifest propuesto invalido: {exc}")
            entry["proposed_manifest"] = None
    return entry


def main() -> int:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    releases = []
    for model in catalog["models"]:
        for version in model.get("versions", []):
            releases.append(build_proposal(
                model["model_id"],
                version["version"],
                version.get("artifacts", []),
                version.get("released_at", ""),
            ))

    statuses = Counter(r["status"] for r in releases)
    report = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "catalog_version": catalog.get("catalog_version"),
        "releases_total": len(releases),
        "releases_by_status": dict(statuses),
        "releases": releases,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"backfill-report-{report['generated_at'][:10]}.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    md = [
        f"# Backfill v1->v2 — reporte de asociabilidad",
        f"Generado: {report['generated_at']} · catálogo v{report['catalog_version']}",
        "",
        f"Releases totales: {report['releases_total']}",
        f"- `automatic`: {statuses.get('automatic', 0)}",
        f"- `verify` (asociable, requiere fase 2 con key): {statuses.get('verify', 0)}",
        f"- `blocked` (requiere intervención humana): {statuses.get('blocked', 0)}",
        "",
        "| tag | status | motivo |",
        "|---|---|---|",
    ]
    for r in releases:
        reason = "; ".join(r["reasons"]) or (
            "verificar bytes en fase 2: " + ", ".join(r["pending_artifacts"])
        ) or "ok"
        md.append(f"| `{r['tag']}` | {r['status']} | {reason} |")
    md_path = out_path.with_suffix(".md")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"Reporte JSON: {out_path}")
    print(f"Reporte MD:   {md_path}")
    print(f"Resumen: {report['releases_by_status']}")
    for r in releases:
        if r["status"] == "blocked":
            print(f"  BLOCKED {r['tag']}: {'; '.join(r['reasons'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
