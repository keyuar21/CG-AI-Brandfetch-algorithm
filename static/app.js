const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const api = async (url, opts = {}) => {
  const r = await fetch(url, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const toast = (msg) => {
  const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
};
const domainOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };

const state = { meta: null, brands: [], category: "All", country: "All", q: "", selCountries: new Set(), selCategories: new Set(["Any"]) };

async function init() {
  state.meta = await api("/api/meta");
  state.selCountries = new Set(state.meta.countries);
  $("#sources").innerHTML = Object.entries(state.meta.sources)
    .map(([k, on]) => `<span class="src ${on ? "on" : ""}" title="${on ? "configured" : "no key — skipped"}">${on ? "●" : "○"} ${k}</span>`).join("");
  renderSearchChips();
  await loadBrands();
}

function renderSearchChips() {
  $("#countryChips").innerHTML = state.meta.countries
    .map((c) => `<button class="chip ${state.selCountries.has(c) ? "on" : ""}" data-country="${esc(c)}">${esc(c)}</button>`).join("");
  $("#categoryChips").innerHTML = ["Any", ...state.meta.categories]
    .map((c) => `<button class="chip ${state.selCategories.has(c) ? "on" : ""}" data-cat="${esc(c)}">${esc(c)}</button>`).join("");
}

$("#countryChips").onclick = (e) => {
  const c = e.target.dataset.country; if (!c) return;
  state.selCountries.has(c) ? state.selCountries.delete(c) : state.selCountries.add(c);
  renderSearchChips();
};
$("#categoryChips").onclick = (e) => {
  const c = e.target.dataset.cat; if (!c) return;
  if (c === "Any") state.selCategories = new Set(["Any"]);
  else {
    state.selCategories.delete("Any");
    state.selCategories.has(c) ? state.selCategories.delete(c) : state.selCategories.add(c);
    if (!state.selCategories.size) state.selCategories.add("Any");
  }
  renderSearchChips();
};
$("#perQuery").oninput = (e) => ($("#perQueryVal").textContent = e.target.value);
$("#maxNew").oninput = (e) => ($("#maxNewVal").textContent = e.target.value);
$("#toggleSearch").onclick = () => $("#searchPanel").classList.toggle("hidden");

$("#runSearch").onclick = async () => {
  if (!state.selCountries.size) return toast("Pick at least one country");
  const btn = $("#runSearch");
  btn.disabled = true; btn.textContent = "Searching…";
  $("#progress").classList.remove("hidden", "done");
  $("#logs").textContent = "Starting LangGraph pipeline…\n";
  try {
    const { job_id } = await api("/api/search", {
      method: "POST",
      body: JSON.stringify({
        countries: [...state.selCountries], categories: [...state.selCategories],
        per_query: +$("#perQuery").value, max_new: +$("#maxNew").value,
      }),
    });
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const job = await api(`/api/search/${job_id}`);
      $("#logs").textContent = job.logs.join("\n");
      $("#logs").scrollTop = 1e9;
      if (job.status !== "running") {
        $("#progress").classList.add("done");
        toast(job.status === "done" ? `Added ${job.saved} new brands` + (job.pruned.length ? `, removed ${job.pruned.length} onboarded` : "") : "Search failed — see log");
        break;
      }
    }
    await loadBrands(true);
  } catch (e) {
    toast(e.message);
  } finally {
    btn.disabled = false; btn.textContent = "Start search";
  }
};

async function loadBrands(fresh = false) {
  state.brands = await api(`/api/brands${fresh ? "?fresh=1" : ""}`);
  render();
}

