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
  const rawPoR = hi3 > lo3 ? Math.round(((px - lo3) / (hi3 - lo3)) * 100) : 50;
  const poR = isPut ? 100 - rawPoR : rawPoR;          // 0 = at the fade / bottom-fish extreme
  const struct = structure(closes.slice(-41, -1));
  const stretch = (px / s20 - 1) * 100;

  // direction-adjusted: + means "moved the trade's way"
  const moveOurWay = isPut ? -ret20 : ret20;
  const stretchOurWay = isPut ? -stretch : stretch;
  const with20 = isPut ? px < s20 : px > s20;
  const with50 = isPut ? px < s50 : px > s50;
  const pctOffLow = ((px / lo3) - 1) * 100;
  const s10now = sma(closes, i, 10), s10prev = sma(closes, Math.max(0, i - 3), 10);
  const ma10Rising = s10now > s10prev;

  // what kind of entry is this? (mirror-image for puts)
  const intent = (poR <= 35 && !with20) ? "reversal"
    : (poR >= 55 && with20 && with50) ? "continuation" : "chop";

  let score = 0;
  const crit: { name: string; pass: boolean; detail: string }[] = [];
  const good = isPut ? "LH/LL" : "HH/HL", bad = isPut ? "HH/HL" : "LH/LL";
  const sgn = (n: number) => (n >= 0 ? "+" : "") + n.toFixed(0);
  crit.push({ name: "Entry type", pass: true, detail: `read as a ${intent}${isPut ? " (bearish)" : ""} entry — scored on that basis` });

  if (intent === "continuation") {
    if (struct === good) { score += 3; crit.push({ name: "Trend structure", pass: true, detail: `${good} — trend intact` }); }
    else if (struct === bad) { score -= 3; crit.push({ name: "Trend structure", pass: false, detail: `${bad} — trend broken` }); }
    else { score += struct === "flat" ? 0 : -1; crit.push({ name: "Trend structure", pass: false, detail: `${struct} — no clean trend` }); }
    if (moveOurWay > 35 || stretchOurWay > 22) { score -= 2; crit.push({ name: "Not extended", pass: false, detail: `already ran ${sgn(stretchOurWay)}% vs 20-day avg — chasing` }); }
    else { score += 2; crit.push({ name: "Not extended", pass: true, detail: `${sgn(stretchOurWay)}% vs 20-day avg — room to run` }); }
    if (poR >= 55 && poR <= 90) { score += 1; crit.push({ name: "Range position", pass: true, detail: `${poR}% of range — driving, not blown off` }); }
    else if (poR > 96) { score -= 1; crit.push({ name: "Range position", pass: false, detail: `${poR}% of range — buying the blow-off high` }); }
    else { crit.push({ name: "Range position", pass: false, detail: `${poR}% of range — not yet in the continuation zone` }); }
  } else if (intent === "reversal") {
    if (moveOurWay < -15 || stretchOurWay < -10) { score += 2; crit.push({ name: "Overextension", pass: true, detail: `stretched ${sgn(stretchOurWay)}% the wrong way — mean-reversion fuel` }); }
    else { crit.push({ name: "Overextension", pass: false, detail: `only ${sgn(stretchOurWay)}% vs 20-day — no exhaustion to fade yet` }); }
    if (poR <= 15) { score += 2; crit.push({ name: "Range position", pass: true, detail: `${poR}% — at the range extreme` }); }
    else if (poR <= 35) { score += 1; crit.push({ name: "Range position", pass: true, detail: `${poR}% — near the range extreme` }); }
    else { score -= 1; crit.push({ name: "Range position", pass: false, detail: `${poR}% — not at an extreme, weak reversal location` }); }
    const turning = isPut ? (!with20 && !ma10Rising) : ((px > s20 && closes[Math.max(0, i - 4)] < sma(closes, Math.max(0, i - 4), 20)) || (ma10Rising && pctOffLow >= 5));
    if (turning) { score += 2; crit.push({ name: "Turn signal", pass: true, detail: isPut ? "rolling over below the 20-day" : "reclaiming the 20-day / turning up" }); }
    else { crit.push({ name: "Turn signal", pass: false, detail: "no turn yet — early / catching the knife" }); }
    if (struct === bad) { score -= 1; crit.push({ name: "Trend structure", pass: false, detail: `${bad} still accelerating` }); }
    else if (struct === "flat") { score += 1; crit.push({ name: "Trend structure", pass: true, detail: "basing (flat)" }); }
  } else {
    if (poR <= 15) { score += 1; crit.push({ name: "Range position", pass: true, detail: `${poR}% — buying near the low` }); }
    else if (poR >= 92) { score -= 1; crit.push({ name: "Range position", pass: false, detail: `${poR}% — buying near the high` }); }
    else { crit.push({ name: "Range position", pass: false, detail: `${poR}% of range — mid-range chop, no location edge` }); }
    if (Math.abs(stretch) > 22) { score -= 1; crit.push({ name: "Not extended", pass: false, detail: `${sgn(stretch)}% vs 20-day — extended in a rangebound tape` }); }
    if (struct === good) { score += 1; crit.push({ name: "Trend structure", pass: true, detail: `${good} forming` }); }
    else if (struct === bad) { score -= 1; crit.push({ name: "Trend structure", pass: false, detail: `${bad} forming` }); }
  }
  score = Math.max(-6, Math.min(6, score));
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
    structure: struct, range_pct: poR, intent, score, tier, criteria: crit, recommended_contract: rec,
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
