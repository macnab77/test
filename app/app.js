"use strict";

// ---------- Speicher -------------------------------------------------------
const STORE_KEY = "finanzen.v1";
const DEFAULT_CATEGORIES = [
  "Gehalt & Einnahmen", "Wohnen", "Energie & Internet", "Lebensmittel", "Drogerie",
  "Essen gehen", "Mobilität", "Versicherungen", "Abos & Streaming", "Shopping",
  "Gesundheit", "Bargeld", "Sparen & Investieren", "Steuern & Gebühren", "Umbuchung", "Rückzahlung", "Sonstiges",
];

function load() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) {
      const d = JSON.parse(raw);
      return { transactions: d.transactions || [], rules: d.rules || {} };
    }
  } catch (e) { console.warn("Laden fehlgeschlagen", e); }
  return { transactions: [], rules: {} };
}
function save() {
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ transactions: db.transactions, rules: db.rules }));
  } catch (e) {
    alert("Speichern fehlgeschlagen – bitte Daten exportieren.\n" + e.message);
  }
}

const db = load();
const ui = {
  view: "overview",
  mode: "month",            // "month" | "year"
  y: 0, m: 0,               // gewählter Zeitraum (m: 1–12)
  listCat: "",
  listPeriod: null,         // {mode, y, m} oder null = alle
  listLimit: 200,
  editing: null,
  editType: "exp",
  splitPrev: [],            // Anteile der Buchung vor dem Bearbeiten (für Verknüpfungen)
};

// ---------- Hilfen ---------------------------------------------------------
const $ = (s) => document.querySelector(s);
const eur = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR" });
const eur0 = new Intl.NumberFormat("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const MONTHS = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"];
const MONTHS_SHORT = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"];
const pad = (n) => String(n).padStart(2, "0");
const monthKey = (y, m) => `${y}-${pad(m)}`;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function inPeriod(t, p) {
  return p.mode === "year" ? t.date.startsWith(`${p.y}-`) : t.date.startsWith(monthKey(p.y, p.m) + "-");
}
function periodLabel(p) {
  return p.mode === "year" ? String(p.y) : `${MONTHS[p.m - 1]} ${p.y}`;
}
// Umbuchungen (z. B. Girokonto -> PayPal) und Rückzahlungen von Freunden für eine
// geteilte Rechnung zählen weder als Einnahme noch als Ausgabe
const counts = (t) => t.category !== "Umbuchung" && t.category !== "Rückzahlung";
// Bei einer geteilten Rechnung zählt nur der eigene Anteil als Ausgabe
const eff = (t) => (t.split && t.amount < 0 ? -t.split.mine : t.amount);
function sums(list) {
  let inc = 0, exp = 0;
  for (const t of list) {
    if (!counts(t)) continue;
    const a = eff(t);
    if (a >= 0) inc += a; else exp -= a;
  }
  return { inc, exp, bal: inc - exp };
}
function similarKey(desc) {
  return desc.toLowerCase().replace(/[^a-zäöüß ]/g, " ").split(/\s+/)
    .filter((w) => w.length > 2 && !["lastschrift", "kartenzahlung", "gutschrift", "überweisung", "dauerauftrag", "folgelastschrift", "sepa", "von", "für", "und"].includes(w))
    .slice(0, 2).join(" ");
}
function hashId(s) {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193); }
  return "c" + (h >>> 0).toString(16);
}
function allCategories() {
  const set = new Set(DEFAULT_CATEGORIES);
  db.transactions.forEach((t) => t.category && set.add(t.category));
  return [...set];
}
function parseAmount(s) {
  s = String(s).trim().replace(/\s|€/g, "");
  if (/,\d{1,2}$/.test(s)) s = s.replace(/\./g, "").replace(",", ".");
  else s = s.replace(/,/g, "");
  const v = parseFloat(s);
  return Number.isFinite(v) ? v : NaN;
}

// ---------- Navigation -----------------------------------------------------
function setView(v) {
  ui.view = v;
  document.querySelectorAll(".view").forEach((el) => el.classList.toggle("active", el.id === "view-" + v));
  document.querySelectorAll(".tabbar button").forEach((b) => b.classList.toggle("on", b.dataset.view === v));
  $("#view-title").textContent = { overview: "Übersicht", list: "Buchungen", settings: "Import" }[v];
  render();
  window.scrollTo(0, 0);
}