function render() {
  const byCountry = state.country === "All" ? state.brands : state.brands.filter((b) => b.county === state.country);
  // category sidebar
  const counts = {};
  byCountry.forEach((b) => (counts[b.catagory] = (counts[b.catagory] || 0) + 1));
  const cats = Object.keys(counts).sort();
  if (state.category !== "All" && !counts[state.category]) state.category = "All";
  $("#categoryList").innerHTML = [["All", byCountry.length], ...cats.map((c) => [c, counts[c]])]
    .map(([c, n]) => `<li class="${c === state.category ? "on" : ""}" data-cat="${esc(c)}"><span>${esc(c)}</span><span class="n">${n}</span></li>`).join("");
  // country filter
  const countries = ["All", ...new Set(state.brands.map((b) => b.county).filter(Boolean))];
  $("#countryFilter").innerHTML = countries
    .map((c) => `<button class="pill ${c === state.country ? "on" : ""}" data-country="${esc(c)}">${esc(c)}</button>`).join("");
  // grid
  const q = state.q.toLowerCase();
  const list = byCountry.filter((b) => (state.category === "All" || b.catagory === state.category) && (!q || b.brand_name.toLowerCase().includes(q)));
  $("#listTitle").textContent = state.category === "All" ? "All brands" : state.category;
  $("#count").textContent = `${list.length} new brand${list.length === 1 ? "" : "s"}`;
  $("#empty").classList.toggle("hidden", list.length > 0);
  $("#grid").innerHTML = list.map(card).join("");
}

function logo(b) {
  return b.logo
    ? `<img class="logo-img" src="${esc(b.logo)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=&quot;logo-img&quot;>${esc(b.brand_name[0])}</div>'">`
    : `<div class="logo-img">${esc(b.brand_name[0])}</div>`;
}

const shortSrc = (s) => s.split("|").map((x) => x.trim().split(":")[0]).filter(Boolean).join(" · ");

function card(b) {
  return `<div class="card" data-name="${esc(b.brand_name)}">
    <div class="tag"><span class="c">📍 ${esc(b.county)}</span><span class="s" title="${esc(b.data_source)}">${esc(shortSrc(b.data_source))}</span></div>
    ${logo(b)}
    <h4>${esc(b.brand_name)}</h4>
    <div class="dom">${esc(domainOf(b.website))}</div>
    <div class="cat">${esc(b.catagory)}</div>
    <div class="icons">${b.email ? "✉️" : ""} ${b.linkedin ? "in" : ""} ${b.phone ? "📞" : ""}</div>
  </div>`;
}

$("#categoryList").onclick = (e) => { const li = e.target.closest("li"); if (li) { state.category = li.dataset.cat; render(); } };
$("#countryFilter").onclick = (e) => { const c = e.target.dataset.country; if (c) { state.country = c; render(); } };
$("#q").oninput = (e) => { state.q = e.target.value; render(); };
$("#grid").onclick = (e) => { const c = e.target.closest(".card"); if (c) openBrand(c.dataset.name); };

// ---------------- drawer
const drawer = $("#drawer");
drawer.onclick = (e) => { if (e.target.hasAttribute("data-close")) drawer.classList.add("hidden"); };
document.onkeydown = (e) => { if (e.key === "Escape") drawer.classList.add("hidden"); };

function contactRow(k, v, href) {
  return `<div><span class="k">${k}</span>${v ? `<a href="${esc(href || v)}" target="_blank" rel="noopener">${esc(v)}</a>` : `<span class="none">not found</span>`}</div>`;
}

async function openBrand(name) {
  const b = state.brands.find((x) => x.brand_name === name);
  if (!b) return;
  drawer.classList.remove("hidden");
  $("#drawerBody").innerHTML = `
    <div class="d-head">${logo(b)}<div><h2>${esc(b.brand_name)}</h2>
      <div class="badges"><span class="badge o">${esc(b.catagory)}</span><span class="badge">📍 ${esc(b.county)}</span></div></div></div>
    <div class="section"><h3>About</h3><div id="summary" class="summary">${b.summary ? esc(b.summary) : '<span class="skeleton">Generating AI summary…</span>'}</div></div>
    <div class="section"><h3>Contacts</h3><div class="contacts">
      ${contactRow("Website", b.website)}
      ${contactRow("Email", b.email, b.email && "mailto:" + b.email)}
      ${contactRow("Phone", b.phone, b.phone && "tel:" + b.phone)}
      ${contactRow("LinkedIn", b.linkedin)}
    </div></div>
    <div class="section"><h3>Data source</h3><div class="badges">${b.data_source.split("|").map((s) => `<span class="badge">${esc(s.trim())}</span>`).join("")}</div></div>
    <div class="section"><h3>Reach out</h3>
      <div class="actions">
        <button class="action" data-kind="email"><span class="t">✉️ Send email</span><span class="d">AI-drafted collab email from CouponzGuru</span></button>
        <button class="action" data-kind="linkedin"><span class="t">💼 Send LinkedIn message</span><span class="d">AI-drafted short LinkedIn note</span></button>
      </div>
      <div id="editor"></div>
    </div>`;
  $("#drawerBody .actions").onclick = (e) => { const a = e.target.closest(".action"); if (a) openDraft(b, a.dataset.kind, a); };
  if (!b.summary) {
    try {
      const { summary } = await api(`/api/brands/${encodeURIComponent(b.brand_name)}/summary`);
      b.summary = summary;
      if ($("#summary")) $("#summary").textContent = summary;
    } catch (e) { $("#summary").textContent = "Couldn't generate summary: " + e.message; }
  }
}

