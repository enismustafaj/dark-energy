// Star-diagram dashboard: household hub + radial device/contract nodes.
// Click a node to filter advice to that device (or the contract). Advice is
// ranked by annual cost benefit; top 5 shown by default.
(function () {
  const dash = document.querySelector(".dash");
  if (!dash) return;
  const hid = dash.dataset.hid;
  const nodes = JSON.parse(dash.dataset.nodes || "[]");

  const svg = document.getElementById("star");
  const SVGNS = "http://www.w3.org/2000/svg";
  const CX = 300, CY = 230, R = 165;

  function el(tag, attrs, text) {
    const e = document.createElementNS(SVGNS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (text != null) e.textContent = text;
    return e;
  }

  let activeNode = null; // {device_id, category} or null

  function drawStar() {
    svg.innerHTML = "";
    const n = nodes.length || 1;
    // Connector lines first (under the nodes).
    const positions = nodes.map((_, i) => {
      const ang = (-Math.PI / 2) + (2 * Math.PI * i) / n;
      return { x: CX + R * Math.cos(ang), y: CY + R * Math.sin(ang) };
    });
    positions.forEach((p) => {
      svg.appendChild(el("line", {
        x1: CX, y1: CY, x2: p.x, y2: p.y,
        stroke: "#2b3647", "stroke-width": 2,
      }));
    });

    // Hub.
    const hub = el("g", { class: "node hub" });
    hub.appendChild(el("circle", { cx: CX, cy: CY, r: 52, class: "hub-circle" }));
    hub.appendChild(el("text", { x: CX, y: CY - 6, class: "node-icon" }, "🏠"));
    hub.appendChild(el("text", { x: CX, y: CY + 16, class: "node-sub" }, "Home"));
    hub.addEventListener("click", () => selectNode(null));
    svg.appendChild(hub);

    // Device + contract nodes.
    nodes.forEach((node, i) => {
      const p = positions[i];
      const g = el("g", {
        class: "node devicenode" + (isActive(node) ? " active" : ""),
        "data-key": nodeKey(node),
      });
      g.appendChild(el("circle", { cx: p.x, cy: p.y, r: 38, class: "node-circle" }));
      g.appendChild(el("text", { x: p.x, y: p.y - 2, class: "node-icon" }, node.icon));
      g.appendChild(el("text", { x: p.x, y: p.y + 16, class: "node-sub" }, node.label));
      if (node.metric) {
        g.appendChild(el("text", { x: p.x, y: p.y + 56, class: "node-metric" }, node.metric));
      }
      g.addEventListener("click", () => selectNode(node));
      svg.appendChild(g);
    });
  }

  function nodeKey(node) {
    return node.kind === "contract" ? "contract" : "dev-" + node.device_id;
  }
  function isActive(node) {
    if (!activeNode) return false;
    if (activeNode.category === "contract") return node.kind === "contract";
    return node.device_id === activeNode.device_id;
  }

  // --- advice ---
  const listEl = document.getElementById("advice-list");
  const titleEl = document.getElementById("advice-title");
  const resetBtn = document.getElementById("advice-reset");

  function selectNode(node) {
    if (node && node.kind === "contract") activeNode = { category: "contract" };
    else if (node) activeNode = { device_id: node.device_id };
    else activeNode = null;
    drawStar();
    loadAdvice();
  }

  resetBtn.addEventListener("click", () => selectNode(null));

  async function loadAdvice() {
    let url = `/api/advice/${hid}`;
    let label = "Top recommendations";
    if (activeNode && activeNode.category === "contract") {
      url += "?category=contract";
      label = "Contract advice";
      resetBtn.hidden = false;
    } else if (activeNode && activeNode.device_id != null) {
      url += `?device_id=${activeNode.device_id}`;
      const nd = nodes.find((n) => n.device_id === activeNode.device_id);
      label = nd ? `${nd.label} advice` : "Device advice";
      resetBtn.hidden = false;
    } else {
      resetBtn.hidden = true;
    }
    titleEl.textContent = label;
    listEl.innerHTML = `<p class="empty">Loading…</p>`;
    try {
      const data = await (await fetch(url)).json();
      renderAdvice(data.advice);
    } catch (e) {
      listEl.innerHTML = `<p class="empty">Could not load advice.</p>`;
    }
  }

  function renderAdvice(items) {
    if (!items || !items.length) {
      listEl.innerHTML = `<p class="empty">No advice for this selection.</p>`;
      return;
    }
    listEl.innerHTML = "";
    items.forEach((a) => {
      const card = document.createElement("div");
      card.className = `advice sev-${a.severity} cat-${a.category}`;
      let costRow = "";
      if (a.advice && a.advice.baseline_cost_eur != null) {
        costRow = `<div class="cost-row">
          <span class="now">€${Math.round(a.advice.baseline_cost_eur)}/yr now</span>
          <span class="arrow">→</span>
          <span class="proj">€${Math.round(a.advice.counterfactual_cost_eur)}/yr</span>
        </div>`;
      }
      const benefit = a.benefit_eur
        ? `<span class="benefit">save €${a.benefit_eur}/yr</span>` : "";
      const payback = (a.advice && a.advice.payback_years)
        ? `<span class="payback">~${Math.round(a.advice.payback_years)}yr payback</span>` : "";
      const btn = a.action_type
        ? `<button class="act-btn" data-action="${a.action_type}">${a.action_label || "Take action"}</button>` : "";
      card.innerHTML = `
        <div class="advice-top"><span class="cat-tag">${a.category}</span>${benefit}${payback}</div>
        <h3>${a.title}</h3>
        <p>${a.body}</p>
        ${costRow}
        <div class="advice-actions">${btn}</div>`;
      listEl.appendChild(card);
    });
    wireActionButtons();
  }

  // --- actions ---
  const log = document.getElementById("action-log");
  function logAction(message, status, savings) {
    const div = document.createElement("div");
    div.className = "action-result" + (status === "failed" ? " err" : "");
    div.textContent = message;
    if (savings && savings > 0) {
      const s = document.createElement("span");
      s.className = "savings"; s.textContent = `  ~€${savings}/yr`;
      div.appendChild(s);
    }
    log.prepend(div);
  }

  function wireActionButtons() {
    listEl.querySelectorAll(".act-btn").forEach((btn) => {
      const orig = btn.textContent;
      btn.addEventListener("click", async () => {
        btn.disabled = true; btn.textContent = "Working…";
        try {
          const r = await fetch(`/api/actions/${btn.dataset.action}?household_id=${hid}`, {
            method: "POST", headers: { "Content-Type": "application/json" }, body: "{}",
          });
          const d = await r.json();
          if (!r.ok) logAction(d.detail || "Action not available", "failed");
          // success arrives via SSE 'action' event
        } catch (e) {
          logAction("Network error", "failed");
        } finally {
          btn.disabled = false; btn.textContent = orig;
        }
      });
    });
  }

  // --- live hub via SSE ---
  const es = new EventSource(`/api/stream/${hid}`);
  es.addEventListener("action", (e) => {
    const d = JSON.parse(e.data);
    logAction(d.message, d.status, d.expected_savings_eur);
  });

  drawStar();
  loadAdvice();
})();
