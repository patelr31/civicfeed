// CivicFeed UI: renders data/<docket>/report.json and, for signed-in users, private stars and notes (Supabase).
const DOCKET = "CMS-2026-1255";
const SUPABASE_URL = "https://afpkwgitlwzvogoqhauw.supabase.co";
const SUPABASE_KEY = "sb_publishable_eh6L099Ju5BcyYMNAoXyGA_GcHI9hvY"; // publishable key; RLS limits every row to its owner
const sb = window.supabase ? window.supabase.createClient(SUPABASE_URL, SUPABASE_KEY) : null;

const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (n) => n.toLocaleString("en-US");
let R = null, user = null, notes = new Map(), shown = 40, onlyMine = false;
const STANCE = { support: "Supports", mixed: "Mixed", objects: "Objection language", unclear: "No clear stance" };

function bars(obj, total, max = 12) {
  const top = Object.entries(obj).slice(0, max), m = Math.max(...top.map(([, v]) => v), 1);
  return top.map(([k, v]) => `<div class="bar-row"><span class="bl">${esc(k)}</span><span class="bt"><i style="width:${(100 * v) / m}%"></i></span><b>${fmt(v)}</b></div>`).join("");
}

function renderStatic() {
  const n = R.numbers;
  $("#docket-link").textContent = `${R.docket} on regulations.gov`;
  $("#docket-link").href = `https://www.regulations.gov/docket/${R.docket}/comments`;
  $("#rule").textContent = R.rule;
  $("#headline").textContent = R.headline;
  $("#kpis").innerHTML = [
    [fmt(n.comments), "comments"], [fmt(n.unique), "distinct after collapsing campaigns"],
    [`${n.campaign_share}%`, "came from coordinated campaigns"], [fmt(R.themes.length), "themes, all quotes verified"],
  ].map(([v, l]) => `<div class="kpi"><strong>${v}</strong><span>${l}</span></div>`).join("");
  $("#proposed").innerHTML = R.what_cms_proposed.map((x) => `<li>${esc(x)}</li>`).join("");
  $("#summary").innerHTML = R.summary.map((p) => `<p>${esc(p)}</p>`).join("");
  $("#types").innerHTML = bars(R.by_type, n.comments);
  $("#campaigns").innerHTML = R.campaigns.map((c) => `<div class="camp">
      <div class="camp-head"><span class="pill">${c.kind === "template" ? "Shared template" : "Form letter"}</span><b>${fmt(c.size)} copies</b><span class="hint">${STANCE[c.stance] || c.stance}</span></div>
      <blockquote>${esc(c.opening.slice(0, 260))}${c.opening.length > 260 ? "…" : ""}</blockquote>
      ${c.organizations.length ? `<p class="hint">${c.organizations.length} organizations, e.g. ${c.organizations.slice(0, 4).map(esc).join(", ")}</p>` : `<p class="hint">Sent by individuals.</p>`}
    </div>`).join("");
  $("#notes").innerHTML = R.notes.map((x) => `<li>${esc(x)}</li>`).join("");
  for (const [t] of Object.entries(R.by_type)) $("#f-type").insertAdjacentHTML("beforeend", `<option>${esc(t)}</option>`);
  for (const t of R.themes) $("#f-theme").insertAdjacentHTML("beforeend", `<option value="${t.id}">${esc(t.title)}</option>`);
}

function noteFor(target) { return notes.get(target); }

function starBtn(target) {
  if (!user) return `<button class="star" data-login aria-label="Sign in to star">☆</button>`;
  const on = noteFor(target)?.starred;
  return `<button class="star${on ? " on" : ""}" data-star="${target}" aria-pressed="${!!on}" aria-label="Star">${on ? "★" : "☆"}</button>`;
}

function renderThemes() {
  const maxD = Math.max(...R.themes.map((t) => t.distinct));
  const list = R.themes.filter((t) => !onlyMine || noteFor(`theme:${t.id}`));
  $("#themes").innerHTML = list.map((t) => {
    const key = `theme:${t.id}`, nt = noteFor(key);
    const sideMax = Math.max(1, ...t.sides.map((s) => s.distinct));
    return `<article class="card theme" id="t-${t.id}">
      <div class="theme-head"><h3>${esc(t.title)}</h3>${starBtn(key)}</div>
      <div class="meter"><i style="width:${(100 * t.distinct) / maxD}%"></i></div>
      <p class="hint">${fmt(t.distinct)} ${esc(t.measure_label)}</p>
      <p>${esc(t.summary)}</p>
      ${t.debate ? `<div class="debate"><b>Debate:</b> ${esc(t.debate)}
        ${t.sides.map((s) => `<div class="bar-row"><span class="bl">${esc(s.label)}</span><span class="bt"><i class="side" style="width:${(100 * s.distinct) / sideMax}%"></i></span><b>${s.distinct}</b></div>
          <p class="who">${Object.entries(s.by_type).slice(0, 3).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}</p>`).join("")}</div>` : ""}
      <details><summary>Who raised it</summary><div class="bars small">${bars(t.by_type, t.distinct, 6)}</div></details>
      ${t.quotes.map((q) => `<figure class="quote"><blockquote>${esc(q.text)}</blockquote>
        <figcaption>${esc(q.org)} · ${esc(q.type)} · <a href="${esc(q.url)}" target="_blank" rel="noopener">${esc(q.id.slice(-4))}</a></figcaption></figure>`).join("")}
      ${user ? `<textarea class="note" data-note="${key}" placeholder="Private note…" rows="2">${esc(nt?.note || "")}</textarea>` : ""}
    </article>`;
  }).join("") || `<p class="hint">You haven't starred any themes yet.</p>`;
  $("#mine-toggle").innerHTML = user ? `<label class="chk"><input type="checkbox" id="only-mine" ${onlyMine ? "checked" : ""}> Only my starred themes</label>` : "";
  $("#only-mine") && ($("#only-mine").onchange = (e) => { onlyMine = e.target.checked; renderThemes(); });
}

