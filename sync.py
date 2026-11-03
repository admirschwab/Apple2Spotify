#!/usr/bin/env python3
"""
Apple Music → Spotify Playlist-Sync

  python sync.py auth             einmalig: bei Spotify anmelden
  python sync.py apple            nur die Apple-Titel als JSON ausgeben
  python sync.py sync             Spotify-Playlist mit Apple abgleichen
  python sync.py sync --dry-run   nur anzeigen, nichts ändern
  python sync.py sync --force     Sicherheitsgrenzen beim Entfernen ignorieren
  python sync.py restore DATEI    entfernte Songs aus einem Backup zurückholen
"""
from __future__ import annotations

import argparse
import base64
import fcntl
import json
import logging
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urlparse

import requests

import config

BASE = Path(__file__).resolve().parent
TOKEN_FILE = BASE / "spotify_tokens.json"
CACHE_FILE = BASE / "match_cache.json"
LOCK_FILE = BASE / "sync.lock"
STATE_FILE = BASE / "sync_state.json"
BACKUP_DIR = BASE / "backups"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

log = logging.getLogger("sync")


# ───────────────────────── Hilfsfunktionen ─────────────────────────

def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def clean_title(t: str) -> str:
    t = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", t)  # (feat. …), [Remastered] …
    t = re.sub(r"\s+-\s+.*$", "", t)               # " - Remastered 2011" …
    return t.strip()


def first_artist(a: str) -> str:
    return re.split(r"\s*,\s*|\s+(?:&|feat\.?|ft\.?|x)\s+", a, flags=re.I)[0].strip()


def norm(s: str) -> str:
    return re.sub(r"[\W_]+", "", s.casefold())


def similar(a: str, b: str) -> bool:
    return bool(a and b) and (a == b or a in b or b in a)


def track_key(title: str, artist: str) -> str:
    return f"{norm(clean_title(title))}|{norm(first_artist(artist))}"


def fix_text(s: str) -> str:
    """Repariert UTF-8-Text, der fälschlich als Latin-1 gelesen wurde (z. B. 'fÃ¼r' → 'für')."""
    if not any(c in s for c in "ÃÂÅÄ"):
        return s
    for enc in ("latin-1", "cp1252"):
        try:
            return s.encode(enc).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return s


def unique(tracks: list[dict]) -> list[dict]:
    seen, out = set(), []
    for t in tracks:
        t = {"title": fix_text(t["title"]), "artist": fix_text(t["artist"])}
        k = track_key(t["title"], t["artist"])
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


# ───────────────────────── Apple Music ─────────────────────────

def apple_static(url: str) -> list[dict]:
    """Ohne Browser: eingebettetes JSON der normalen music.apple.com-Seite lesen."""
    main_url = re.sub(r"^https?://embed\.music\.apple\.com", "https://music.apple.com", url, flags=re.I)
    r = requests.get(main_url, timeout=30,
                     headers={"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"})
    r.raise_for_status()
    # Apple sendet keinen Zeichensatz im Header → requests würde ISO-8859-1 annehmen
    html = r.content.decode("utf-8", errors="replace")
    tracks: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            t, a = node.get("title"), node.get("artistName")
            if isinstance(t, str) and isinstance(a, str):
                tracks.append({"title": t, "artist": a})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    m = re.search(r'<script[^>]+id=["\']serialized-server-data["\'][^>]*>(.*?)</script>', html, re.S | re.I)
    if m:
        try:
            walk(json.loads(m.group(1)))
        except json.JSONDecodeError:
            pass

    if not tracks:  # Fallback: schema.org JSON-LD
        for block in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                                html, re.S | re.I):
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            for t in (data.get("track") or []) if isinstance(data, dict) else []:
                by = t.get("byArtist") or {}
                if isinstance(by, list):
                    by = by[0] if by else {}
                if t.get("name"):
                    tracks.append({"title": t["name"], "artist": by.get("name", "")})

    return unique(tracks)


SONG_TYPES = {"songs", "library-songs", "music-videos"}


