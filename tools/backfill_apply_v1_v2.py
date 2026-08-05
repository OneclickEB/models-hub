#!/usr/bin/env python3
"""Backfill v1->v2 — fase 2 (aplicacion). Construye y publica los manifests.

Fase 1 (`backfill_report_v1_v2.py`) produjo la propuesta de manifest v2 por
release usando SOLO el catalogo v1. El catalogo v1 no guarda el ``size_bytes``
del archivo PLANO para pesos cifrados (guarda el del ``.enc``), asi que esta
fase descarga cada asset del release, verifica ``sha256_enc``, lo desencripta
con ``MODEL_HUB_KEY``, verifica ``sha256_plain`` y con ese tamaño real construye
el ``manifest.json`` v2 FINAL (``size_bytes``/``sha256`` del archivo plano).
Luego:

* dry-run (por defecto): escribe el manifest + los bytes planos verificados en
  ``tools/backfill/staging/<release_id>/`` y valida con ``validate_release_dir``
  (estructura Y bytes reales). NO toca GitHub.
* --publish: ademas sube ``manifest.json`` como asset del release EXISTENTE
  (mismo tag, sin release nuevo) y actualiza ``catalog.json`` en el working tree
  (agrega ``version_entry["manifest"]`` y ``artifact_id``/``role``/``target``
  por artifact). El commit del catalogo lo decide el flujo del plan (solo tras
  aprobacion explicita del humano).

NO requiere el paquete ``model_hub``: implementa el formato AES-GCM de
``ENCRYPTION.md`` con ``cryptography`` (PyPI), igual que la referencia.

Requiere: ``MODEL_HUB_KEY`` (passphrase) o ``--key-file``; ``--publish`` ademas
necesita token GitHub autenticado (``gh auth``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contract.manifest_v2 import (  # noqa: E402
    ManifestError,
    validate_manifest_document,
    validate_release_dir,
)

STAGING = ROOT / "tools" / "backfill" / "staging"
CATALOG_PATH = ROOT / "catalog.json"
REPO = "OneclickEB/models-hub"
MAGIC = b"MHENC1"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def derive_key(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    return Scrypt(salt=salt, length=32, n=2 ** 14, r=8, p=1).derive(
        passphrase.encode("utf-8")
    )


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if blob[: len(MAGIC)] != MAGIC:
        raise ValueError("no es un blob .enc (magic MHENC1 faltante)")
    salt = blob[len(MAGIC): len(MAGIC) + 16]
    nonce = blob[len(MAGIC) + 16: len(MAGIC) + 16 + 12]
    ciphertext = blob[len(MAGIC) + 16 + 12:]
    key = derive_key(passphrase, salt)
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def download(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "models-hub-backfill"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


class _V2Artifact:
    """Resultado del procesamiento de un artifact v1."""

    __slots__ = ("id", "filename", "role", "compat", "encrypted", "plain",
                 "sha256")


def map_artifact(artifact: dict[str, Any], model_id: str) -> _V2Artifact:
    """Mapea un artifact del catalogo v1 a su id/role/filename v2.

    Levanta ValueError con el motivo si no se puede asociar automaticamente.
    """
    fmt = artifact.get("format")
    name = artifact["filename"]
    real_name = name[: -len(".enc")] if (artifact.get("encrypted") and name.endswith(".enc")) else name
    compat: dict[str, Any] | None = None

    if fmt == "pt":
        aid, role = "pt", "source"
    elif fmt == "onnx":
        aid, role = "onnx", "source"
    elif fmt == "meta":
        # Un meta con device en el nombre real (ej. ..._qcs6490_...) es sidecar
        # del weights; sino es el src_meta del source set.
        m = re.search(r"qcs(\d+)", real_name)
        aid, role = (f"meta{m.group(1)}", "metadata") if m else ("src_meta", "metadata")
    elif fmt in ("meta_snpe_qcs6490_fp32", "meta_snpe_qcs8550_fp32"):
        code = "6490" if "6490" in fmt else "8550"
        aid, role = f"meta{code}", "metadata"
    elif fmt in ("dlc_6490_fp32", "dlc_8550_fp32"):
        code = fmt.split("_")[1]
        aid, role = f"dlc{code}", "weights"
        compat = {"device": f"qcs{code}", "backend": "snpe_adreno", "precision": "fp32"}
    elif fmt == "dlc":
        m = re.search(r"(qcs(\d+))", real_name)
        if m is None:
            raise ValueError(f"dlc generico sin device derivable: {name!r}")
        device, code = m.group(1), m.group(2)
        aid, role = f"dlc{code}", "weights"
        compat = {"device": device, "backend": "snpe_adreno", "precision": "fp32"}
    else:
        raise ValueError(f"format v1 desconocido: {fmt!r}")

    art = _V2Artifact()
    art.id = aid
    art.filename = real_name
    art.role = role
    art.compat = compat
    art.encrypted = bool(artifact.get("encrypted"))
    art.plain = None
    art.sha256 = artifact.get("sha256_plain")
    return art


def verify_and_decrypt(artifact: dict[str, Any], key: str, art: _V2Artifact) -> None:
    """Descarga, verifica sha256_enc, desencripta, verifica sha256_plain y
    deja ``art.plain`` listo (bytes del archivo real)."""
    raw = download(artifact["download_url"])
    if art.encrypted:
        sha_enc = artifact.get("sha256_enc")
        if sha_enc and sha256_bytes(raw) != sha_enc:
            raise ValueError(
                f"{artifact['filename']}: sha256_enc no coincide (descarga corrupta?)"
            )
        plain = decrypt_bytes(raw, key)
    else:
        plain = raw
    if art.sha256 and sha256_bytes(plain) != art.sha256:
        raise ValueError(
            f"{artifact['filename']}: sha256_plain no coincide (clave incorrecta?)"
        )
    art.plain = plain
    if Path(art.filename).name != art.filename:
        raise ValueError(f"{art.filename}: filename con ruta (no se puede publicar)")


def tag_from_url(download_url: str) -> str:
    """El tag real del release apuntado por el download_url. En v1 rara vez no
    coincide con `model_id-version` (releases históricos publicados antes de
    renombrar el modelo): el fuente de verdad es la URL del asset."""
    m = re.search(r"/releases/download/([^/]+)/", download_url)
    if m is None:
        raise ValueError(f"no se puede derivar el tag de: {download_url}")
    return m.group(1)


def build_manifest(
    artifacts: list[dict[str, Any]], model_id: str, version_id: str, released_at: str,
    key: str, out_dir: Path, release_id: str,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Construye y valida el manifest v2 para una version. Descarga todo y
    escribe los bytes planos verificados en ``out_dir`` (para que
    ``validate_release_dir`` pueda validar estructura Y bytes)."""
    problems: list[str] = []
    mapped: list[tuple[_V2Artifact, bytes]] = []
    for artifact in artifacts:
        try:
            art = map_artifact(artifact, model_id)
        except ValueError as exc:
            problems.append(str(exc))
            continue
        try:
            verify_and_decrypt(artifact, key, art)
        except Exception as exc:  # noqa: BLE001 - reportar todos los problemas
            problems.append(str(exc))
            continue
        mapped.append((art, art.plain))

    if problems:
        return None, problems

    if not mapped:
        return None, ["sin artifacts mapeados"]

    from collections import Counter
    dupes = [i for i, n in Counter(a.id for a, _ in mapped).items() if n > 1]
    if dupes:
        return None, [f"ids duplicados tras el mapeo: {sorted(dupes)}"]

    artifacts_v2 = []
    for art, plain in mapped:
        entry: dict[str, Any] = {
            "id": art.id,
            "filename": art.filename,
            "role": art.role,
            "size_bytes": len(plain),
            "sha256": sha256_bytes(plain),
            "transport": {"encrypted": False},
            "compat": art.compat,
        }
        artifacts_v2.append(entry)

    sets = build_sets(artifacts_v2)
    manifest = {
        "schema_version": 2,
        "release_id": release_id,
        "producer": "models-hub-backfill@1.0",
        "created_at": released_at or _now_iso(),
        "lineage": {"job_id": None, "source_release_id": release_id},
        "artifacts": artifacts_v2,
        "sets": sets,
        "signatures": [],
    }
    try:
        manifest = validate_manifest_document(manifest)
    except ManifestError as exc:
        return None, [f"manifest propuesto invalido: {exc}"]

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        for art, plain in mapped:
            (out_dir / art.filename).write_bytes(plain)
    return manifest, []


