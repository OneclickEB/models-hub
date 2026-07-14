# Formato de cifrado — Model Hub

Los archivos de modelo (`*.pt.enc`, `*.onnx.enc`, etc.) publicados en este
repositorio están cifrados con **AES-256-GCM**. El repositorio es público a
propósito — publicar el método no compromete la seguridad, porque la
seguridad está en la **clave**, no en ocultar el algoritmo. Sin la clave
correcta, un archivo descargado de este hub es un blob de bytes inútil.

## Formato de archivo `.enc`

```
┌─────────────┬──────────┬───────────┬──────────────────────────────┐
│ magic (6B)  │ salt(16B)│ nonce(12B)│ ciphertext + tag GCM (16B)    │
│ "MHENC1"    │ random   │ random    │ AES-256-GCM                   │
└─────────────┴──────────┴───────────┴──────────────────────────────┘
```

- **`magic`**: 6 bytes ASCII `MHENC1` — identifica el formato/versión.
- **`salt`**: 16 bytes aleatorios, distintos en cada archivo (aunque se
  reutilice la misma passphrase, cada archivo cifrado es único).
- **`nonce`**: 12 bytes aleatorios (requerido por GCM, nunca se reutiliza
  con la misma clave derivada).
- **Derivación de clave**: `scrypt(passphrase, salt, N=2**14, r=8, p=1, dklen=32)`
  produce la clave AES-256 de 32 bytes a partir de la passphrase del usuario.
- **Cifrado**: AES-256-GCM sobre el contenido plano del archivo. El tag de
  autenticación (16 bytes) queda al final del ciphertext (comportamiento
  estándar de la mayoría de las implementaciones de AESGCM, incluida
  `cryptography` de Python).

## Verificación de integridad

Cada entrada de `catalog.json` incluye, por artifact:

- `sha256_enc`: hash SHA-256 del archivo `.enc` completo (detecta
  corrupción de la descarga **antes** de intentar desencriptar).
- `sha256_plain`: hash SHA-256 del contenido **desencriptado** (detecta
  clave incorrecta o corrupción post-desencriptado).

## Implementación de referencia

La implementación de referencia (cifrado, descifrado, verificación) vive en
el paquete `model_hub` (`crypto.py`), en el repositorio
[`ai-ocr-2026`](https://github.com/OneclickEB/ai-ocr-2026), carpeta
`componentes/tools/model-hub-driver/`. Cualquier cliente que implemente este
formato puede leer los archivos de este hub.

## La clave

La clave (passphrase) **nunca** se publica en ningún repositorio, catálogo,
ni sitio. Vive únicamente en la variable de entorno `MODEL_HUB_KEY` de cada
servicio que publica o consume modelos. Si se pierde, todo lo publicado
hasta ese momento es irrecuperable — hacer backup de la clave fuera de este
ecosistema.