def apple_browser(url: str) -> list[dict]:
    """Mit Playwright: Seite in echtem (unsichtbarem) Chromium rendern."""
    try:
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("Playwright ist nicht installiert "
                           "(pip install playwright && playwright install chromium).")

    title_sel = ".audio-tracklist-item__title-text"
    api_tracks: dict[str, dict] = {}
    dom_tracks: list[dict] = []
    responses = []

    def collect_api(node):
        # Apple-API-Antworten: {"type": "songs", "attributes": {"name", "artistName"}}
        if isinstance(node, dict):
            attrs = node.get("attributes")
            if node.get("type") in SONG_TYPES and isinstance(attrs, dict) and attrs.get("name"):
                key = node.get("id") or f'{attrs["name"]}|{attrs.get("artistName", "")}'
                api_tracks.setdefault(key, {"title": attrs["name"], "artist": attrs.get("artistName", "")})
            for v in node.values():
                collect_api(v)
        elif isinstance(node, list):
            for v in node:
                collect_api(v)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(user_agent=UA, locale="de-DE")
            # Antworten von Apples API mitschneiden (werden unten ausgewertet)
            page.on("response", lambda r: responses.append(r) if "amp-api" in r.url else None)
            page.goto(url, wait_until="domcontentloaded", timeout=60_000)

            try:
                page.wait_for_selector(title_sel, timeout=30_000)
                # Bei langen Listen nach unten scrollen, bis keine neuen Titel mehr kommen
                last = -1
                for _ in range(60):
                    count = page.locator(title_sel).count()
                    if count == last:
                        break
                    last = count
                    page.locator(title_sel).last.scroll_into_view_if_needed()
                    page.wait_for_timeout(1000)

                # Deine Klassen auslesen (Playwright durchsucht auch offene Shadow-DOMs)
                items = page.locator(".audio-tracklist-item__metadata")
                for i in range(items.count()):
                    item = items.nth(i)
                    t = item.locator(title_sel)
                    s = item.locator(".audio-tracklist-item__subheadline")
                    title = t.first.inner_text().strip() if t.count() else ""
                    artist = s.first.inner_text().strip() if s.count() else ""
                    if title:
                        dom_tracks.append({"title": title, "artist": artist})
            except PWTimeout:
                log.warning("Tracklist-Elemente nicht gefunden – nutze nur die API-Daten.")

            for r in responses:
                try:
                    collect_api(r.json())
                except Exception:
                    pass
        finally:
            browser.close()

    api_list = list(api_tracks.values())
    log.info("Browser: %d Titel aus HTML-Klassen, %d aus API-Daten.", len(dom_tracks), len(api_list))
    return unique(api_list if len(api_list) >= len(dom_tracks) else dom_tracks)


def get_apple_tracks(expected: int = 0) -> list[dict]:
    """Holt die Apple-Titel. Liefert der sparsame Weg auffällig weniger Titel als beim
    letzten Lauf, wird zusätzlich der Browser versucht und die längere Liste genommen."""
    method = getattr(config, "APPLE_METHOD", "auto")
    candidates: list[list[dict]] = []

    if method in ("auto", "static"):
        try:
            tracks = apple_static(config.APPLE_URL)
        except requests.RequestException as e:
            log.warning("Abruf ohne Browser fehlgeschlagen: %s", e)
            tracks = []
        if method == "static":
            return tracks
        if tracks and (not expected or len(tracks) >= expected * 0.9):
            log.info("%d Titel ohne Browser gelesen.", len(tracks))
            return tracks
        if tracks:
            log.warning("Ohne Browser nur %d Titel (letzter Lauf: %d) – prüfe zusätzlich per Browser.",
                        len(tracks), expected)
        else:
            log.info("Ohne Browser keine Titel gefunden.")
        candidates.append(tracks)

    log.info("Starte Browser …")
    try:
        candidates.append(apple_browser(config.APPLE_URL))
    except Exception as e:
        if not any(candidates):
            raise
        log.warning("Browser fehlgeschlagen: %s", e)

    best = max(candidates, key=len)
    log.info("%d Titel aus Apple Music gelesen.", len(best))
    return best


# ───────────────────────── Spotify ─────────────────────────