def _now_iso() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_sets(artifacts_v2: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {a["id"]: a for a in artifacts_v2}
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

    for device in sorted({
        a["compat"]["device"] for a in artifacts_v2 if a["role"] == "weights"
    }):
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


def publish_manifest(release_id: str, manifest_path: Path) -> str:
    """Sube manifest.json al release EXISTENTE (mismo tag). Devuelve la URL."""
    import subprocess

    r = subprocess.run(
        ["gh", "release", "upload", release_id, str(manifest_path),
         "--clobber", "-R", REPO],
        capture_output=True, text=True, timeout=300,
    )
    if r.returncode != 0:
        raise RuntimeError(f"gh release upload {release_id}: {r.stderr.strip()}")
    return f"https://github.com/{REPO}/releases/download/{release_id}/manifest.json"


def stamp_catalog(catalog: dict, model_id: str, version_id: str,
                  manifest: dict, manifest_url: str) -> bool:
    """Actualiza in-place el version_entry del release: agrega el bloque
    ``manifest`` y ``artifact_id``/``role``/``target`` por artifact. Devuelve
    True si algo cambio."""
    model = next((m for m in catalog["models"] if m["model_id"] == model_id), None)
    if model is None:
        raise ValueError(f"model_id {model_id} no encontrado en el catalogo")
    version_entry = next((v for v in model["versions"] if v["version"] == version_id), None)
    if version_entry is None:
        raise ValueError(f"version {version_id} no encontrada para {model_id}")

    changed = False
    if "manifest" not in version_entry:
        version_entry["manifest"] = {
            "filename": "manifest.json",
            "sha256_plain": sha256_bytes(json.dumps(manifest, ensure_ascii=False).encode()),
            "download_url": manifest_url,
            "release_id": manifest["release_id"],
        }
        changed = True

    by_plain = {
        art["filename"]: art
        for art in manifest["artifacts"]
    }
    for entry in version_entry.get("artifacts", []):
        plain_name = (entry["filename"][: -len(".enc")]
                      if entry["filename"].endswith(".enc") else entry["filename"])
        m_art = by_plain.get(plain_name)
        if m_art is None:
            continue
        if "artifact_id" not in entry:
            entry["artifact_id"] = m_art["id"]
            changed = True
        if "role" not in entry:
            entry["role"] = m_art["role"]
            changed = True
        device = (m_art.get("compat") or {}).get("device")
        if device and "target" not in entry:
            entry["target"] = device
            changed = True
    return changed


def run(args) -> int:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    failures: list[str] = []
    total, ok = 0, 0

    for model in catalog["models"]:
        model_id = model["model_id"]
        if args.model and args.model not in model_id:
            continue
        for version in model.get("versions", []):
            artifacts = version.get("artifacts", [])
            if not artifacts:
                continue
            version_id = version["version"]
            release_id = tag_from_url(artifacts[0]["download_url"])
            total += 1
            rel_dir = STAGING / release_id
            manifest, problems = build_manifest(
                artifacts, model_id, version_id,
                version.get("released_at", ""), args.key, rel_dir, release_id,
            )
            if manifest is None:
                failures.append(f"{release_id}: {'; '.join(problems)}")
                print(f"  [FAIL] {release_id}: {'; '.join(problems)}")
                continue

            try:
                validate_release_dir(rel_dir)
            except ManifestError as exc:
                failures.append(f"{release_id}: validate_release_dir: {exc}")
                print(f"  [FAIL] {release_id}: validate_release_dir: {exc}")
                continue

            if args.publish:
                manifest_path = rel_dir / "manifest.json"
                try:
                    url = publish_manifest(release_id, manifest_path)
                    stamp_catalog(catalog, model_id, version_id, manifest, url)
                    print(f"  [PUB] {release_id} -> {url}")
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{release_id}: publish: {exc}")
                    print(f"  [FAIL] {release_id}: publish: {exc}")
                    continue
            else:
                print(f"  [OK] {release_id} -> manifest + bytes planos validados ({rel_dir})")
            ok += 1

    if args.publish:
        from collections import OrderedDict
        CATALOG_PATH.write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"\ncatalog.json actualizado (sin commit): {CATALOG_PATH}")

    print(f"\nReleases: {total} · OK: {ok} · FAILED: {len(failures)}")
    if failures:
        print("\nFallos:")
        for f in failures:
            print(f"  - {f}")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--publish", action="store_true",
                    help="aplicar: validar y luego publicar (dry-run por defecto)")
    ap.add_argument("--key-file", help="passphrase en archivo (o MODEL_HUB_KEY env)")
    ap.add_argument("--model", default=None, help="filtrar por substring de model_id")
    args = ap.parse_args()

    if args.key_file:
        args.key = Path(args.key_file).read_text().strip()
    else:
        candidate = os.environ.get("MODEL_HUB_KEY") or os.environ.get("MODEL_HUB_KEY_ENV")
        if candidate and Path(candidate).exists():
            args.key = Path(candidate).read_text().strip()
        elif candidate:
            args.key = candidate
        else:
            ap.error("requiere MODEL_HUB_KEY o --key-file (passphrase .enc)")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())