#!/usr/bin/env python3
"""Ante-Social Invitational: turn group-chat replies into RSVPs.

Runs on Kyler's Mac. In the days before each game it reads the poker group
chat in Messages and RSVPs people on kylerbooth.com/poker when they:
  - react to the reminder text with 👍 or ❤️ (in) or 👎 (out), or
  - reply "yes", "in", "I'm in", "down"... (in) or "out", "can't", "no"... (out).
Names come from your Contacts; anyone it can't match is asked once in setup.

Usage:
  python3 poker_rsvp.py setup   # pick the group chat, confirm names (run once, in Terminal)
  python3 poker_rsvp.py run     # what the background job runs every 5 minutes
  python3 poker_rsvp.py status  # show what it sees right now (no RSVPs sent)
"""
import glob, json, os, re, sqlite3, subprocess, sys, time
from datetime import datetime, timedelta

HOME = os.path.expanduser("~")
DIR = os.path.join(HOME, ".poker-rsvp")
CFG = os.path.join(DIR, "config.json")
STATE = os.path.join(DIR, "state.json")
LOG = os.path.join(DIR, "log.txt")
CHAT_DB = os.path.join(HOME, "Library", "Messages", "chat.db")
SITE = "https://kylerbooth.com"
APPLE_EPOCH = datetime(2001, 1, 1)
WINDOW_DAYS = 3          # start listening this many days before a game
GAME_HOURS = 4.5         # stop after the game would be over

# ---------- small helpers ----------
def log(msg):
    os.makedirs(DIR, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M} {msg}"
    with open(LOG, "a") as f:
        f.write(line + "\n")
    if sys.stdout.isatty():
        print(line)

def load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default

def save(path, data):
    os.makedirs(DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)

def curl(url, data=None):
    cmd = ["curl", "-fsS", "--max-time", "20", url]
    if data is not None:
        cmd += ["-H", "content-type: application/json", "-d", json.dumps(data)]
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout

def apple_ns(dt):
    return int((dt - APPLE_EPOCH).total_seconds() * 1e9)

def from_apple(v):
    v = v or 0
    if v > 1e12:  # nanoseconds (all modern macOS)
        v = v / 1e9
    return APPLE_EPOCH + timedelta(seconds=v)

def utc_offset():
    return datetime.now() - datetime.utcnow()

def digits(s):
    d = re.sub(r"\D", "", s or "")
    return d[-10:] if len(d) >= 10 else d

def notify(title, text):
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification {json.dumps(text)} with title {json.dumps(title)}'],
                       capture_output=True, timeout=10)
    except Exception:
        pass

# ---------- message text ----------
def body_text(text, blob):
    """Message text; newer macOS keeps it only in attributedBody."""
    if text:
        return text
    if not blob:
        return ""
    try:
        s = blob.split(b"NSString")[1][5:]
        if s[0] == 0x81:
            n = int.from_bytes(s[1:3], "little"); s = s[3:3 + n]
        elif s[0] == 0x82:
            n = int.from_bytes(s[1:4], "little"); s = s[4:4 + n]
        else:
            n = s[0]; s = s[1:1 + n]
        return s.decode("utf-8", "ignore")
    except Exception:
        return ""

IN_PHRASES = ["yes", "yess", "yesss", "yep", "yup", "yeah", "ya", "yea", "y", "sure", "in", "im in", "i'm in",
              "count me in", "down", "im down", "i'm down", "lets go", "let's go", "lfg", "for sure",
              "absolutely", "definitely", "i'll be there", "ill be there", "be there", "see you there"]
OUT_PHRASES = ["no", "nope", "out", "im out", "i'm out", "can't", "cant", "cannot", "won't", "wont",
               "not this time", "pass", "i'll pass", "ill pass", "next time", "can't make it",
               "cant make it", "can't tonight", "cant tonight", "count me out", "not gonna make it"]
REACT_IN = re.compile(r"^(liked|loved|emphasi[sz]ed|reacted (👍|❤️|❤|😍|🙌|🔥|💯|‼️) to)\s", re.I)
REACT_OUT = re.compile(r"^(disliked|reacted 👎 to)\s", re.I)