class Spotify:
    API = "https://api.spotify.com/v1"

    def __init__(self):
        self.session = requests.Session()
        self._token: str | None = None
        self._expires = 0.0

    def _request(self, method: str, url: str, **kw) -> dict:
        for _ in range(5):
            r = self.session.request(method, url, timeout=30, **kw)
            if r.status_code == 429:  # Rate-Limit → warten und erneut versuchen
                try:
                    wait = int(r.headers.get("Retry-After", 5))
                except ValueError:
                    wait = 5
                wait = max(1, min(wait, 120))
                log.info("Rate-Limit erreicht, warte %ds …", wait)
                time.sleep(wait)
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"HTTP {r.status_code} bei {method} {url}: {r.text}")
            return r.json() if r.content else {}
        raise RuntimeError(f"Zu viele Rate-Limit-Versuche bei {url}")

    def token_request(self, data: dict) -> dict:
        auth = base64.b64encode(f"{config.SPOTIFY_CLIENT_ID}:{config.SPOTIFY_CLIENT_SECRET}".encode()).decode()
        return self._request("POST", "https://accounts.spotify.com/api/token",
                             data=data, headers={"Authorization": f"Basic {auth}"})

    def access_token(self) -> str:
        if self._token and self._expires > time.time() + 60:
            return self._token
        tokens = load_json(TOKEN_FILE)
        if not tokens.get("refresh_token"):
            raise RuntimeError("Kein Refresh-Token. Bitte zuerst 'python sync.py auth' ausführen.")
        if not (tokens.get("access_token") and tokens.get("expires_at", 0) > time.time() + 60):
            res = self.token_request({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
            tokens["access_token"] = res["access_token"]
            tokens["expires_at"] = time.time() + int(res.get("expires_in", 3600))
            if res.get("refresh_token"):  # Spotify kann es rotieren
                tokens["refresh_token"] = res["refresh_token"]
            save_json(TOKEN_FILE, tokens)
        self._token, self._expires = tokens["access_token"], tokens["expires_at"]
        return self._token

    def api(self, method: str, path: str, **kw) -> dict:
        url = path if path.startswith("https://") else self.API + path
        return self._request(method, url, headers={"Authorization": f"Bearer {self.access_token()}"}, **kw)

    def search_track(self, title: str, artist: str) -> dict | None:
        want_title, want_artist = clean_title(title), first_artist(artist)
        q_safe = lambda s: s.replace('"', "")
        queries = []
        if want_artist:
            queries.append(f'track:"{q_safe(want_title)}" artist:"{q_safe(want_artist)}"')
        queries.append(f"{want_title} {want_artist}".strip())

        for q in queries:
            res = self.api("GET", "/search?" + urlencode({"q": q, "type": "track", "limit": 10}))
            for t in res.get("tracks", {}).get("items", []):
                if not similar(norm(clean_title(t.get("name", ""))), norm(want_title)):
                    continue
                if not want_artist:
                    return t
                if any(similar(norm(a.get("name", "")), norm(want_artist)) for a in t.get("artists", [])):
                    return t
        return None


def playlist_id() -> str:
    m = re.search(r"playlist[/:]([A-Za-z0-9]+)", config.SPOTIFY_PLAYLIST)
    return m.group(1) if m else config.SPOTIFY_PLAYLIST.strip()


# ───────────────────────── Befehle ─────────────────────────

def cmd_auth() -> None:
    state = secrets.token_hex(8)
    url = "https://accounts.spotify.com/authorize?" + urlencode({
        "client_id": config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
        "scope": "playlist-read-private playlist-read-collaborative "
                 "playlist-modify-public playlist-modify-private",
        "state": state,
    })
    print("1. Öffne diese URL im Browser (auf irgendeinem Gerät) und erlaube den Zugriff:\n")
    print(url + "\n")
    print("2. Danach landest du auf einer Seite, die nicht lädt (127.0.0.1 …). Das ist normal.")
    print("   Kopiere die KOMPLETTE Adresse aus der Adresszeile und füge sie hier ein:\n")
    q = parse_qs(urlparse(input("> ").strip()).query)

    if "error" in q:
        raise RuntimeError(f"Spotify meldet: {q['error'][0]}")
    if q.get("state", [""])[0] != state:
        raise RuntimeError("State stimmt nicht überein. Bitte die URL aus genau diesem Durchlauf verwenden.")
    if "code" not in q:
        raise RuntimeError('Kein "code" in der URL gefunden.')

    res = Spotify().token_request({
        "grant_type": "authorization_code",
        "code": q["code"][0],
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
    })
    save_json(TOKEN_FILE, {
        "access_token": res["access_token"],
        "refresh_token": res["refresh_token"],
        "expires_at": time.time() + int(res.get("expires_in", 3600)),
    })
    print(f"\nFertig! Token gespeichert in {TOKEN_FILE}")


def cmd_apple() -> int:
    state = load_json(STATE_FILE)
    tracks = get_apple_tracks(int(state.get("last_apple_count", 0)))
    print(json.dumps(tracks, ensure_ascii=False, indent=2))
    return 0 if tracks else 2


def load_playlist(sp: Spotify, pid: str) -> dict[str, dict]:
    """Alle Songs der Spotify-Playlist: {uri: {"key", "label"}}"""
    out: dict[str, dict] = {}
    next_url: str | None = f"/playlists/{pid}/items?limit=50"
    while next_url:
        page = sp.api("GET", next_url)
        for row in page.get("items", []):
            obj = row.get("item") or row.get("track")  # "item" seit Feb. 2026
            if not obj or not obj.get("uri"):
                continue
            name = obj.get("name", "")
            artist = (obj.get("artists") or [{}])[0].get("name", "")
            out[obj["uri"]] = {"key": track_key(name, artist), "label": f"{name} – {artist}"}
        next_url = page.get("next")
    return out


def backup(playlist: dict[str, dict], removed: list[str]) -> Path:
    BACKUP_DIR.mkdir(exist_ok=True)
    f = BACKUP_DIR / f"{time.strftime('%Y-%m-%d_%H%M%S')}.json"
    save_json(f, {
        "removed": [{"uri": u, "label": playlist[u]["label"]} for u in removed],
        "playlist_before": [{"uri": u, "label": v["label"]} for u, v in playlist.items()],
    })
    for old in sorted(BACKUP_DIR.glob("*.json"))[:-30]:  # die letzten 30 behalten
        old.unlink()
    return f


def cmd_sync(dry_run: bool, force: bool) -> int:
    lock = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.error("Ein anderer Sync-Lauf ist noch aktiv.")
        return 1

    state = load_json(STATE_FILE)
    last_count = int(state.get("last_apple_count", 0))

    # ── 1. Apple-Titel holen (mit Failover) ──
    apple_tracks = get_apple_tracks(last_count)
    if not apple_tracks:
        log.error("Keine Titel von Apple Music erhalten – breche ab, an Spotify wird nichts geändert.")
        return 2

    warnings: list[str] = []
    max_drop = getattr(config, "MAX_DROP_PERCENT", 20)
    if last_count and len(apple_tracks) < last_count * (1 - max_drop / 100):
        warnings.append(f"Apple liefert nur {len(apple_tracks)} Titel, beim letzten Lauf waren es {last_count}.")

    sp = Spotify()
    pid = quote(playlist_id())
    playlist = load_playlist(sp, pid)
    log.info("%d Songs aktuell in der Spotify-Playlist.", len(playlist))

    # ── 2. Neue Titel suchen ──
    cache = load_json(CACHE_FILE)
    retry_after = config.RETRY_NOT_FOUND_DAYS * 86400
    existing_uris = set(playlist)
    existing_keys = {v["key"] for v in playlist.values()}
    to_add: dict[str, tuple[str, str]] = {}  # uri -> (label, key)
    not_found: list[str] = []

    for t in apple_tracks:
        title, artist = t.get("title", ""), t.get("artist", "")
        key = track_key(title, artist)
        if not title or key in existing_keys:
            continue

        entry = cache.get(key)
        fresh = entry and (entry["uri"] or time.time() - entry["checked"] < retry_after)
        if not fresh:
            found = sp.search_track(title, artist)
            entry = {"uri": found["uri"] if found else "", "checked": time.time(),
                     "title": title, "artist": artist}
            cache[key] = entry
            time.sleep(0.2)  # API schonen

        label = f"{title} – {artist}"
        if not entry["uri"]:
            not_found.append(label)
        elif entry["uri"] not in existing_uris:
            to_add[entry["uri"]] = (label, key)
            existing_uris.add(entry["uri"])

    save_json(CACHE_FILE, cache)

    # ── 3. Herausfinden, was entfernt werden soll ──
    # Nur Songs, die aus Apple stammen ("managed"). Manuell in Spotify
    # hinzugefügte Songs, die nie in der Apple-Playlist waren, bleiben immer.
    apple_keys = {track_key(t.get("title", ""), t.get("artist", "")) for t in apple_tracks}
    apple_uris = {cache[k]["uri"] for k in apple_keys if cache.get(k, {}).get("uri")}

    managed: dict[str, str] = state.get("managed", {})
    for uri, info in playlist.items():
        if uri in apple_uris or info["key"] in apple_keys:
            managed[uri] = info["key"]
    for uri, (_, key) in to_add.items():
        managed[uri] = key
    managed = {u: k for u, k in managed.items() if u in playlist or u in to_add}

    to_remove: list[str] = []
    if getattr(config, "REMOVE_MISSING", True):
        to_remove = [u for u, k in managed.items()
                     if u in playlist and u not in apple_uris and k not in apple_keys]

    max_remove = getattr(config, "MAX_REMOVE_COUNT", 10)
    if len(to_remove) > max_remove:
        warnings.append(f"{len(to_remove)} Songs würden entfernt (Limit: {max_remove}).")

    blocked = bool(to_remove) and bool(warnings) and not force

    # ── 4. Ausgabe ──
    for w in warnings:
        log.warning("WARNUNG: %s", w)
    for label, _ in to_add.values():
        log.info("%s%s", "[Testlauf] würde hinzufügen: " if dry_run else "Neu: ", label)
    for u in to_remove:
        prefix = "[Blockiert] würde entfernen: " if blocked else (
            "[Testlauf] würde entfernen: " if dry_run else "Entfernt: ")
        log.info("%s%s", prefix, playlist[u]["label"])
    for label in not_found:
        log.info("Nicht auf Spotify gefunden: %s", label)
    if blocked:
        log.warning("Zur Sicherheit wurde NICHTS entfernt. Wenn das so gewollt ist: "
                    "'python sync.py sync --force' ausführen.")

    # ── 5. Ausführen ──
    if not dry_run:
        uris = list(to_add)
        for i in range(0, len(uris), 100):
            sp.api("POST", f"/playlists/{pid}/items", json={"uris": uris[i:i + 100]})

        if to_remove and not blocked:
            f = backup(playlist, to_remove)
            log.info("Backup gespeichert: %s", f)
            for i in range(0, len(to_remove), 100):
                sp.api("DELETE", f"/playlists/{pid}/items",
                       json={"items": [{"uri": u} for u in to_remove[i:i + 100]]})
            for u in to_remove:
                managed.pop(u, None)

        state["managed"] = managed
        if not blocked:  # bei Verdacht den alten Vergleichswert behalten
            state["last_apple_count"] = len(apple_tracks)
        save_json(STATE_FILE, state)

    removed = 0 if (blocked or dry_run) else len(to_remove)
    log.info("Fertig: %d hinzugefügt, %d entfernt, %d nicht gefunden%s.",
             0 if dry_run else len(to_add), removed, len(not_found),
             " (Testlauf, nichts geändert)" if dry_run else "")
    return 3 if blocked else 0


def cmd_restore(file: str) -> int:
    data = load_json(Path(file))
    removed = [r["uri"] for r in data.get("removed", [])]
    if not removed:
        log.error("Keine entfernten Songs in %s gefunden.", file)
        return 1
    sp = Spotify()
    pid = quote(playlist_id())
    current = load_playlist(sp, pid)
    uris = [u for u in removed if u not in current]
    for i in range(0, len(uris), 100):
        sp.api("POST", f"/playlists/{pid}/items", json={"uris": uris[i:i + 100]})
    log.info("%d Songs wiederhergestellt.", len(uris))
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    parser = argparse.ArgumentParser(description="Apple Music → Spotify Playlist-Sync")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("auth", help="einmalig bei Spotify anmelden")
    sub.add_parser("apple", help="Apple-Titel als JSON ausgeben")
    p_sync = sub.add_parser("sync", help="Spotify-Playlist mit Apple abgleichen")
    p_sync.add_argument("--dry-run", action="store_true", help="nur anzeigen, nichts ändern")
    p_sync.add_argument("--force", action="store_true", help="Sicherheitsgrenzen beim Entfernen ignorieren")
    p_restore = sub.add_parser("restore", help="entfernte Songs aus einem Backup wiederherstellen")
    p_restore.add_argument("file", help="Backup-Datei aus dem Ordner backups/")
    args = parser.parse_args()

    try:
        if args.cmd == "auth":
            cmd_auth()
            return 0
        if args.cmd == "apple":
            return cmd_apple()
        if args.cmd == "restore":
            return cmd_restore(args.file)
        return cmd_sync(args.dry_run, args.force)
    except Exception as e:
        log.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