function initPeriod() {
  const latest = db.transactions.reduce((a, t) => (t.date > a ? t.date : a), "");
  const d = latest ? new Date(latest + "T12:00:00") : new Date();
  ui.y = d.getFullYear(); ui.m = d.getMonth() + 1;
}
function shiftPeriod(dir) {
  if (ui.mode === "year") ui.y += dir;
  else {
    ui.m += dir;
    if (ui.m < 1) { ui.m = 12; ui.y--; }
    if (ui.m > 12) { ui.m = 1; ui.y++; }
  }
  render();
}

// ---------- Übersicht ------------------------------------------------------
function renderOverview() {
  const p = { mode: ui.mode, y: ui.y, m: ui.m };
  $("#period-label").textContent = periodLabel(p);
  document.querySelectorAll(".period .seg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === ui.mode));

  const list = db.transactions.filter((t) => inPeriod(t, p));
  const s = sums(list);
  $("#t-income").textContent = eur.format(s.inc);
  $("#t-expense").textContent = eur.format(s.exp);
  $("#t-balance").textContent = (s.bal > 0 ? "+" : "") + eur.format(s.bal);
  $("#t-rate").textContent = s.inc > 0
    ? `Sparquote ${Math.round((s.bal / s.inc) * 100)} % · ${list.length} Buchungen`
    : `${list.length} Buchungen`;
  $("#empty-hint").hidden = db.transactions.length > 0;
  renderOpen();

  renderTrend();
  renderCategories(list, p);
}

function trendMonths() {
  // Jahr: Jan–Dez des Jahres; Monat: 12 Monate bis zum gewählten Monat
  const out = [];
  for (let i = 11; i >= 0; i--) {
    let y = ui.y, m = ui.mode === "year" ? 12 - i : ui.m - i;
    while (m < 1) { m += 12; y--; }
    out.push({ y, m });
  }
  return out;
}

function niceMax(v) {
  if (v <= 0) return 100;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / p;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
}

// Balken mit 4px abgerundeter Oberkante, unten bündig auf der Grundlinie
function barPath(x, y, w, h) {
  if (h <= 0) return "";
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

function renderTrend() {
  const months = trendMonths();
  const byMonth = new Map(months.map((mm) => [monthKey(mm.y, mm.m), { inc: 0, exp: 0 }]));
  for (const t of db.transactions) {
    const b = byMonth.get(t.date.slice(0, 7));
    if (!b || !counts(t)) continue;
    const a = eff(t);
    if (a >= 0) b.inc += a; else b.exp -= a;
  }
  const W = 340, H = 170, L = 40, R = 4, T = 8, B = 22;
  const max = niceMax(Math.max(...[...byMonth.values()].flatMap((v) => [v.inc, v.exp])));
  const plotH = H - T - B, colW = (W - L - R) / 12;
  const barW = Math.max(3, Math.min(12, colW * 0.34));
  const y = (v) => T + plotH - (v / max) * plotH;

  let svg = `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Einnahmen und Ausgaben der letzten 12 Monate">`;
  for (let i = 0; i <= 2; i++) {
    const v = (max / 2) * i, yy = y(v);
    svg += `<line class="grid" x1="${L}" x2="${W - R}" y1="${yy}" y2="${yy}"/>`;
    svg += `<text class="axis-label" x="${L - 6}" y="${yy + 3}" text-anchor="end">${esc(compact(v))}</text>`;
  }
  months.forEach((mm, i) => {
    const k = monthKey(mm.y, mm.m), v = byMonth.get(k);
    const cx = L + colW * i + colW / 2;
    const sel = ui.mode === "month" ? (mm.y === ui.y && mm.m === ui.m) : false;
    svg += `<g class="col${sel ? " sel" : ""}" data-key="${k}" data-i="${i}">`;
    svg += `<rect class="col-bg" x="${L + colW * i + 1}" y="${T}" width="${colW - 2}" height="${plotH}" rx="4"/>`;
    svg += `<path class="bar-inc" d="${barPath(cx - barW - 1, y(v.inc), barW, T + plotH - y(v.inc))}"/>`;
    svg += `<path class="bar-exp" d="${barPath(cx + 1, y(v.exp), barW, T + plotH - y(v.exp))}"/>`;
    svg += `<text class="axis-label" x="${cx}" y="${H - 6}" text-anchor="middle">${MONTHS_SHORT[mm.m - 1].slice(0, colW < 24 ? 1 : 3)}</text>`;
    svg += `</g>`;
  });
  svg += `</svg>`;
  const el = $("#trend");
  el.innerHTML = svg;

  const tip = $("#trend-tip");
  const show = (g) => {
    if (!g) { tip.hidden = true; return; }
    const [yy, m] = g.dataset.key.split("-").map(Number);
    const v = byMonth.get(g.dataset.key);
    tip.innerHTML = `<strong>${MONTHS[m - 1]} ${yy}</strong><br>Einnahmen ${eur.format(v.inc)}<br>Ausgaben ${eur.format(v.exp)}<br>Saldo ${eur.format(v.inc - v.exp)}`;
    tip.hidden = false;
    const card = el.parentElement.getBoundingClientRect();
    const r = g.getBoundingClientRect();
    const tw = tip.offsetWidth;
    let left = r.left - card.left + r.width / 2 - tw / 2;
    left = Math.max(8, Math.min(card.width - tw - 8, left));
    tip.style.left = left + "px";
    tip.style.top = (r.top - card.top - tip.offsetHeight + 6) + "px";
  };
  const svgEl = el.firstChild;
  svgEl.addEventListener("pointermove", (e) => show(e.target.closest(".col")));
  // Bei Touch feuert nach dem Tippen pointerleave – Tooltip dann stehen lassen
  svgEl.addEventListener("pointerleave", (e) => { if (e.pointerType === "mouse") show(null); });
  svgEl.addEventListener("click", (e) => {
    const g = e.target.closest(".col");
    if (!g) return;
    const [yy, m] = g.dataset.key.split("-").map(Number);
    ui.mode = "month"; ui.y = yy; ui.m = m;
    renderOverview();
    show($(`#trend .col[data-key="${g.dataset.key}"]`));
  });
}

function compact(v) {
  if (v >= 1000) return (v / 1000).toLocaleString("de-DE", { maximumFractionDigits: 1 }) + " T€";
  return eur0.format(v);
}

function renderCategories(list, p) {
  const by = new Map();
  for (const t of list) {
    if (eff(t) >= 0 || !counts(t)) continue;
    const c = t.category || "Sonstiges";
    by.set(c, (by.get(c) || 0) - eff(t));
  }
  const rows = [...by.entries()].sort((a, b) => b[1] - a[1]);
  const total = rows.reduce((a, r) => a + r[1], 0);
  const max = rows.length ? rows[0][1] : 1;
  $("#cats").innerHTML = rows.length
    ? rows.map(([c, v]) => `
      <button class="cat-row" data-cat="${esc(c)}">
        <span class="cat-name">${esc(c)}</span>
        <span class="cat-val">${eur.format(v)}<small>${Math.round((v / total) * 100)} %</small></span>
        <span class="cat-track"><span class="cat-fill" style="width:${Math.max(1, (v / max) * 100)}%"></span></span>
      </button>`).join("")
    : `<p class="muted">Keine Ausgaben in ${esc(periodLabel(p))}.</p>`;
  $("#cats").querySelectorAll(".cat-row").forEach((b) => b.addEventListener("click", () => {
    ui.listCat = b.dataset.cat;
    ui.listPeriod = { ...p };
    $("#search").value = "";
    setView("list");
  }));
}

// ---------- Buchungsliste --------------------------------------------------
function renderList() {
  const sel = $("#cat-filter");
  sel.innerHTML = `<option value="">Alle Kategorien</option>` +
    allCategories().map((c) => `<option ${c === ui.listCat ? "selected" : ""}>${esc(c)}</option>`).join("");

  const q = $("#search").value.trim().toLowerCase();
  const list = db.transactions
    .filter((t) => (!ui.listCat || t.category === ui.listCat)
      && (!ui.listPeriod || inPeriod(t, ui.listPeriod))
      && (!q || `${t.description} ${t.category} ${t.account} ${t.amount}`.toLowerCase().includes(q)))
    .sort((a, b) => b.date.localeCompare(a.date));

  const s = sums(list);
  const chip = ui.listPeriod ? ` · <button class="btn" id="clear-period" style="padding:2px 8px;font-size:12px">${esc(periodLabel(ui.listPeriod))} ✕</button>` : "";
  $("#list-summary").innerHTML = `${list.length} Buchungen · +${eur.format(s.inc)} / −${eur.format(s.exp)}${chip}`;
  const cp = $("#clear-period");
  if (cp) cp.addEventListener("click", () => { ui.listPeriod = null; renderList(); });

  let html = "", curMonth = "";
  const shown = list.slice(0, ui.listLimit);
  const monthSums = new Map();
  list.forEach((t) => {
    const k = t.date.slice(0, 7);
    if (counts(t)) monthSums.set(k, (monthSums.get(k) || 0) + eff(t));
  });
  for (const t of shown) {
    const k = t.date.slice(0, 7);
    if (k !== curMonth) {
      if (curMonth) html += `</div>`;
      const [yy, m] = k.split("-").map(Number);
      html += `<div class="group-head"><span>${MONTHS[m - 1]} ${yy}</span><span>${eur.format(monthSums.get(k) || 0)}</span></div><div class="group">`;
      curMonth = k;
    }
    const d = t.date.split("-").reverse().slice(0, 2).join(".") + ".";
    html += `<button class="tx" data-id="${esc(t.id)}">
      <span class="tx-desc">${esc(t.description)}</span>
      <span class="tx-amt ${t.amount > 0 ? "pos" : ""}">${eur.format(t.amount)}</span>
      <span class="tx-meta">${d} · ${esc(t.category || "Sonstiges")}${t.account ? " · " + esc(t.account) : ""}${splitNote(t)}</span>
    </button>`;
  }
  if (curMonth) html += `</div>`;
  if (list.length > shown.length) html += `<p style="text-align:center"><button class="btn" id="more-btn">Weitere ${list.length - shown.length} anzeigen</button></p>`;
  if (!list.length) html = `<p class="muted" style="text-align:center;margin-top:32px">Keine Buchungen gefunden.</p>`;
  $("#list").innerHTML = html;
  const more = $("#more-btn");
  if (more) more.addEventListener("click", () => { ui.listLimit += 500; renderList(); });
}

// ---------- Bearbeiten -----------------------------------------------------
function openEdit(tx) {
  ui.editing = tx || null;
  const f = $("#edit-form");
  $("#edit-title").textContent = tx ? "Buchung bearbeiten" : "Neue Buchung";
  f.category.innerHTML = allCategories().map((c) => `<option>${esc(c)}</option>`).join("");
  setEditType(tx && tx.amount > 0 ? "inc" : "exp");
  f.amount.value = tx ? Math.abs(tx.amount).toFixed(2).replace(".", ",") : "";
  f.date.value = tx ? tx.date : new Date().toISOString().slice(0, 10);
  f.description.value = tx ? tx.description : "";
  f.category.value = tx ? (tx.category || "Sonstiges") : "Sonstiges";
  f.applySimilar.checked = false;
  $("#apply-similar-wrap").hidden = !tx;
  $("#delete-btn").hidden = !tx;
  resetSplitBox(tx);
  $("#edit-dialog").showModal();
}
function setEditType(type) {
  ui.editType = type;
  document.querySelectorAll(".type-seg button").forEach((b) => b.classList.toggle("on", b.dataset.type === type));
  $("#split-box").hidden = type !== "exp";   // nur Ausgaben lassen sich teilen
}
function submitEdit(e) {
  e.preventDefault();
  const f = $("#edit-form");
  const amt = parseAmount(f.amount.value);
  if (!Number.isFinite(amt) || amt === 0) { f.amount.focus(); return; }
  const data = {
    date: f.date.value,
    amount: Math.round(Math.abs(amt) * 100) / 100 * (ui.editType === "inc" ? 1 : -1),
    description: f.description.value.trim(),
    category: f.category.value,
  };

  // Rechnung teilen: Summe muss aufgehen, sonst nicht speichern
  const shares = ui.editType === "exp" ? readShares().filter((sh) => sh.amount > 0 || sh.name) : [];
  if (shares.length) {
    const { diff } = splitDiff();
    if (Math.abs(diff) >= 0.01 || shares.some((sh) => !sh.amount)) {
      $("#split-box").open = true;
      updateSplitHint();
      $("#split-hint").classList.add("bad");
      if (shares.some((sh) => !sh.amount)) $("#split-hint").textContent = "Bitte bei jeder Person einen Betrag eintragen.";
      return;
    }
  }

  let t = ui.editing;
  if (t) {
    Object.assign(t, data);
    if (f.applySimilar.checked) {
      const key = similarKey(data.description);
      if (key) {
        db.rules[key] = data.category;
        db.transactions.forEach((x) => { if (similarKey(x.description) === key) x.category = data.category; });
      }
    }
  } else {
    t = { ...data, id: "m" + Date.now().toString(36), source: "manuell", account: "" };
    db.transactions.push(t);
  }

  // Anteile übernehmen; Verknüpfungen zu Geldeingängen nur ändern, wo sich der Status ändert
  const prev = new Map(ui.splitPrev.map((sh) => [sh.id, sh]));
  const kept = new Set();
  const messages = [];
  const newShares = shares.map((sh, i) => {
    const old = prev.get(sh.id);
    const merged = { ...(old || {}), id: sh.id, name: sh.name || `Person ${i + 2}`, amount: sh.amount };
    kept.add(sh.id);
    if (!old || old.status !== sh.status || old.amount !== sh.amount) {
      const msg = settleShare(t, merged, sh.status);
      if (msg) messages.push(`${merged.name}: ${msg}`);
    }
    return merged;
  });
  for (const old of ui.splitPrev) if (!kept.has(old.id)) unlinkShare(old);
  if (newShares.length) t.split = { mine: round2(Math.abs(parseAmount($("#split-mine").value) || 0)), shares: newShares };
  else delete t.split;

  save();
  $("#edit-dialog").close();
  render();
  if (messages.length) toast(messages.join("\n"));
}

// ---------- Rechnung teilen --------------------------------------------------
// Eine Ausgabe kann ein Feld `split` tragen:
//   { mine: 25.00, shares: [{ id, name, amount, status, settledDate, linkedTx, prevCategory }] }
// status: "offen" | "bar" | "ueberweisung". Kommt das Geld per Überweisung, wird der
// passende Geldeingang verknüpft und als "Rückzahlung" markiert (zählt nicht als Einnahme).
const SHARE_STATUS = { offen: "offen", bar: "bar erhalten", ueberweisung: "per Überweisung erhalten" };
const round2 = (v) => Math.round(v * 100) / 100;
const fmtNum = (v) => v.toFixed(2).replace(".", ",");
const fmtDate = (iso) => iso.split("-").reverse().join(".");
const today = () => new Date().toISOString().slice(0, 10);
const newShareId = () => "s" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
function addDays(iso, n) {
  const d = new Date(iso + "T12:00:00");
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

function splitNote(t) {
  if (!t.split || t.amount >= 0) return "";
  const open = t.split.shares.filter((sh) => sh.status === "offen").length;
  return ` · geteilt, mein Anteil ${eur.format(t.split.mine)}${open ? ` · ${open} offen` : ""}`;
}

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

// --- Geldeingang einer Rückzahlung zuordnen ---
function findRepayment(t, sh) {
  const used = new Set(db.transactions.flatMap((x) =>
    x.split ? x.split.shares.map((s) => s.linkedTx).filter(Boolean) : []));
  const first = sh.name.toLowerCase().split(/\s+/)[0];
  const nameHit = (x) => (first.length >= 3 && x.description.toLowerCase().includes(first) ? 1 : 0);
  return db.transactions
    .filter((x) => x.amount > 0 && Math.abs(x.amount - sh.amount) < 0.005
      && x.date >= t.date && x.date <= addDays(t.date, 90)
      && !used.has(x.id) && x.category !== "Umbuchung")
    .sort((a, b) => nameHit(b) - nameHit(a) || a.date.localeCompare(b.date))[0];
}
function linkShare(t, sh) {
  const r = findRepayment(t, sh);
  if (!r) return null;
  sh.linkedTx = r.id;
  sh.prevCategory = r.category;
  r.category = "Rückzahlung";
  sh.settledDate = r.date;
  return r;
}
function unlinkShare(sh) {
  if (!sh.linkedTx) return;
  const r = db.transactions.find((x) => x.id === sh.linkedTx);
  if (r && r.category === "Rückzahlung") r.category = sh.prevCategory || "Sonstiges";
  delete sh.linkedTx;
  delete sh.prevCategory;
}
// Nach einem Import: Überweisungen, die schon als erhalten markiert sind, zuordnen
function autoLinkRepayments() {
  let n = 0;
  for (const t of db.transactions) {
    if (!t.split) continue;
    for (const sh of t.split.shares) {
      if (sh.status === "ueberweisung" && !sh.linkedTx && linkShare(t, sh)) n++;
    }
  }
  return n;
}
function settleShare(t, sh, status) {
  unlinkShare(sh);
  sh.status = status;
  if (status === "offen") { delete sh.settledDate; return ""; }
  sh.settledDate = today();
  if (status !== "ueberweisung") return "";
  const r = linkShare(t, sh);
  return r
    ? `Geldeingang vom ${fmtDate(r.date)} zugeordnet – zählt nicht als Einnahme.`
    : "Noch kein passender Geldeingang da – wird beim nächsten Import automatisch zugeordnet.";
}

// --- Offene Beträge in der Übersicht ---
function renderOpen() {
  const items = [];
  for (const t of db.transactions) {
    if (t.split) for (const sh of t.split.shares) if (sh.status === "offen") items.push({ t, sh });
  }
  $("#open-card").hidden = !items.length;
  if (!items.length) return;
  items.sort((a, b) => a.t.date.localeCompare(b.t.date));
  $("#open-total").textContent = eur.format(items.reduce((a, x) => a + x.sh.amount, 0));
  $("#open-list").innerHTML = items.map(({ t, sh }) => `
    <div class="open-item" data-t="${esc(t.id)}" data-s="${esc(sh.id)}">
      <span class="open-who">${esc(sh.name)}</span>
      <span class="open-amt">${eur.format(sh.amount)}</span>
      <span class="open-meta">${fmtDate(t.date)} · ${esc(t.description)}</span>
      <div class="open-actions">
        <button class="btn" data-act="bar">Bar erhalten</button>
        <button class="btn" data-act="ueberweisung">Überweisung erhalten</button>
      </div>
    </div>`).join("");
}

// --- Bereich im Bearbeiten-Dialog ---
function editTotal() {
  const v = parseAmount($("#edit-form").amount.value);
  return Number.isFinite(v) ? round2(Math.abs(v)) : 0;
}
function renderShares(shares) {
  $("#split-shares").innerHTML = shares.map((sh) => `
    <div class="share" data-id="${esc(sh.id)}">
      <input class="sh-name" placeholder="Name" value="${esc(sh.name || "")}" aria-label="Name">
      <input class="sh-amt" inputmode="decimal" placeholder="0,00" value="${sh.amount ? fmtNum(sh.amount) : ""}" aria-label="Betrag">
      <select class="sh-status" aria-label="Status">${Object.entries(SHARE_STATUS).map(([k, l]) =>
        `<option value="${k}" ${(sh.status || "offen") === k ? "selected" : ""}>${l}</option>`).join("")}</select>
      <button type="button" class="share-del">Entfernen</button>
    </div>`).join("");
  updateSplitHint();
}
function readShares() {
  return [...document.querySelectorAll("#split-shares .share")].map((el) => ({
    id: el.dataset.id,
    name: el.querySelector(".sh-name").value.trim(),
    amount: round2(Math.abs(parseAmount(el.querySelector(".sh-amt").value) || 0)),
    status: el.querySelector(".sh-status").value,
  }));
}
function splitDiff() {
  const mine = round2(Math.abs(parseAmount($("#split-mine").value) || 0));
  const others = readShares().reduce((a, sh) => a + sh.amount, 0);
  return { total: editTotal(), mine, others: round2(others), diff: round2(editTotal() - mine - others) };
}
function updateSplitHint() {
  const hint = $("#split-hint");
  const shares = readShares();
  $("#split-remove").hidden = !shares.length;
  if (!shares.length) { hint.textContent = ""; hint.classList.remove("bad"); return; }
  const { mine, others, diff } = splitDiff();
  const open = shares.filter((sh) => sh.status === "offen").reduce((a, sh) => a + sh.amount, 0);
  if (Math.abs(diff) >= 0.01) {
    hint.textContent = diff > 0 ? `Es fehlen noch ${eur.format(diff)} bis zur Rechnungssumme.`
      : `${eur.format(-diff)} mehr als die Rechnungssumme.`;
    hint.classList.add("bad");
  } else {
    hint.textContent = `Ausgabe für dich: ${eur.format(mine)} · Freunde: ${eur.format(others)}` +
      (open ? `, davon ${eur.format(open)} offen` : ", alles erhalten");
    hint.classList.remove("bad");
  }
}
function splitEqually() {
  const total = editTotal();
  const n = Math.max(2, Math.min(30, parseInt($("#split-people").value, 10) || 2));
  if (!total) { $("#edit-form").amount.focus(); return; }
  const per = Math.floor((total / n) * 100) / 100;
  const old = readShares();
  const shares = Array.from({ length: n - 1 }, (_, i) =>
    ({ ...(old[i] || { id: newShareId(), name: "", status: "offen" }), amount: per }));
  $("#split-mine").value = fmtNum(round2(total - per * (n - 1)));   // Rundungscent trägst du
  renderShares(shares);
}
function resetSplitBox(tx) {
  const split = tx && tx.split;
  ui.splitPrev = split ? split.shares.map((sh) => ({ ...sh })) : [];
  $("#split-box").open = !!split;
  $("#split-mine").value = split ? fmtNum(split.mine) : "";
  $("#split-people").value = split ? split.shares.length + 1 : 2;
  renderShares(split ? split.shares : []);
}

// ---------- Import / Export ------------------------------------------------
function parseCsv(text) {
  text = text.replace(/^﻿/, "");
  const first = text.split(/\r?\n/, 1)[0];
  const delim = (first.match(/;/g) || []).length >= (first.match(/,/g) || []).length ? ";" : ",";
  const rows = []; let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') q = false;
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === delim) { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); rows.push(row); row = []; cell = "";
    } else cell += c;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  const head = rows.shift().map((h) => h.trim().toLowerCase());
  const col = (...names) => head.findIndex((h) => names.includes(h));
  const iD = col("datum", "date", "buchungstag"), iA = col("betrag", "amount", "netto", "umsatz"),
    iT = col("beschreibung", "description", "verwendungszweck", "name"), iC = col("kategorie", "category"),
    iK = col("konto", "account"), iS = col("quelle", "source");
  if (iD < 0 || iA < 0) throw new Error("CSV braucht Spalten 'Datum' und 'Betrag'.");
  return rows.filter((r) => r.length > 1).map((r) => {
    let d = r[iD].trim();
    const m = d.match(/^(\d{1,2})\.(\d{1,2})\.(\d{2,4})/);
    if (m) d = `${m[3].length === 2 ? "20" + m[3] : m[3]}-${pad(m[2])}-${pad(m[1])}`;
    return { date: d.slice(0, 10), amount: parseAmount(r[iA]), description: iT >= 0 ? r[iT].trim() : "",
      category: iC >= 0 ? r[iC].trim() : "", account: iK >= 0 ? r[iK].trim() : "", source: iS >= 0 ? r[iS].trim() : "CSV" };
  });
}

function importTransactions(items) {
  const existing = new Set(db.transactions.map((t) => t.id));
  let added = 0, skipped = 0;
  for (const raw of items) {
    const t = {
      id: raw.id || hashId(`${raw.date}|${Number(raw.amount).toFixed(2)}|${String(raw.description || "").toLowerCase()}`),
      date: String(raw.date || "").slice(0, 10),
      amount: Number(raw.amount),
      description: String(raw.description || "").trim(),
      category: raw.category || "",
      account: raw.account || "",
      source: raw.source || "",
    };
    if (!/^\d{4}-\d{2}-\d{2}$/.test(t.date) || !Number.isFinite(t.amount)) { skipped++; continue; }
    if (existing.has(t.id)) { skipped++; continue; }
    // geteilte Rechnung aus einem Backup übernehmen
    if (raw.split && Array.isArray(raw.split.shares) && t.amount < 0) {
      t.split = { mine: Number(raw.split.mine) || 0, shares: raw.split.shares.map((sh) => ({ ...sh })) };
    }
    const learned = db.rules[similarKey(t.description)];
    if (learned && t.category !== "Rückzahlung") t.category = learned;
    if (!t.category) t.category = t.amount > 0 ? "Gehalt & Einnahmen" : "Sonstiges";
    db.transactions.push(t);
    existing.add(t.id);
    added++;
  }
  const linked = autoLinkRepayments();
  save();
  return { added, skipped, linked };
}

async function handleImport(file) {
  const out = $("#import-result");
  try {
    const text = await file.text();
    let items;
    if (/\.json$/i.test(file.name) || text.trim().startsWith("{") || text.trim().startsWith("[")) {
      const data = JSON.parse(text);
      items = Array.isArray(data) ? data : data.transactions;
      if (data.rules) Object.assign(db.rules, data.rules);
    } else {
      items = parseCsv(text);
    }
    if (!Array.isArray(items)) throw new Error("Keine Buchungen in der Datei gefunden.");
    const r = importTransactions(items);
    out.textContent = `✓ ${r.added} neue Buchungen importiert` + (r.skipped ? `, ${r.skipped} übersprungen (schon vorhanden/ungültig).` : ".") +
      (r.linked ? ` ${r.linked} Rückzahlung(en) von Freunden zugeordnet.` : "");
    initPeriod();
    render();
  } catch (e) {
    out.textContent = "Import fehlgeschlagen: " + e.message;
  }
}

async function exportData() {
  const name = `finanzen-backup-${new Date().toISOString().slice(0, 10)}.json`;
  const blob = new Blob([JSON.stringify({ exported: new Date().toISOString(), rules: db.rules, transactions: db.transactions }, null, 1)],
    { type: "application/json" });
  const file = new File([blob], name, { type: "application/json" });
  if (navigator.canShare && navigator.canShare({ files: [file] })) {
    try { await navigator.share({ files: [file], title: name }); return; } catch (e) { if (e.name === "AbortError") return; }
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function renderSettings() {
  const n = db.transactions.length;
  if (!n) { $("#stats").textContent = "Keine Buchungen gespeichert."; return; }
  const dates = db.transactions.map((t) => t.date).sort();
  const accounts = [...new Set(db.transactions.map((t) => t.account).filter(Boolean))];
  const fmt = (d) => d.split("-").reverse().join(".");
  $("#stats").textContent = `${n} Buchungen vom ${fmt(dates[0])} bis ${fmt(dates[n - 1])}` +
    (accounts.length ? ` · Konten: ${accounts.join(", ")}` : "") +
    ` · ${Object.keys(db.rules).length} gelernte Kategorie-Regeln`;
}

// ---------- Start ----------------------------------------------------------
function render() {
  if (ui.view === "overview") renderOverview();
  else if (ui.view === "list") renderList();
  else renderSettings();
}

document.querySelectorAll(".tabbar button").forEach((b) => b.addEventListener("click", () => {
  if (b.dataset.view === "list") { ui.listLimit = 200; }
  setView(b.dataset.view);
}));
document.querySelectorAll(".period .seg button").forEach((b) => b.addEventListener("click", () => { ui.mode = b.dataset.mode; render(); }));
$("#prev-period").addEventListener("click", () => shiftPeriod(-1));
$("#next-period").addEventListener("click", () => shiftPeriod(1));
$("#add-btn").addEventListener("click", () => openEdit(null));
$("#search").addEventListener("input", () => { ui.listLimit = 200; renderList(); });
$("#cat-filter").addEventListener("change", (e) => { ui.listCat = e.target.value; renderList(); });
$("#list").addEventListener("click", (e) => {
  const b = e.target.closest(".tx");
  if (b) openEdit(db.transactions.find((t) => t.id === b.dataset.id));
});
document.querySelectorAll(".type-seg button").forEach((b) => b.addEventListener("click", () => setEditType(b.dataset.type)));
$("#edit-form").addEventListener("submit", submitEdit);
$("#split-equal").addEventListener("click", splitEqually);
$("#split-add").addEventListener("click", () => {
  const shares = readShares();
  const { diff } = splitDiff();
  shares.push({ id: newShareId(), name: "", amount: diff > 0 ? diff : 0, status: "offen" });
  renderShares(shares);
  $("#split-people").value = shares.length + 1;
});
$("#split-remove").addEventListener("click", () => {
  $("#split-mine").value = "";
  renderShares([]);
});
$("#split-box").addEventListener("input", updateSplitHint);
$("#split-box").addEventListener("change", updateSplitHint);
$("#split-shares").addEventListener("click", (e) => {
  if (!e.target.closest(".share-del")) return;
  e.target.closest(".share").remove();
  $("#split-people").value = readShares().length + 1;
  updateSplitHint();
});
$("#edit-form").amount.addEventListener("input", updateSplitHint);
$("#open-list").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-act]");
  if (!btn) return;
  const item = btn.closest(".open-item");
  const t = db.transactions.find((x) => x.id === item.dataset.t);
  const sh = t && t.split && t.split.shares.find((x) => x.id === item.dataset.s);
  if (!sh) return;
  const msg = settleShare(t, sh, btn.dataset.act);
  save();
  render();
  toast(msg || `${sh.name}: ${eur.format(sh.amount)} bar erhalten.`);
});
$("#cancel-btn").addEventListener("click", () => $("#edit-dialog").close());
$("#delete-btn").addEventListener("click", () => {
  if (!ui.editing || !confirm("Buchung löschen?")) return;
  if (ui.editing.split) ui.editing.split.shares.forEach(unlinkShare);
  db.transactions = db.transactions.filter((t) => t !== ui.editing);
  save(); $("#edit-dialog").close(); render();
});
$("#import-file").addEventListener("change", (e) => { if (e.target.files[0]) handleImport(e.target.files[0]); e.target.value = ""; });
$("#export-btn").addEventListener("click", exportData);
$("#clear-btn").addEventListener("click", () => {
  if (!confirm("Wirklich ALLE Buchungen auf diesem Gerät löschen?")) return;
  db.transactions = []; db.rules = {}; save(); render();
});

initPeriod();
render();

if ("serviceWorker" in navigator && (location.protocol === "https:" || location.hostname === "localhost")) {
  navigator.serviceWorker.register("sw.js").catch((e) => console.warn("Service Worker", e));
}