def classify_text(t):
    """'in', 'out' or None for a plain text reply."""
    raw = (t or "").strip()
    if not raw:
        return None
    low = raw.lower().replace("’", "'").replace("‘", "'")
    # Android / SMS reactions arrive as text quoting the message they react to
    if REACT_OUT.match(raw):
        return "out" if re.search(r"ante|poker|kylerbooth", low) else None
    if REACT_IN.match(raw):
        return "in" if re.search(r"ante|poker|kylerbooth", low) else None
    if len(raw) > 60 or "maybe" in low or "?" in low or " if " in f" {low} " or "prob" in low:
        return None
    has_up = any(e in raw for e in ("👍", "✅", "🙋", "🤝"))
    has_down = "👎" in raw or "❌" in raw
    words = re.sub(r"[^a-z' ]", " ", low)
    words = re.sub(r"\s+", " ", words).strip()
    n_words = len(words.split()) if words else 0
    def starts(phrases):
        # one-word answers ("in", "yes", "no") only count in short replies,
        # so "in a meeting, will check" or "no way I'm missing it" aren't misread
        return any(words == p or (words.startswith(p + " ") and (" " in p or n_words <= 4)) for p in phrases)
    if starts(OUT_PHRASES) or (has_down and not has_up):
        return "out"
    if starts(IN_PHRASES) or (has_up and not has_down and not words):
        return "in"
    return None

# ---------- Contacts ----------
def contacts():
    """{normalized phone/email: (first, last, nickname)} from the macOS Contacts databases."""
    out = {}
    base = os.path.join(HOME, "Library", "Application Support", "AddressBook")
    paths = glob.glob(os.path.join(base, "AddressBook-v22.abcddb")) + \
        glob.glob(os.path.join(base, "Sources", "*", "AddressBook-v22.abcddb"))
    for p in paths:
        try:
            db = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
            people = {r[0]: (r[1] or "", r[2] or "", r[3] or "") for r in
                      db.execute("SELECT Z_PK, ZFIRSTNAME, ZLASTNAME, ZNICKNAME FROM ZABCDRECORD")}
            for owner, num in db.execute("SELECT ZOWNER, ZFULLNUMBER FROM ZABCDPHONENUMBER"):
                if owner in people and num:
                    out[digits(num)] = people[owner]
            for owner, addr in db.execute("SELECT ZOWNER, ZADDRESS FROM ZABCDEMAILADDRESS"):
                if owner in people and addr:
                    out[addr.lower()] = people[owner]
            db.close()
        except Exception as e:
            log(f"Couldn't read contacts at {p}: {e}")
    return out

def handle_key(h):
    return h.lower() if "@" in h else digits(h)

def contact_label(c):
    return " ".join(x for x in c[:2] if x).strip() or c[2] or "?"

# ---------- site data ----------
def site_data():
    """(roster names, list of game start datetimes in local time) from kylerbooth.com/poker."""
    html = curl(f"{SITE}/poker/?t={int(time.time())}")
    names = set(re.findall(r'\["([^"\]]{1,30})",\s*[\d.]+,\s*[\d.]+\]', html))
    try:
        for g in json.loads(curl(f"{SITE}/api/games")).get("games", []):
            names.update(p[0] for p in g.get("players", []))
    except Exception:
        pass
    m = re.search(r"const SCHEDULE = \[([^\]]*)\]", html)
    starts = [datetime.strptime(s, "%Y-%m-%dT%H:%M") for s in re.findall(r'"(\d{4}-\d\d-\d\dT\d\d:\d\d)"', m.group(1) if m else "")]
    return sorted(names), sorted(starts)

