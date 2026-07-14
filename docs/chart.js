// ─────────────────────────────────────────────────────────────────────────────
// Gráfico de entrenamiento — canvas 2D vanilla, sin dependencias.
// Cada serie se normaliza 0..1 de forma independiente (las escalas de
// plate_acc/mAP/loss no son comparables entre sí) para poder verlas juntas.
// ─────────────────────────────────────────────────────────────────────────────
(function () {
  const PALETTE = ["#1976D2", "#4CAF50", "#FB8C00", "#EF5350", "#AB47BC", "#26C6DA"];

  function drawTrainingChart(canvas, rows, seriesSpecs) {
    const ctx = canvas.getContext("2d");
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 560;
    const h = canvas.clientHeight || 200;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const pad = 8;
    if (!rows || rows.length < 2) {
      ctx.fillStyle = "#90A4AE";
      ctx.font = "12px sans-serif";
      ctx.fillText("No hay suficientes épocas para graficar", pad, h / 2);
      return [];
    }

    const legend = [];
    seriesSpecs.forEach((spec, idx) => {
      const values = rows.map((r) => toNumber(r[spec.key])).filter((v) => v !== null);
      if (values.length < 2) return;
      const min = Math.min(...values);
      const max = Math.max(...values);
      const span = Math.max(max - min, 1e-9);
      const color = PALETTE[idx % PALETTE.length];

      const points = rows
        .map((r, i) => {
          const v = toNumber(r[spec.key]);
          if (v === null) return null;
          const x = pad + (i * (w - pad * 2)) / (rows.length - 1);
          const y = h - pad - ((v - min) / span) * (h - pad * 2);
          return [x, y];
        })
        .filter(Boolean);

      ctx.beginPath();
      ctx.strokeStyle = color;
      ctx.lineWidth = 2;
      points.forEach(([x, y], i) => (i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
      ctx.stroke();

      legend.push({ label: spec.label, color, last: values[values.length - 1] });
    });

    return legend;
  }

  function toNumber(v) {
    if (v === null || v === undefined || v === "") return null;
    const n = typeof v === "number" ? v : parseFloat(v);
    return Number.isFinite(n) ? n : null;
  }

  window.ModelHubChart = { drawTrainingChart };
})();
