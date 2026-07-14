// ─────────────────────────────────────────────────────────────────────────────
// Model Hub — UI estática (solo lectura del catalog.json). Nunca desencripta:
// solo lista, muestra metadata/gráficos y sirve la descarga cruda del artifact.
// ─────────────────────────────────────────────────────────────────────────────
(function () {
  const cfg = window.MODEL_HUB_CONFIG || {};
  let allModels = [];
  let activeFamily = "";

  document.getElementById("site-title").textContent = cfg.siteTitle || "Model Hub";
  document.title = cfg.siteTitle || "Model Hub";
  if (cfg.logo) {
    document.getElementById("brand-logo").src = cfg.logo;
    document.querySelector('link[rel="icon"]').href = cfg.logo;
  }
  if (cfg.repoUrl) document.getElementById("repo-link").href = cfg.repoUrl;

  const $grid = document.getElementById("grid");
  const $state = document.getElementById("state");
  const $count = document.getElementById("result-count");
  const $search = document.getElementById("search");

  function setState(msg, isError) {
    $state.textContent = msg || "";
    $state.className = "state" + (isError ? " error" : "");
    $state.style.display = msg ? "block" : "none";
  }

  function latestVersion(m) {
    const vs = (m.versions || []).slice().sort((a, b) => String(b.version).localeCompare(String(a.version)));
    return vs[0] || null;
  }

  function familyIcon(family) {
    return family === "yolo" ? "mdi-cube-scan" : "mdi-card-text-outline";
  }

  function fmtHeadline(hm) {
    if (!hm || hm.value === undefined || hm.value === null) return "—";
    if (hm.format === "percent") return (hm.value * 100).toFixed(2) + "%";
    return String(hm.value);
  }

  function cardHtml(m) {
    const latest = latestVersion(m);
    const hm = latest ? latest.headline_metric : null;
    const date = latest ? fmtDate(latest.released_at) : "sin versiones";
    const chips = [
      `<span class="chip family-${esc(m.family)}">${esc(m.family)}</span>`,
      `<span class="chip">talla ${esc(m.size || "?")}</span>`,
    ];
    if (m.target) chips.push(`<span class="chip">${esc(m.target)}</span>`);
    if (m.version_scheme) chips.push(`<span class="chip">${esc(m.version_scheme)}</span>`);
    return `
      <div class="card" data-id="${esc(m.model_id)}">
        <div class="card-head">
          <i class="mdi ${familyIcon(m.family)}"></i>
          <div>
            <h3>${esc(m.display_name || m.model_id)}</h3>
            <div class="card-sub">${date}</div>
          </div>
        </div>
        <div class="card-metric">${fmtHeadline(hm)} <span class="unit">${esc((hm && hm.label) || "")}</span></div>
        <div class="chips">${chips.join("")}</div>
        <div class="card-foot">
          <span class="version-badge">${(m.versions || []).length} versión(es)</span>
        </div>
      </div>`;
  }

  function render(list) {
    if (!list.length) {
      $grid.innerHTML = "";
      setState("No se encontraron modelos.");
      $count.textContent = "";
      return;
    }
    setState("");
    $grid.innerHTML = list.map(cardHtml).join("");
    $count.textContent = `${list.length} de ${allModels.length}`;
    $grid.querySelectorAll(".card").forEach((el) => {
      el.addEventListener("click", () => openDetail(el.dataset.id));
    });
  }

  function applyFilter() {
    const q = $search.value.trim().toLowerCase();
    let list = allModels;
    if (activeFamily) list = list.filter((m) => m.family === activeFamily);
    if (q) {
      list = list.filter((m) => {
        const hay = [m.display_name, m.model_id, m.size, m.target, m.family]
          .join(" ").toLowerCase();
        return hay.includes(q);
      });
    }
    render(list);
  }

  document.querySelectorAll(".family-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".family-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      activeFamily = btn.dataset.family || "";
      applyFilter();
    });
  });

  // --- Detalle ---
  const $overlay = document.getElementById("detail-overlay");
  function openDetail(id) {
    const m = allModels.find((x) => x.model_id === id);
    if (!m) return;
    document.getElementById("detail-icon").className = "mdi " + familyIcon(m.family);
    document.getElementById("detail-name").textContent = m.display_name || m.model_id;
    document.getElementById("detail-sub").textContent = m.model_id;
    document.getElementById("detail-download-hint").textContent = cfg.downloadHint || "";

    const chips = [
      `<span class="chip family-${esc(m.family)}">${esc(m.family)}</span>`,
      `<span class="chip">talla ${esc(m.size || "?")}</span>`,
    ];
    if (m.target) chips.push(`<span class="chip">${esc(m.target)}</span>`);
    if (m.version_scheme) chips.push(`<span class="chip">${esc(m.version_scheme)}</span>`);
    document.getElementById("detail-chips").innerHTML = chips.join("");

    const versions = (m.versions || []).slice().sort((a, b) => String(b.version).localeCompare(String(a.version)));
    document.getElementById("detail-versions").innerHTML = versions.map(versionRowHtml).join("");

    $overlay.classList.remove("hidden");

    // Los charts se dibujan después de insertar el HTML (necesitan el canvas en el DOM)
    versions.forEach((v, i) => {
      const canvas = document.getElementById(`chart-${i}`);
      if (!canvas || !v.chart_series || !v.rows) return;
      const legend = window.ModelHubChart.drawTrainingChart(canvas, v.rows, v.chart_series);
      const legendEl = document.getElementById(`legend-${i}`);
      if (legendEl) {
        legendEl.innerHTML = legend
          .map((l) => `<span><i style="background:${l.color}"></i>${esc(l.label)}: ${fmtNum(l.last)}</span>`)
          .join("");
      }
    });
  }

  function versionRowHtml(v, i) {
    const metrics = [
      v.headline_metric ? { label: v.headline_metric.label, val: fmtHeadline(v.headline_metric) } : null,
      ...((v.extra_metrics || []).map((em) => ({ label: em.label, val: em.value }))),
    ].filter(Boolean);

    const artifacts = (v.artifacts || [])
      .map((a) => {
        const isPlain = a.encrypted === false;
        const icon = isPlain ? "mdi-file-code-outline" : "mdi-lock-outline";
        return `
      <div class="artifact-row">
        <a href="${esc(a.download_url)}" target="_blank" rel="noopener">
          <i class="mdi ${icon}"></i> ${esc(a.format)} — ${fmtSize(a.size_bytes)}${isPlain ? " (sin cifrar)" : ""}
        </a>
        <span class="muted">sha256: ${esc((a.sha256_plain || "").slice(0, 12))}…</span>
      </div>`;
      })
      .join("");

    const hasChart = v.rows && v.rows.length > 1 && v.chart_series && v.chart_series.length;

    return `
      <div class="version-row">
        <div class="vtop">
          <span class="vname">${esc(v.version)}</span>
          <span class="vdate">${fmtDate(v.released_at)}</span>
        </div>
        ${v.description ? `<div class="vdesc">${esc(v.description)}</div>` : ""}
        <div class="metrics-row">
          ${metrics.map((m) => `<div class="metric-box"><div class="val">${esc(String(m.val))}</div><div class="lbl">${esc(m.label)}</div></div>`).join("")}
        </div>
        ${hasChart ? `<div class="chart-wrap"><canvas id="chart-${i}" style="width:100%;height:180px"></canvas><div class="chart-legend" id="legend-${i}"></div></div>` : ""}
        <div class="artifacts">${artifacts}</div>
      </div>`;
  }

  function closeDetail() { $overlay.classList.add("hidden"); }
  document.getElementById("detail-close").addEventListener("click", closeDetail);
  $overlay.addEventListener("click", (e) => { if (e.target === $overlay) closeDetail(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDetail(); });

  $search.addEventListener("input", applyFilter);

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
  function fmtDate(iso) {
    if (!iso) return "";
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  }
  function fmtSize(bytes) {
    if (!bytes) return "0 MB";
    return (bytes / 1024 / 1024).toFixed(1) + " MB";
  }
  function fmtNum(n) {
    if (n === undefined || n === null) return "—";
    return Math.abs(n) < 10 ? n.toFixed(4) : n.toFixed(1);
  }

  // --- Carga del catálogo ---
  function loadCatalog(url, fallback) {
    return fetch(url, { cache: "no-cache" }).then((r) => {
      if (!r.ok) {
        if (fallback) return loadCatalog(fallback, null);
        throw new Error("HTTP " + r.status);
      }
      return r.json();
    });
  }

  setState("Cargando catálogo…");
  loadCatalog(cfg.catalogUrl || "../catalog.json", "catalog.json")
    .then((data) => {
      allModels = (data.models || []).slice().sort((a, b) =>
        (a.display_name || a.model_id).localeCompare(b.display_name || b.model_id));
      const meta = data.updated_at
        ? `${allModels.length} modelos · actualizado ${new Date(data.updated_at).toLocaleString()}`
        : `${allModels.length} modelos`;
      document.getElementById("catalog-meta").textContent = meta;
      render(allModels);
    })
    .catch((err) => {
      setState("No se pudo cargar el catálogo (" + err.message + "). Verificá catalogUrl en config.js.", true);
    });
})();