def auto_name(c, roster):
    """Leaderboard name for a contact, or None if it can't tell for sure."""
    first, last, nick = (x.strip() for x in c)
    by_lower = {n.lower(): n for n in roster}
    if first and last:
        cand = f"{first} {last[0]}."
        if cand.lower() in by_lower:
            return by_lower[cand.lower()]
        # "Jake Duncan" -> "Jake Dun." (roster abbreviates further when first names clash)
        hits = [n for n in roster if len(n.split(" ")) == 2 and n.split(" ")[0].lower() == first.split(" ")[0].lower()
                and last.lower().replace(" ", "").startswith(n.split(" ")[1].rstrip(".").lower())]
        if len(hits) == 1:
            return hits[0]
    for f in (first.split(" ")[0] if first else "", nick):
        if not f:
            continue
        hits = [n for n in roster if n.split(" ")[0].lower() == f.lower()]
        if len(hits) != 1:
            continue  # unknown, or two people share the first name (the two Davids)
        parts = hits[0].split(" ")
        if not last or len(parts) < 2 or parts[1][0].lower() == last[0].lower():
            return hits[0]
    return None

# ---------- Messages ----------
def open_chat_db():
    try:
        db = sqlite3.connect(f"file:{CHAT_DB}?mode=ro", uri=True)
        db.execute("SELECT 1 FROM message LIMIT 1")
        return db
    except Exception as e:
        raise SystemExit(
            "Can't read your Messages database. Give Full Disk Access to the app running this "
            f"(System Settings → Privacy & Security → Full Disk Access).\nDetail: {e}")

def chat_handles(db, chat_id):
    return [r[0] for r in db.execute(
        "SELECT h.id FROM chat_handle_join j JOIN handle h ON h.ROWID=j.handle_id WHERE j.chat_id=?", (chat_id,))]

def name_for(handle, cfg, book, roster):
    k = handle_key(handle)
    if k in cfg.get("names", {}):
        return cfg["names"][k] or None  # "" means "not a player"
    c = book.get(k)
    return auto_name(c, roster) if c else None

