# Contrato de releases — `manifest.json` v2

Fuente de verdad del contrato de distribución/instalación verificable de modelos,
compartido por NV Exporter, Model Hub, Backend, SmartClient, HIS QCS8550, AI OCR y
YOLO Trainer. Reemplaza el campo ambiguo `format` del catálogo v1 por artefactos con
**rol, hash, tamaño, compatibilidad y relaciones explícitas**.

## Archivos

| Archivo | Rol |
|---|---|
| `manifest_v2.py` | **Validador puro** (stdlib, sin red). Es la autoridad. Vendorizable. |
| `manifest.v2.schema.json` | JSON Schema declarativo (estructura). No cubre reglas relacionales. |
| `fixtures/valid/*.json` · `fixtures/invalid/*.json` | Corpus de conformidad. |
| `test_manifest_v2.py` | Runner de conformidad (corre en cada consumidor). |

## El manifest

```
schema_version: 2
release_id            # inmutable, [A-Za-z0-9][A-Za-z0-9._-]{0,127}
producer              # p.ej. "nv-exporter@2.43.2"
created_at            # ISO-8601
lineage: {job_id?, source_release_id?}
artifacts[]:          # cada archivo real del release
  id, filename, role(source|weights|context|metadata|attestation|labels|report),
  size_bytes, sha256, transport{encrypted, sha256_enc?},
  compat{device, hexagon_arch, qairt, backend, precision, layout}   # obligatorio en context/weights
sets[]:               # lo instalable por target; nunca mezcla dos devices/Hexagon
  artifact_set, entrypoint, members[], requires[], sidecar_for{sidecar: principal}
signatures: []        # reservado; una firma presente se RECHAZA en esta iteración
```

### Reglas relacionales (las impone `manifest_v2.py`, no el JSON Schema)

- `entrypoint`, `members`, `requires` y `sidecar_for` referencian ids existentes.
- Ningún artefacto queda huérfano (todo id aparece en algún `set.members`).
- Un `set` no mezcla artefactos de distinto `compat.device` ni `hexagon_arch`.
- `context`/`weights` exigen `compat` con `device`, `backend`, `precision`.
- `signatures` debe venir vacío.

## Dos capas de validación

- `validate_manifest_document(dict)` — estructura + relaciones. Sin filesystem.
- `validate_release_dir(dir)` — además verifica bytes reales (sha256 y tamaño) de
  cada artefacto en el directorio del release.

## Vendoring en los consumidores

`manifest_v2.py` es de una sola pieza y sin dependencias: se **copia tal cual** al
consumidor (mismo patrón que el driver `model_hub`). Cada consumidor debe:

1. Copiar `contract/manifest_v2.py` a su árbol (p.ej. `.../contract/manifest_v2.py`).
2. Copiar `contract/fixtures/` y `contract/test_manifest_v2.py`.
3. Correr `python3 test_manifest_v2.py` en su CI para probar contra **los mismos
   fixtures**. Si el contrato cambia, se re-vendoriza desde este repo (fuente de verdad).

> Regla: el contrato se edita **solo aquí**. Un consumidor nunca diverge su copia.

## Correr la conformidad

```bash
python3 contract/test_manifest_v2.py
# validar un release en disco:
python3 contract/manifest_v2.py --release-dir /ruta/al/release
```
