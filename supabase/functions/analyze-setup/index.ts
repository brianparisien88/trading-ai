// Analyze a prospective trade: fetch the ticker's chart from Yahoo (server-side,
// no CORS), compute the technical Setup Score, return criteria + a contract
// recommendation. Public read-only utility — no DB, no secrets. verify_jwt off
// so the static dashboard can call it without a logged-in user.
import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
};
const J = (b: unknown, s = 200) =>
  new Response(JSON.stringify(b), { status: s, headers: { ...CORS, "Content-Type": "application/json" } });

function sma(a: number[], i: number, k: number) {
  const s = a.slice(Math.max(0, i - k + 1), i + 1);
  return s.reduce((x, y) => x + y, 0) / s.length;
}
function pivots(seq: number[], lr = 3) {
  const hi: number[] = [], lo: number[] = [];
  for (let i = lr; i < seq.length - lr; i++) {
    const w = seq.slice(i - lr, i + lr + 1);
    if (seq[i] === Math.max(...w)) hi.push(seq[i]);
    if (seq[i] === Math.min(...w)) lo.push(seq[i]);
  }
  return { hi, lo };
}
function structure(pre: number[]) {
  if (pre.length < 20) return null;
  const { hi, lo } = pivots(pre);
  if (hi.length < 2 || lo.length < 2) return "flat";
  if (hi.at(-1)! > hi.at(-2)! && lo.at(-1)! > lo.at(-2)!) return "HH/HL";
  if (hi.at(-1)! < hi.at(-2)! && lo.at(-1)! < lo.at(-2)!) return "LH/LL";
  return "mixed";
}

async function yahoo(ticker: string) {
  const u = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?range=6mo&interval=1d`;
  const r = await fetch(u, { headers: { "User-Agent": "Mozilla/5.0 (analyze-setup)" } });
  if (!r.ok) throw new Error(`yahoo ${r.status}`);
  const res = (await r.json())?.chart?.result?.[0];
  if (!res) throw new Error("no data for that ticker");
  const closes: number[] = (res.indicators?.quote?.[0]?.close ?? []).filter((x: number | null) => x != null);
  return { price: res.meta?.regularMarketPrice as number, name: res.meta?.longName ?? ticker, closes };
}

function analyze(closes: number[], price: number, isPut: boolean) {
  const i = closes.length - 1;
  const px = closes[i];
  const s20 = sma(closes, i, 20), s50 = sma(closes, i, 50);
  const ret20 = i >= 20 ? (px / closes[i - 20] - 1) * 100 : 0;
  const look = closes.slice(Math.max(0, i - 63));
  const lo3 = Math.min(...look), hi3 = Math.max(...look);
  let poR = hi3 > lo3 ? Math.round(((px - lo3) / (hi3 - lo3)) * 100) : 50;
  if (isPut) poR = 100 - poR;
  const struct = structure(closes.slice(-41, -1));
  const stretch = (px / s20 - 1) * 100;

  let score = 0;
  const crit: { name: string; pass: boolean; detail: string }[] = [];

  const good = isPut ? "LH/LL" : "HH/HL", bad = isPut ? "HH/HL" : "LH/LL";
  if (struct === good) { score += 3; crit.push({ name: "Trend structure", pass: true, detail: `${good} — pivots trending your way` }); }
  else if (struct === bad) { score -= 3; crit.push({ name: "Trend structure", pass: false, detail: `${bad} — pivots trending against the trade` }); }
  else { score += struct === "flat" ? 0 : -1; crit.push({ name: "Trend structure", pass: false, detail: `${struct} — no clean trend` }); }

  const extended = Math.abs(ret20) > 35 || Math.abs(stretch) > 22;
  if (extended) { score -= 2; crit.push({ name: "Not extended", pass: false, detail: `stretched ${stretch >= 0 ? "+" : ""}${stretch.toFixed(0)}% vs 20-day avg` }); }
  else { score += 2; crit.push({ name: "Not extended", pass: true, detail: `${stretch >= 0 ? "+" : ""}${stretch.toFixed(0)}% vs 20-day avg — room to move` }); }

  if (poR >= 25 && poR <= 88) { score += 1; crit.push({ name: "Range position", pass: true, detail: `${poR}% of the 3-month range — room to run` }); }
  else if (poR > 95) { score -= 1; crit.push({ name: "Range position", pass: false, detail: `${poR}% of range — buying the extreme` }); }
  else { crit.push({ name: "Range position", pass: poR >= 12, detail: `${poR}% of range` }); }

  const tier = score >= 4 ? "strong" : score >= 1 ? "ok" : "weak";

  // contract recommendation (baseline / "Default" strategy)
  const lo = (price * (isPut ? 1.0 : 0.95)), hiK = (price * (isPut ? 0.95 : 1.05));
  const rec = {
    strike: `$${Math.min(lo, hiK).toFixed(2)}–$${Math.max(lo, hiK).toFixed(2)} (ATM to 5% ${isPut ? "ITM" : "OTM"})`,
    dte: "46–90 days to expiry",
    note: "ATM/ITM + 46–90 DTE is where your closed trades actually made money.",
  };
  return {
    price, s20: +s20.toFixed(2), s50: +s50.toFixed(2), ret20: +ret20.toFixed(1),
    structure: struct, range_pct: poR, score, tier, criteria: crit, recommended_contract: rec,
  };
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return new Response("ok", { headers: CORS });
  try {
    const url = new URL(req.url);
    const body = req.method === "POST" ? await req.json().catch(() => ({})) : {};
    const ticker = String(body.ticker ?? url.searchParams.get("ticker") ?? "").trim().toUpperCase();
    const isPut = String(body.side ?? url.searchParams.get("side") ?? "call").toLowerCase() === "put";
    if (!/^[A-Z.\-^]{1,10}$/.test(ticker)) return J({ error: "invalid ticker" }, 400);
    const { price, name, closes } = await yahoo(ticker);
    if (closes.length < 30) return J({ error: "not enough price history" }, 422);
    return J({ ticker, name, side: isPut ? "put" : "call", ...analyze(closes, price, isPut) });
  } catch (e) {
    return J({ error: String((e as Error).message ?? e) }, 502);
  }
});