# ---------- commands ----------
def setup():
    if not sys.stdin.isatty():
        raise SystemExit("Run setup in Terminal: python3 ~/.poker-rsvp/poker_rsvp.py setup")
    cfg = load(CFG, {"names": {}})
    db = open_chat_db()
    book = contacts()
    print(f"Read {len(book)} phone numbers/emails from Contacts.")
    try:
        roster, _ = site_data()
    except Exception as e:
        raise SystemExit(f"Couldn't reach {SITE}: {e}")
    chats = db.execute("""
        SELECT c.ROWID, c.guid, c.display_name, MAX(m.date) AS last,
               (SELECT COUNT(*) FROM chat_handle_join j WHERE j.chat_id=c.ROWID) AS people
        FROM chat c JOIN chat_message_join cm ON cm.chat_id=c.ROWID JOIN message m ON m.ROWID=cm.message_id
        GROUP BY c.ROWID HAVING people >= 2 ORDER BY last DESC LIMIT 15""").fetchall()
    if not chats:
        raise SystemExit("No group chats found in Messages on this Mac. Is iMessage turned on here?")
    print("\nWhich group chat is the poker chat?\n")
    for i, (cid, guid, dname, last, people) in enumerate(chats, 1):
        who = [contact_label(book[handle_key(h)]) if handle_key(h) in book else h for h in chat_handles(db, cid)]
        label = dname or ", ".join(who[:5]) + ("…" if len(who) > 5 else "")
        print(f"  {i:>2}. {label}  ({people} people, last message {from_apple(last) + utc_offset():%b %d})")
    while True:
        pick = input("\nNumber: ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(chats):
            break
    cid, guid, dname, _, _ = chats[int(pick) - 1]
    cfg["chat_guid"] = guid
    cfg["chat_label"] = dname or ""
    print("\nMatching people in that chat to leaderboard names…")
    print("Leaderboard names: " + ", ".join(roster) + "\n")
    for h in chat_handles(db, cid):
        k = handle_key(h)
        c = book.get(k)
        label = f"{contact_label(c)} ({h})" if c else h
        current = cfg["names"].get(k)
        guess = current if current is not None else (auto_name(c, roster) if c else None)
        if guess:
            ans = input(f"  {label} → {guess}   [Enter = yes, or type a different name, or '-' if not a player]: ").strip()
            cfg["names"][k] = guess if ans == "" else ("" if ans == "-" else ans)
        else:
            ans = input(f"  {label} → ?   [type their leaderboard name, e.g. 'David S.', or '-' if not a player]: ").strip()
            cfg["names"][k] = "" if ans in ("", "-") else ans
    save(CFG, cfg)
    print(f"\nSaved. Watching “{cfg['chat_label'] or 'that chat'}”. I'll RSVP people from replies to your poker reminder.")

def scan(dry=False):
    cfg = load(CFG, {})
    if not cfg.get("chat_guid"):
        log("Not set up yet: run setup first.")
        return
    try:
        roster, starts = site_data()
    except Exception as e:
        log(f"Couldn't reach {SITE}: {e}")
        return
    now = datetime.now()
    game = next((s for s in starts if s - timedelta(days=WINDOW_DAYS) <= now <= s + timedelta(hours=GAME_HOURS)), None)
    if not game:
        if dry:
            log("No game in the next few days, so nothing to watch.")
        return
    gkey = game.strftime("%Y-%m-%d")
    db = open_chat_db()
    row = db.execute("SELECT ROWID FROM chat WHERE guid=?", (cfg["chat_guid"],)).fetchone()
    if not row:
        log("The poker group chat wasn't found. Run setup again.")
        return
    chat_id = row[0]
    off = utc_offset()  # chat.db stores UTC
    since = apple_ns(game - timedelta(days=WINDOW_DAYS) - off)
    rows = db.execute("""
        SELECT m.ROWID, m.guid, m.text, m.attributedBody, m.is_from_me, m.date,
               m.associated_message_guid, m.associated_message_type, h.id
        FROM message m JOIN chat_message_join cm ON cm.message_id=m.ROWID
        LEFT JOIN handle h ON h.ROWID=m.handle_id
        WHERE cm.chat_id=? AND m.date>=? ORDER BY m.date""", (chat_id, since)).fetchall()
    reminder = None
    for r in rows:
        if r[4] and "kylerbooth.com/poker" in body_text(r[2], r[3]).lower():
            reminder = r  # latest one wins
    if not reminder:
        if dry:
            log(f"Game {gkey}: no reminder with the kylerbooth.com/poker link in the chat yet.")
        return
    book = contacts()
    latest, unknown = {}, set()
    for (rid, guid, text, blob, mine, date, aguid, atype, handle) in rows:
        if date <= reminder[5] or mine or not handle:
            continue
        status = None
        atype = atype or 0
        if 2000 <= atype <= 3005:
            if not aguid or not aguid.endswith(reminder[1]):
                continue  # reaction to some other message
            status = {2000: "in", 2001: "in", 2002: "out", 3000: "out", 3001: "out"}.get(atype)
        else:
            status = classify_text(body_text(text, blob))
        if not status:
            continue
        name = name_for(handle, cfg, book, roster)
        if not name:
            if cfg.get("names", {}).get(handle_key(handle)) != "":
                unknown.add(handle)
            continue
        latest[name] = status
    state = load(STATE, {})
    sent = state.setdefault(gkey, {})
    for name, status in latest.items():
        if sent.get(name) == status:
            continue
        if dry:
            log(f"Would RSVP {name}: {status}")
            continue
        try:
            curl(f"{SITE}/api/rsvp", {"game": gkey, "name": name, "status": status})
            sent[name] = status
            log(f"RSVP'd {name}: {status} for {gkey}")
        except Exception as e:
            log(f"Couldn't RSVP {name}: {e}")
    if not dry:
        for k in list(state):
            if k < (now - timedelta(days=30)).strftime("%Y-%m-%d"):
                del state[k]
        save(STATE, state)
    if unknown:
        msg = "Couldn't match: " + ", ".join(sorted(unknown)) + ". Run setup to name them."
        log(msg)
        if not dry and state.get("_warned") != sorted(unknown):
            state["_warned"] = sorted(unknown)
            save(STATE, state)
            notify("Poker RSVPs", msg)
    if dry:
        log(f"Game {gkey}: reminder found; {len(latest)} people replied.")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "setup":
        setup()
    elif cmd == "status":
        scan(dry=True)
    else:
        scan()
