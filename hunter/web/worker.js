/* Hunter web — a single Cloudflare Worker serving 4 friends.
 *
 * GET  /            the search UI (embedded below at build time)
 * POST /api/hunt    {query, deep} + header x-anthropic-key → offers JSON
 *
 * BYOK: the caller's Anthropic key arrives per-request and is used for the
 * LLM calls only — never stored, never logged. Optional shared gate: set the
 * ACCESS_CODE secret and the UI asks for it once (header x-access-code).
 * Playbooks are injected by build.py from ../playbooks/*.json.
 */

const PLAYBOOKS = /*PLAYBOOKS*/[];
const UI_HTML = /*UI*/"";

const CFG = { vat: 0.18, taxFreeUnder: 75, customsOver: 500 };
const SKU_SHAPES = [/^[A-Z]{1,3}\d{4}-\d{3}$/i, /^[A-Z]{2}\d{4}$/i, /^\d{12,14}$/];
const UA = { "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" };

const landed = (p) =>
  p == null ? null
  : p <= CFG.taxFreeUnder ? round(p)
  : p <= CFG.customsOver ? round(p * (1 + CFG.vat))
  : round(p * (1 + CFG.vat) * 1.10);
const round = (x) => Math.round(x * 100) / 100;

async function anthropic(key, model, system, prompt, maxTokens = 1500) {
  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": key,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model, max_tokens: maxTokens, system,
      messages: [{ role: "user", content: prompt }],
    }),
  });
  if (!r.ok) throw new Error(`anthropic ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const data = await r.json();
  return data.content.filter((b) => b.type === "text").map((b) => b.text).join("");
}

function parseJson(text) {
  let t = text.trim();
  if (t.startsWith("```")) { t = t.split("```")[1]; if (t.startsWith("json")) t = t.slice(4); }
  return JSON.parse(t);
}

async function identify(key, query) {
  const q = query.trim();
  if (SKU_SHAPES.some((re) => re.test(q)))
    return { query: q, brand: null, product: null, sku: q.toUpperCase(), aliases: [] };
  const out = parseJson(await anthropic(key, "claude-haiku-4-5",
    'You identify fashion/footwear products. JSON only: {"brand": str, "product": str,' +
    ' "sku": str|null, "aliases": [str]}. sku = manufacturer style code if known for' +
    " this exact product, else null. aliases: 2-4 alternate store search phrasings.",
    `Identify: ${q}`));
  out.query = q;
  return out;
}

function queriesFor(identity, pb) {
  const qs = [];
  if (identity.sku && pb.accepts_sku !== false) qs.push(identity.sku);
  if (identity.product) qs.push(`${identity.brand || ""} ${identity.product}`.trim());
  qs.push(...(identity.aliases || []));
  if (!qs.length) qs.push(identity.query);
  return qs.slice(0, 2);
}

async function shopifySuggest(pb, q) {
  const url = `https://${pb.domain}/search/suggest.json?q=${encodeURIComponent(q)}` +
    "&resources[type]=product&resources[limit]=8";
  const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(8000) });
  if (!r.ok) return [];
  const data = await r.json();
  return (data.resources?.results?.products || []).flatMap((p) => {
    const price = parseFloat(p.price);
    return isNaN(price) ? [] : [{
      store: pb.domain, title: p.title || "", brand: p.vendor || "",
      price, url: `https://${pb.domain}${p.url || ""}`.split("?")[0],
    }];
  });
}

async function llmParse(key, pb, q) {
  const url = pb.search_url.replace("{q}", encodeURIComponent(q));
  const r = await fetch(url, { headers: UA, signal: AbortSignal.timeout(10000) });
  if (!r.ok) return [];
  let html = await r.text();
  html = html.replace(/<(script|style|svg)[^>]*>[\s\S]*?<\/\1>/gi, " ")
             .replace(/\s+/g, " ").slice(0, 28000);
  const hits = parseJson(await anthropic(key, "claude-haiku-4-5",
    "Extract product offers from this store search-results page. JSON only:" +
    ' [{"title": str, "brand": str, "price": number, "currency": str, "url": str}].' +
    " Absolute URLs. Empty array if none." +
    (pb.price_selector ? ` Hint: ${pb.price_selector}` : ""),
    `Query: ${q}\nPage from ${pb.domain}:\n${html}`, 2500));
  return hits.filter((h) => h.price).map((h) => ({ ...h, store: pb.domain }));
}

async function huntStore(key, pb, identity, deep) {
  const method = pb.method || "search-url";
  for (const q of queriesFor(identity, pb)) {
    try {
      let hits = [];
      if (method === "shopify-suggest") hits = await shopifySuggest(pb, q);
      else if (method === "llm-parse" && deep) hits = await llmParse(key, pb, q);
      else if (method !== "llm-parse")
        return [{ store: pb.domain, manual: true,
                  url: pb.search_url.replace("{q}", encodeURIComponent(q)) }];
      if (hits.length) return hits;
    } catch (e) { /* store unreachable — move on */ }
  }
  return [];
}

async function handleHunt(req, env) {
  if (env.ACCESS_CODE && req.headers.get("x-access-code") !== env.ACCESS_CODE)
    return json({ error: "bad access code" }, 403);
  const key = req.headers.get("x-anthropic-key");
  if (!key || !key.startsWith("sk-ant-")) return json({ error: "missing api key" }, 400);
  const { query, deep } = await req.json();
  if (!query) return json({ error: "empty query" }, 400);

  let identity;
  try { identity = await identify(key, query); }
  catch (e) { return json({ error: "identify failed: " + e.message }, 502); }

  // all stores in parallel — Workers allow ~50 subrequests, we're well under
  const results = await Promise.all(
    PLAYBOOKS.map((pb) => huntStore(key, pb, identity, !!deep)));
  const offers = results.flat();
  for (const o of offers) if (o.price) o.landed = landed(o.price);
  offers.sort((a, b) => (a.landed ?? 1e9) - (b.landed ?? 1e9));
  return json({ identity, offers, stores_searched: PLAYBOOKS.length });
}

const json = (obj, status = 200) =>
  new Response(JSON.stringify(obj), {
    status, headers: { "content-type": "application/json; charset=utf-8" } });

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (url.pathname === "/api/hunt" && req.method === "POST")
      return handleHunt(req, env);
    return new Response(UI_HTML, {
      headers: { "content-type": "text/html; charset=utf-8" } });
  },
};
