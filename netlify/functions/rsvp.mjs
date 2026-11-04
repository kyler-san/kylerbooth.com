// RSVPs for poker nights. GET /api/rsvp?game=YYYY-MM-DD lists responses;
// POST {game, name, status} records one (status: in | maybe | out).
import { getStore } from "@netlify/blobs";

const STATUSES = new Set(["in", "maybe", "out"]);
const GAME_RE = /^\d{4}-\d{2}-\d{2}$/;
const json = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });

export default async (req) => {
  const store = getStore({ name: "poker-rsvp", consistency: "strong" });
  const url = new URL(req.url);

  if (req.method === "GET") {
    const game = url.searchParams.get("game") || "";
    if (!GAME_RE.test(game)) return json({ error: "bad game" }, 400);
    const data = (await store.get(game, { type: "json" })) || {};
    return json({ game, responses: Object.values(data) });
  }

  if (req.method === "POST") {
    let body;
    try { body = await req.json(); } catch { return json({ error: "bad json" }, 400); }
    const game = String(body.game || "");
    const name = String(body.name || "").replace(/[<>"&]/g, "").replace(/\s+/g, " ").trim().slice(0, 30);
    const status = String(body.status || "");
    const guests = status === "out" ? 0 : Math.max(0, Math.min(5, parseInt(body.guests, 10) || 0));
    if (!GAME_RE.test(game) || !name || !STATUSES.has(status)) return json({ error: "bad input" }, 400);
    const data = (await store.get(game, { type: "json" })) || {};
    data[name.toLowerCase()] = { name, status, guests, at: new Date().toISOString() };
    await store.setJSON(game, data);
    return json({ game, responses: Object.values(data) });
  }

  return json({ error: "method not allowed" }, 405);
};

export const config = { path: "/api/rsvp" };
