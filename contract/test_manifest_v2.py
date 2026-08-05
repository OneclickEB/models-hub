"""Conformidad del contrato manifest v2. Corre en cualquier consumidor que
vendorice ``manifest_v2.py`` contra estos mismos fixtures.

    python3 contract/test_manifest_v2.py
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import manifest_v2  # noqa: E402
from manifest_v2 import ManifestError, validate_manifest_document, validate_release_dir  # noqa: E402

VALID_DIR = HERE / "fixtures" / "valid"
INVALID_DIR = HERE / "fixtures" / "invalid"
SCHEMA_PATH = HERE / "manifest.v2.schema.json"


class ManifestDocumentTests(unittest.TestCase):
    def test_valid_fixtures_pass(self):
        fixtures = sorted(VALID_DIR.glob("*.json"))
        self.assertGreaterEqual(len(fixtures), 4, "faltan fixtures válidos")
        for fixture in fixtures:
            with self.subTest(fixture=fixture.name):
                manifest = json.loads(fixture.read_text(encoding="utf-8"))
                validate_manifest_document(manifest)  # no debe lanzar

    def test_invalid_fixtures_are_rejected(self):
        fixtures = sorted(INVALID_DIR.glob("*.json"))
        self.assertGreaterEqual(len(fixtures), 10, "faltan fixtures inválidos")
        for fixture in fixtures:
            with self.subTest(fixture=fixture.name):
                manifest = json.loads(fixture.read_text(encoding="utf-8"))
                with self.assertRaises(ManifestError):
                    validate_manifest_document(manifest)

    def test_schema_file_is_valid_json(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertEqual(schema.get("title"), "Model Hub release manifest v2")


class ReleaseDirTests(unittest.TestCase):
    def _build_release(self, root: Path) -> dict:
        ctx = b"context-bytes"
        meta = b'{"schema_version": 2}'
        (root / "m_qnn_htp_v73_fp32.bin").write_bytes(ctx)
        (root / "m_qnn_htp_v73_fp32_meta.json").write_bytes(meta)
        manifest = {
            "schema_version": 2,
            "release_id": "m-20260804",
            "producer": "test",
            "created_at": "2026-08-04T12:00:00Z",
            "artifacts": [
                {
                    "id": "ctx",
                    "filename": "m_qnn_htp_v73_fp32.bin",
                    "role": "context",
                    "size_bytes": len(ctx),
                    "sha256": hashlib.sha256(ctx).hexdigest(),
                    "transport": {"encrypted": False},
                    "compat": {
                        "device": "qcs8550", "hexagon_arch": "v73", "qairt": "2.41",
                        "backend": "qnn_htp", "precision": "fp32", "layout": "NHWC",
                    },
                },
                {
                    "id": "meta",
                    "filename": "m_qnn_htp_v73_fp32_meta.json",
                    "role": "metadata",
                    "size_bytes": len(meta),
                    "sha256": hashlib.sha256(meta).hexdigest(),
                    "transport": {"encrypted": False},
                    "compat": None,
                },
            ],
            "sets": [
                {
                    "artifact_set": "s", "entrypoint": "ctx",
                    "members": ["ctx", "meta"], "sidecar_for": {"meta": "ctx"},
                }
            ],
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def test_valid_release_dir_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_release(root)
            validate_release_dir(root)  # no debe lanzar

    def test_corrupted_bytes_fail_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_release(root)
            (root / "m_qnn_htp_v73_fp32.bin").write_bytes(b"tampered-bytes-different-len")
            with self.assertRaises(ManifestError):
                validate_release_dir(root)

    def test_wrong_size_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self._build_release(root)
            manifest["artifacts"][0]["size_bytes"] = 999999
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ManifestError):
                validate_release_dir(root)

    def test_symlink_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._build_release(root)
            target = root / "m_qnn_htp_v73_fp32.bin"
            outside = root / "real.bin"
            target.rename(outside)
            os.symlink(outside, target)
            with self.assertRaises(ManifestError):
                validate_release_dir(root)


if __name__ == "__main__":
    unittest.main(verbosity=2)
