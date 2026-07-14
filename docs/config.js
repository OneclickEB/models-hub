// ─────────────────────────────────────────────────────────────────────────────
// Configuración del sitio del Model Hub.
// ─────────────────────────────────────────────────────────────────────────────
window.MODEL_HUB_CONFIG = {
  siteTitle: "Model Hub",
  logo: "assets/logo.svg",

  // Con GitHub Pages sirviendo desde /docs, el catalog.json de la raíz del
  // repo no se publica, así que se lee por URL raw (permite CORS). Si falla,
  // el sitio cae a ./catalog.json (útil para preview local).
  catalogUrl: "https://raw.githubusercontent.com/OneclickEB/models-hub/main/catalog.json",

  repoUrl: "https://github.com/OneclickEB/models-hub",

  downloadHint:
    "Los archivos se descargan cifrados (AES-256-GCM). Son inútiles sin el " +
    "driver model_hub configurado con la clave correcta. Ver ENCRYPTION.md " +
    "en el repo para el detalle del formato.",
};
