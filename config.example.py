# Konfiguration für den Apple-Music → Spotify Sync

# Aus dem Spotify Developer Dashboard (developer.spotify.com/dashboard)
SPOTIFY_CLIENT_ID = "DEINE_CLIENT_ID"
SPOTIFY_CLIENT_SECRET = "DEIN_CLIENT_SECRET"

# Muss EXAKT so im Dashboard unter "Redirect URIs" eingetragen sein
SPOTIFY_REDIRECT_URI = "http://127.0.0.1:8888/callback"

# Ziel-Playlist: ID oder kompletter Link (open.spotify.com/playlist/...)
# Die Playlist muss dir gehören (oder du musst Mitwirkender sein)
SPOTIFY_PLAYLIST = "DEINE_SPOTIFY_PLAYLIST_ID"

# Deine Apple-Music-Playlist (Embed-Link)
APPLE_URL = "https://embed.music.apple.com/de/playlist/name/pl.u-XXXXXXXX"

# "auto"    = erst ohne Browser versuchen, nur wenn das nichts liefert Playwright starten
# "browser" = immer Playwright verwenden (zuverlässiger, braucht kurz mehr RAM)
# "static"  = nie den Browser starten
APPLE_METHOD = "auto"

# Nicht gefundene Titel erst nach so vielen Tagen erneut auf Spotify suchen
RETRY_NOT_FOUND_DAYS = 7

# ── Entfernen & Sicherheit ──
# Songs aus Spotify entfernen, die nicht mehr in der Apple-Playlist sind.
# Betrifft nur Songs, die aus Apple stammen – manuell in Spotify hinzugefügte bleiben.
REMOVE_MISSING = True

# Würden mehr Songs auf einmal entfernt, wird zur Sicherheit nichts entfernt
MAX_REMOVE_COUNT = 10

# Liefert Apple plötzlich so viel % weniger Titel als beim letzten Lauf,
# wird ebenfalls nichts entfernt (Hinzufügen läuft trotzdem weiter)
MAX_DROP_PERCENT = 20
