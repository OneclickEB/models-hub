# Model Hub

Catálogo público de modelos entrenados (YOLO y LPR OCR) con historial de
versiones, métricas de entrenamiento y descarga cifrada.

- **Sitio**: https://oneclickeb.github.io/models-hub/
- **Formato de cifrado**: ver [`ENCRYPTION.md`](ENCRYPTION.md)
- **Cómo se publica/consume**: paquete `model_hub` en el repo
  [`ai-ocr-2026`](https://github.com/OneclickEB/ai-ocr-2026),
  carpeta `componentes/tools/model-hub-driver/`

## Estructura de este repo

```
models-hub/
├── docs/              # sitio estático (GitHub Pages)
│   ├── index.html
│   ├── app.js         # lógica del sitio (lista, filtros, detalle)
│   ├── chart.js        # gráfico de entrenamiento (canvas, sin dependencias)
│   ├── styles.css
│   ├── config.js       # única config a tocar si se clona este patrón
│   └── assets/logo.svg
├── catalog.json        # índice de modelos (liviano, committeado)
├── ENCRYPTION.md        # spec del formato de cifrado
└── README.md
```

Los binarios cifrados **no viven en este repo** — se publican como assets de
[GitHub Releases](https://github.com/OneclickEB/models-hub/releases), uno
por versión de modelo publicada. `catalog.json` referencia sus URLs.

## Publicar un modelo

No se publica manualmente. Se hace desde los botones "Subir al Hub" de:

- **LPR OCR Labeler** (`ai-ocr-2026/componentes/tools/ocr-labeling`), en
  Train History.
- **YOLO Server & Trainer** (`yolo11-backend`), en la tabla de modelos
  entrenados.

Ambos usan el paquete `model_hub` para cifrar y publicar vía la API REST de
GitHub — no requieren git ni credenciales locales más allá de un token con
scope `repo`.

## GitHub Pages

Settings → Pages → Source: rama `main`, carpeta `/docs`.
