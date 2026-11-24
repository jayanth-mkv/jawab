// Shared helpers: demo access code, API calls, server-sent events, formatting.
const Jawab = (() => {
  const KEY = "jawab-code";
  const params = new URLSearchParams(location.search);
  if (params.get("code")) {
    try { localStorage.setItem(KEY, params.get("code")); } catch {}
  }
  const code = () => {
    try { return localStorage.getItem(KEY) || ""; } catch { return params.get("code") || ""; }
  };
  let asking = false;
  const askCode = (msg) => {
    if (asking) return; asking = true;
    const c = prompt(msg || "Enter the demo access code (it is on the GitHub & Demo slide of the deck)");
    asking = false;
    if (c && c.trim()) { try { localStorage.setItem(KEY, c.trim()); } catch {} location.reload(); }
  };
  // Check the code as soon as the page loads, so a missing/wrong code is caught before the first call.
  fetch("/api/state", { headers: { "x-access-code": code() } })
    .then((r) => { if (r.status === 401) askCode(code() ? "That access code didn't work. Enter the demo access code" : undefined); })
    .catch(() => {});

  async function api(path, opts = {}) {
    const res = await fetch(path, { ...opts, headers: { "x-access-code": code(), ...(opts.headers || {}) } });
    if (res.status === 401) { askCode(); throw new Error("access code required"); }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.error || res.statusText);
    return body;
  }

  function events(onMessage) {
    let es;
    const open = () => {
      es = new EventSource(`/api/events?code=${encodeURIComponent(code())}`);
      es.onmessage = (e) => { try { onMessage(JSON.parse(e.data)); } catch {} };
      es.onerror = () => { es.close(); setTimeout(open, 2500); };
    };
    open();
  }

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ago = (t) => {
    if (!t) return "";
    const s = Math.max(0, Math.round(Date.now() / 1000 - t));
    return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
  };
  const secs = (v) => (v == null ? "—" : v < 90 ? `${Math.round(v)}s` : `${(v / 60).toFixed(1)} min`);

  const STEPS = [
    ["open", "Gap found"],
    ["answered", "Owner answered"],
    ["called_back", "Customer told"],
    ["published", "On website"],
    ["verified", "Verified live"],
  ];

  return { api, events, esc, ago, secs, code, askCode, STEPS };
})();
