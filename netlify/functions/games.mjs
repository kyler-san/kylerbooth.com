// Poker nights saved from Game Night mode.
// GET  /api/games                      -> { games: [...] }  finished nights (public)
// GET  /api/games?draft=1  (x-host-key) -> { draft }        in-progress night (host only)
// POST /api/games {key, action, game|draft}                 host only
//   action "final": save a finished night (replaces same date) and clear the draft
//   action "draft": save the in-progress night
//   action "delete": remove a finished night by game.date
import { getStore } from "@netlify/blobs";
import { createHash } from "node:crypto";

// sha256 of the host key. The key itself is never stored here.
const HOST_KEY_SHA256 = "c721cd24aef3e465d5f65ff032dbdb922c1712712c8433fbf82b302bc056ba1e";
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
const okKey = (k) =>
  typeof k === "string" && createHash("sha256").update(k).digest("hex") === HOST_KEY_SHA256;
const str = (v, n) => String(v ?? "").replace(/[<>"&]/g, "").replace(/\s+/g, " ").trim().slice(0, n);
const num = (v) => {
  const x = Math.round(Number(v) * 100) / 100;
  return Number.isFinite(x) && x >= 0 && x <= 100000 ? x : null;
};

function cleanGame(g) {
  if (!g || !DATE_RE.test(g.date) || !Array.isArray(g.players) || !g.players.length || g.players.length > 40) return null;
  const players = [];
  for (const p of g.players) {
    const name = str(p?.[0], 30), inn = num(p?.[1]), out = num(p?.[2]);
    if (!name || inn === null || out === null) return null;
    players.push([name, inn, out]);
  }
  const out = { date: g.date, players };
  if (g.note) out.note = str(g.note, 240);
  if (num(g.food)) out.food = num(g.food);
  if (Array.isArray(g.knockouts))
    out.knockouts = g.knockouts.slice(0, 80).map((k) => [str(k?.[0], 30), str(k?.[1], 30)]).filter((k) => k[0] && k[1]);
  if (Array.isArray(g.bustOrder)) out.bustOrder = g.bustOrder.slice(0, 40).map((n) => str(n, 30)).filter(Boolean);
  return out;
}

export default async (req) => {
  const store = getStore({ name: "poker-games", consistency: "strong" });
  const url = new URL(req.url);

  if (req.method === "GET") {
    if (url.searchParams.get("draft")) {
      if (!okKey(req.headers.get("x-host-key"))) return json({ error: "forbidden" }, 403);
      return json({ draft: (await store.get("draft", { type: "json" })) || null });
    }
    const map = (await store.get("games", { type: "json" })) || {};
    return json({ games: Object.values(map).sort((a, b) => b.date.localeCompare(a.date)) });
  }

  if (req.method === "POST") {
    let body;
    try { body = await req.json(); } catch { return json({ error: "bad json" }, 400); }
    if (!okKey(body.key)) return json({ error: "forbidden" }, 403);

    if (body.action === "draft") {
      const s = JSON.stringify(body.draft ?? null);
      if (s.length > 200000) return json({ error: "too big" }, 413);
      await store.setJSON("draft", body.draft ?? null);
      return json({ ok: true });
    }
    const map = (await store.get("games", { type: "json" })) || {};
    if (body.action === "final") {
      const g = cleanGame(body.game);
      if (!g) return json({ error: "bad game" }, 400);
      map[g.date] = g;
      await store.setJSON("games", map);
      await store.setJSON("draft", null);
      return json({ ok: true, game: g });
    }
    if (body.action === "delete") {
      const d = body.game?.date;
      if (!DATE_RE.test(d || "")) return json({ error: "bad date" }, 400);
      delete map[d];
      await store.setJSON("games", map);
      return json({ ok: true });
    }
    return json({ error: "bad action" }, 400);
  }

  return json({ error: "method not allowed" }, 405);
};

export const config = { path: "/api/games" };