function filtered() {
  const q = $("#q").value.trim().toLowerCase(), ty = $("#f-type").value, th = $("#f-theme").value, st = $("#f-stance").value, hide = $("#f-copies").checked;
  const seen = new Set();
  return R.comments.filter((c) => {
    const ok = (!ty || c.type === ty) && (!th || c.themes.includes(th)) && (!st || c.stance === st) &&
      (!q || (c.org + " " + c.excerpt + " " + c.id).toLowerCase().includes(q));
    if (!ok) return false;
    // collapse campaigns to the first copy that matches the filters, not the first copy overall
    if (hide && c.campaign) { if (seen.has(c.campaign_id)) return false; seen.add(c.campaign_id); }
    return true;
  });
}

function renderList() {
  const rows = filtered();
  $("#b-count").textContent = `${fmt(rows.length)} comments`;
  $("#list").innerHTML = rows.slice(0, shown).map((c) => {
    const key = `comment:${c.id}`;
    return `<li><div class="li-head"><a href="${esc(c.url)}" target="_blank" rel="noopener"><b>${esc(c.org || "Individual commenter")}</b></a>
      <span class="pill">${esc(c.type)}</span>${c.campaign ? `<span class="pill warn">${c.campaign === "template" ? "Template" : "Form letter"}</span>` : ""}
      <span class="hint">${STANCE[c.stance]} · ${c.posted}${c.attachments ? " · attachment" : ""}</span>${starBtn(key)}</div>
      <p>${esc(c.excerpt)}${c.excerpt.length >= 300 ? "…" : ""}</p></li>`;
  }).join("");
  $("#more").hidden = rows.length <= shown;
}

// ---------- auth + notes ----------
async function loadNotes() {
  notes = new Map();
  if (!user) return;
  const { data, error } = await sb.from("cf_notes").select("target,note,starred").eq("docket", DOCKET);
  if (!error) for (const r of data) notes.set(r.target, r);
}

async function save(target, patch) {
  const cur = noteFor(target) || { target, note: "", starred: false };
  const row = { ...cur, ...patch };
  notes.set(target, row);
  if (!row.starred && !row.note) {
    notes.delete(target);
    await sb.from("cf_notes").delete().eq("docket", DOCKET).eq("target", target);
    return;
  }
  const { error } = await sb.from("cf_notes").upsert({ docket: DOCKET, target, note: row.note, starred: row.starred, updated_at: new Date().toISOString() }, { onConflict: "user_id,docket,target" });
  if (error) alert("Couldn't save: " + error.message);
}

function renderAuth() {
  $("#auth").innerHTML = user
    ? `<span class="hint">${esc(user.email)}</span><button class="ghost small" id="signout">Sign out</button>`
    : `<button class="small" id="signin-open">Sign in</button>`;
  $("#signout") && ($("#signout").onclick = async () => { await sb.auth.signOut(); });
  $("#signin-open") && ($("#signin-open").onclick = () => $("#auth-dialog").showModal());
}

async function refresh() {
  await loadNotes();
  renderAuth(); renderThemes(); renderList();
}

function bind() {
  for (const id of ["#q", "#f-type", "#f-theme", "#f-stance", "#f-copies"]) $(id).oninput = () => { shown = 40; renderList(); };
  $("#more").onclick = () => { shown += 40; renderList(); };
  document.body.addEventListener("click", async (e) => {
    if (e.target.closest("[data-login]")) return $("#auth-dialog").showModal();
    const s = e.target.closest("[data-star]");
    if (s) { await save(s.dataset.star, { starred: !noteFor(s.dataset.star)?.starred }); renderThemes(); renderList(); }
  });
  let timer;
  document.body.addEventListener("input", (e) => {
    const t = e.target.closest("[data-note]");
    if (!t) return;
    clearTimeout(timer);
    timer = setTimeout(() => save(t.dataset.note, { note: t.value, starred: noteFor(t.dataset.note)?.starred ?? true }), 600);
  });
  $("#auth-form").addEventListener("submit", async (e) => {
    const mode = e.submitter?.value;
    if (mode === "cancel") return;
    e.preventDefault();
    const email = $("#a-email").value.trim(), password = $("#a-pass").value;
    $("#a-msg").textContent = "Working…";
    const { data, error } = mode === "signup"
      ? await sb.auth.signUp({ email, password, options: { emailRedirectTo: location.origin + location.pathname } })
      : await sb.auth.signInWithPassword({ email, password });
    if (error) { $("#a-msg").textContent = error.message; return; }
    if (mode === "signup" && !data.session) { $("#a-msg").textContent = "Check your email to confirm your account, then sign in here."; return; }
    $("#auth-dialog").close();
  });
}

async function init() {
  R = await (await fetch(`data/${DOCKET}/report.json`)).json();
  renderStatic(); bind(); renderAuth(); renderThemes(); renderList();
  if (!sb) return;
  const { data } = await sb.auth.getSession();
  user = data.session?.user || null;
  if (user) refresh();
  sb.auth.onAuthStateChange((_ev, session) => { user = session?.user || null; refresh(); });
}

init();