const drafts = {};
async function openDraft(b, kind, el) {
  document.querySelectorAll(".action").forEach((a) => a.classList.toggle("on", a === el));
  const ed = $("#editor");
  const key = kind + ":" + b.brand_name;
  if (!drafts[key]) {
    ed.innerHTML = `<p class="skeleton">✍️ Drafting ${kind === "email" ? "email" : "LinkedIn message"}…</p>`;
    try { drafts[key] = await api("/api/draft", { method: "POST", body: JSON.stringify({ brand: b.brand_name, kind }) }); }
    catch (e) { ed.innerHTML = `<p class="skeleton">Draft failed: ${esc(e.message)}</p>`; return; }
  }
  const d = drafts[key];
  if (kind === "email") {
    ed.innerHTML = `<div class="editor">
      <input id="eTo" placeholder="To (email)" value="${esc(b.email)}" />
      <input id="eSubj" value="${esc(d.subject)}" />
      <textarea id="eBody">${esc(d.body)}</textarea>
      <div class="btns">
        ${state.meta.smtp ? `<button class="btn btn-primary" id="eSend">Send now</button>` : ""}
        <button class="btn ${state.meta.smtp ? "" : "btn-primary"}" id="eGmail">Open in Gmail</button>
        <button class="btn" id="eMail">Open in mail app</button>
        <button class="btn" id="eCopy">Copy</button>
        <button class="btn" id="eRegen">↻ Regenerate</button>
      </div></div>`;
    const vals = () => ({ to: $("#eTo").value.trim(), subject: $("#eSubj").value, body: $("#eBody").value });
    $("#eGmail").onclick = () => { const v = vals(); window.open(`https://mail.google.com/mail/?view=cm&fs=1&to=${encodeURIComponent(v.to)}&su=${encodeURIComponent(v.subject)}&body=${encodeURIComponent(v.body)}`, "_blank"); };
    $("#eMail").onclick = () => { const v = vals(); location.href = `mailto:${encodeURIComponent(v.to)}?subject=${encodeURIComponent(v.subject)}&body=${encodeURIComponent(v.body)}`; };
    $("#eCopy").onclick = () => { const v = vals(); navigator.clipboard.writeText(`Subject: ${v.subject}\n\n${v.body}`); toast("Copied"); };
    if ($("#eSend")) $("#eSend").onclick = async () => {
      const v = vals(); if (!v.to) return toast("Add a recipient email");
      if (!confirm(`Send this email to ${v.to}?`)) return;
      try { await api("/api/send-email", { method: "POST", body: JSON.stringify(v) }); toast("Email sent ✔"); } catch (e) { toast(e.message); }
    };
  } else {
    const target = b.linkedin || `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(b.brand_name)}`;
    ed.innerHTML = `<div class="editor">
      <textarea id="lBody">${esc(d.body)}</textarea>
      <div class="btns">
        <button class="btn btn-blue" id="lGo">Copy & open LinkedIn</button>
        <button class="btn" id="lCopy">Copy</button>
        <button class="btn" id="eRegen">↻ Regenerate</button>
      </div>
      <span class="muted" style="font-size:12px">LinkedIn has no public messaging API, so the message is copied — paste it into the chat on the page that opens.</span></div>`;
    $("#lCopy").onclick = () => { navigator.clipboard.writeText($("#lBody").value); toast("Copied"); };
    $("#lGo").onclick = () => { navigator.clipboard.writeText($("#lBody").value); toast("Copied — paste it in LinkedIn"); window.open(target, "_blank"); };
  }
  $("#eRegen").onclick = () => { delete drafts[key]; openDraft(b, kind, el); };
}

init().catch((e) => toast("Failed to load: " + e.message));
