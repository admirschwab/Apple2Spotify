# Apple Music → Spotify Playlist-Sync

Gleicht eine Apple-Music-Playlist automatisch mit einer Spotify-Playlist ab. Das Script liest die Titel aus einem Apple-Music-Embed-Link aus, sucht sie auf Spotify und hält die Spotify-Playlist auf demselben Stand: Neue Songs werden hinzugefügt, Songs, die aus der Apple-Playlist verschwinden, werden entfernt. Gedacht ist es für den unbeaufsichtigten Betrieb als täglicher Cronjob auf einem Server.

## Funktionen

- **Neue Songs hinzufügen:** Titel aus der Apple-Playlist, die noch nicht in der Spotify-Playlist sind, werden gesucht und hinzugefügt. Bereits vorhandene Songs werden nicht doppelt eingefügt, auch nicht in einer anderen Version (z. B. Album- statt Single-Version).
- **Entfernte Songs löschen:** Verschwindet ein Song aus der Apple-Playlist, wird er auch aus Spotify entfernt. Songs, die du direkt in Spotify hinzugefügt hast und die nie in der Apple-Playlist waren, bleiben immer unangetastet.
- **Zwei Methoden zum Auslesen:** Zuerst wird die Apple-Seite sparsam ohne Browser gelesen. Liefert das nichts oder auffällig wenig, startet das Script per Playwright einen unsichtbaren Chromium-Browser, der die Seite vollständig rendert.
- **Schutz vor Fehlern:** Liefert Apple plötzlich kaputte oder unvollständige Daten, wird nichts gelöscht. Vor jedem Löschen wird ein Backup angelegt, das sich wiederherstellen lässt.
- **Such-Cache:** Ergebnisse der Spotify-Suche werden gespeichert, damit nicht bei jedem Lauf die ganze Playlist neu gesucht wird.
- **Sonderzeichen:** Umlaute und Akzente (ä, ö, ü, ß, é, …) werden korrekt verarbeitet. Falsch kodierter Text wird automatisch repariert.

## Voraussetzungen

- **Linux-Server** (vServer, Raspberry Pi o. Ä.) mit Shell-Zugang. Ein reines PHP-Webhosting reicht nicht.
- **Python 3.9 oder neuer**
- **Etwa 1 GB RAM** für den Fall, dass der Browser gestartet wird. Chromium braucht kurzzeitig etwa 150–300 MB und wird danach sofort wieder beendet.
- **Einige hundert MB Speicherplatz** für Chromium
- **Spotify Premium:** Seit Februar 2026 setzt Spotify für Apps im Entwicklermodus ein aktives Premium-Abo des App-Besitzers voraus. Läuft das Abo aus, funktioniert der Sync nicht mehr, bis es verlängert wird.
- Die **Spotify-Ziel-Playlist muss dir gehören** (oder du musst Mitwirkender sein). Fremde Playlists kann die API nicht auslesen.
- Die **Apple-Music-Playlist muss öffentlich geteilt** sein.

## Dateien

| Datei | Zweck |
|---|---|
| `sync.py` | Das eigentliche Script mit allen Befehlen |
| `config.example.py` | Vorlage für die Einstellungen, ohne Zugangsdaten. Wird im Repository mitgeliefert |
| `config.py` | Deine Einstellungen und Zugangsdaten. Wird aus der Vorlage erstellt und **nicht** ins Repository übernommen |
| `.gitignore` | Sorgt dafür, dass Zugangsdaten und persönliche Daten nicht ins Repository gelangen |
| `requirements.txt` | Benötigte Python-Pakete |
| `README.md` | Diese Anleitung |

## Einrichtung

### 1. Projekt auf den Server holen

Lege den Ordner an einem Ort **außerhalb des Webroots** ab, z. B. in deinem Home-Verzeichnis. In `config.py` und den später erzeugten Token-Dateien stehen Zugangsdaten, die niemand über den Browser abrufen können darf.

Per Git:

```bash
cd ~
git clone <URL-DEINES-REPOSITORYS> apple2spotify
cd apple2spotify
```

Oder ohne Git, indem du die Dateien von Hand in einen Ordner kopierst:

```bash
mkdir -p ~/apple2spotify
cd ~/apple2spotify
# sync.py, config.example.py und requirements.txt hierher kopieren
```

### 2. Python-Umgebung und Pakete installieren

```bash
cd ~/apple2spotify
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Falls `python3 -m venv` fehlschlägt, fehlt meist das venv-Paket. Unter Debian/Ubuntu installierst du es mit `sudo apt install python3-venv`.

### 3. Chromium für Playwright installieren

Mit root-Rechten installiert dieser Befehl Chromium samt aller benötigten Systembibliotheken:

```bash
playwright install --with-deps chromium
```

Ohne root-Rechte geht es in zwei Schritten:

```bash
playwright install chromium
sudo venv/bin/playwright install-deps chromium
```

> **Wichtig:** Playwright legt Chromium im Home-Verzeichnis des Benutzers ab, der den Befehl ausführt. Der Cronjob muss später unter **demselben Benutzer** laufen.

### 4. Spotify-App anlegen

1. Öffne das [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) und melde dich mit deinem Spotify-Konto an.
2. Klicke auf **Create app**.
3. Vergib einen beliebigen Namen und eine Beschreibung.
4. Trage unter **Redirect URIs** genau diese Adresse ein und klicke auf **Add**:
   ```
   http://127.0.0.1:8888/callback
   ```
   Spotify erlaubt keine `localhost`-Adressen mehr. Es muss `127.0.0.1` sein.
5. Wähle bei den APIs **Web API** aus.
6. Speichere die App und öffne danach ihre **Settings**. Dort findest du die **Client ID** und, nach Klick auf „View client secret“, das **Client Secret**.

### 5. Konfiguration ausfüllen

Erstelle deine eigene Konfiguration aus der Vorlage und schütze sie vor dem Zugriff anderer Benutzer auf dem Server:

```bash
cp config.example.py config.py
chmod 600 config.py
```

Bearbeite danach **nur `config.py`**, nicht die Vorlage. `config.py` wird durch die `.gitignore` nicht ins Repository übernommen, deine Zugangsdaten bleiben also auf dem Server. Öffne `config.py` und trage deine Daten ein:

| Einstellung | Beschreibung |
|---|---|
| `SPOTIFY_CLIENT_ID` | Client ID aus dem Dashboard |
| `SPOTIFY_CLIENT_SECRET` | Client Secret aus dem Dashboard |
| `SPOTIFY_REDIRECT_URI` | Muss exakt der Adresse im Dashboard entsprechen. Standard: `http://127.0.0.1:8888/callback` |
| `SPOTIFY_PLAYLIST` | ID oder kompletter Link der Ziel-Playlist, z. B. `https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M` |
| `APPLE_URL` | Embed-Link deiner Apple-Music-Playlist, z. B. `https://embed.music.apple.com/de/playlist/name/pl.u-XXXXXXXX` |
| `APPLE_METHOD` | `"auto"` (Standard): erst ohne Browser, bei Bedarf mit. `"browser"`: immer Browser. `"static"`: nie Browser |
| `RETRY_NOT_FOUND_DAYS` | Nach wie vielen Tagen nicht gefundene Titel erneut gesucht werden. Standard: `7` |
| `REMOVE_MISSING` | `True`: Songs entfernen, die nicht mehr in der Apple-Playlist sind. `False`: nur hinzufügen |
| `MAX_REMOVE_COUNT` | Würden mehr Songs auf einmal entfernt, wird nichts entfernt. Standard: `10` |
| `MAX_DROP_PERCENT` | Liefert Apple so viel Prozent weniger Titel als beim letzten Lauf, wird nichts entfernt. Standard: `20` |

**Den Spotify-Playlist-Link** bekommst du in der Spotify-App über die drei Punkte an der Playlist → **Teilen** → **Link zur Playlist kopieren**.

**Den Apple-Embed-Link** bekommst du in Apple Music über die drei Punkte an der Playlist → **Teilen** → **Einbetten**. Aus dem angezeigten Code brauchst du nur die Adresse, die mit `https://embed.music.apple.com/` beginnt. Alternativ kannst du auch den normalen Teilen-Link (`https://music.apple.com/...`) verwenden und `music.` durch `embed.music.` ersetzen.

### 6. Einmalig bei Spotify anmelden

```bash
cd ~/apple2spotify
source venv/bin/activate
python sync.py auth
```

Das Script zeigt eine lange Adresse an. So geht es weiter:

1. Öffne die Adresse in einem beliebigen Browser, auch auf deinem PC oder Handy. Der Browser muss nicht auf dem Server laufen.
2. Melde dich bei Spotify an und erlaube den Zugriff.
3. Du landest auf einer Seite, die nicht lädt (`127.0.0.1` …). **Das ist normal.**
4. Kopiere die **komplette Adresse aus der Adresszeile** und füge sie im Terminal ein.

Danach wird die Datei `spotify_tokens.json` angelegt. Ab jetzt erneuert das Script seinen Zugang selbstständig, die Anmeldung ist also nur einmal nötig.

### 7. Testen

Zuerst prüfen, ob das Auslesen von Apple Music funktioniert:

```bash
python sync.py apple
```

Du solltest eine Liste aller Titel als JSON sehen. Fehlen Titel, stell in `config.py` testweise `APPLE_METHOD = "browser"` ein und probiere es erneut.

Dann einen Testlauf machen, der nichts verändert:

```bash
python sync.py sync --dry-run
```

Im Log steht, welche Songs hinzugefügt und welche entfernt würden. Passt alles, führst du den ersten echten Lauf aus:

```bash
python sync.py sync
```

### 8. Cronjob einrichten

Öffne die Cron-Tabelle des Benutzers, unter dem du Playwright installiert hast:

```bash
crontab -e
```

Füge diese Zeile ein, damit der Sync jeden Tag um 6 Uhr läuft (Pfad anpassen):

```
0 6 * * * cd /home/BENUTZER/apple2spotify && venv/bin/python sync.py sync >> sync.log 2>&1
```

Alle Meldungen landen dann in `sync.log` im Projektordner.

## Befehle

| Befehl | Beschreibung |
|---|---|
| `python sync.py auth` | Einmalige Anmeldung bei Spotify |
| `python sync.py apple` | Gibt nur die Apple-Titel als JSON aus, ohne Spotify zu ändern |
| `python sync.py sync` | Gleicht die Spotify-Playlist mit Apple ab |
| `python sync.py sync --dry-run` | Zeigt nur an, was passieren würde, und ändert nichts |
| `python sync.py sync --force` | Ignoriert die Sicherheitsgrenzen beim Entfernen |
| `python sync.py restore backups/DATEI.json` | Holt entfernte Songs aus einem Backup zurück |

## So funktioniert es

### Auslesen der Apple-Playlist

Die Apple-Embed-Seite wird im Browser per JavaScript aufgebaut. Ein einfacher Abruf liefert deshalb nicht die sichtbare Titelliste. Das Script nutzt zwei Wege:

1. **Ohne Browser:** Es ruft die normale Seite `music.apple.com/...` ab und liest die eingebetteten Daten aus, die Apple für die Seite und für Suchmaschinen mitliefert. Das ist schnell und braucht kaum Ressourcen.
2. **Mit Browser (Playwright):** Ein unsichtbarer Chromium lädt die Embed-Seite vollständig. Das Script liest dann die Titel aus den HTML-Elementen (`audio-tracklist-item__title-text`, `audio-tracklist-item__subheadline`) und schneidet zusätzlich die Daten mit, die die Seite im Hintergrund von Apples Server lädt. Bei langen Playlists scrollt es nach unten, bis keine weiteren Titel nachgeladen werden. Verwendet wird die vollständigere der beiden Listen.

Im Modus `"auto"` wird der Browser nur gestartet, wenn der erste Weg keine Titel liefert oder deutlich weniger als beim letzten Lauf.

### Suche auf Spotify

Für jeden neuen Titel sucht das Script auf Spotify nach Titel und Hauptkünstler. Zusätze wie „(feat. …)“, „[Remastered]“ oder „- Radio Edit“ werden für den Vergleich ignoriert. Ein Treffer zählt nur, wenn Titel **und** Künstler passen. Das Ergebnis wird in `match_cache.json` gespeichert, damit dieselbe Suche nicht täglich wiederholt wird.

### Abgleich

- **Hinzufügen:** Ein Song wird hinzugefügt, wenn er weder mit derselben Spotify-ID noch mit demselben Titel und Künstler in der Playlist steht.
- **Entfernen:** Das Script merkt sich in `sync_state.json`, welche Songs der Spotify-Playlist aus der Apple-Playlist stammen. Nur diese Songs werden entfernt, wenn sie aus der Apple-Playlist verschwinden. Songs, die schon vor der Einrichtung in der Spotify-Playlist standen und auch in der Apple-Playlist vorkommen, werden beim ersten Lauf automatisch zugeordnet.

## Sicherheitsmechanismen

Ändert Apple die Struktur der Seite, könnte das Script eine leere oder unvollständige Liste bekommen. Ohne Schutz würde es dann fälschlich große Teile der Spotify-Playlist löschen. Deshalb gilt:

1. **Keine Titel gefunden:** Der Lauf bricht ab, an Spotify wird nichts geändert.
2. **Automatischer Methodenwechsel:** Liefert der Weg ohne Browser deutlich weniger Titel als beim letzten Lauf, wird zusätzlich der Browser versucht.
3. **Auffälliger Einbruch:** Liefert Apple mehr als `MAX_DROP_PERCENT` Prozent weniger Titel als beim letzten erfolgreichen Lauf, wird nichts entfernt.
4. **Zu viele Löschungen:** Würden mehr als `MAX_REMOVE_COUNT` Songs auf einmal entfernt, wird nichts entfernt.
5. **Backup:** Vor jedem Entfernen wird der Zustand der Playlist in `backups/` gespeichert. Die letzten 30 Backups bleiben erhalten.

Bei Punkt 3 und 4 werden neue Songs trotzdem hinzugefügt, weil das keinen Schaden anrichtet. Im Log steht genau, welche Songs entfernt worden wären.

### Wenn du absichtlich viele Songs entfernt hast

Dann greift der Schutz natürlich auch. Prüfe im Log die Zeilen mit `[Blockiert] würde entfernen:`. Stimmt die Liste, führst du einmal aus:

```bash
python sync.py sync --force
```

### Versehentlich entfernte Songs zurückholen

```bash
ls backups/
python sync.py restore backups/2026-11-02_060012.json
```

Wiederhergestellte Songs gelten danach als manuell hinzugefügt und werden beim nächsten Lauf nicht erneut entfernt.

## Exit-Codes

Für Überwachung oder Cron-Benachrichtigungen:

| Code | Bedeutung |
|---|---|
| `0` | Erfolgreich |
| `1` | Fehler (z. B. Netzwerk, Anmeldung, Konfiguration) |
| `2` | Keine Titel von Apple Music erhalten, nichts geändert |
| `3` | Entfernen wurde durch eine Sicherheitsgrenze blockiert |

## Erzeugte Dateien

| Datei / Ordner | Inhalt |
|---|---|
| `spotify_tokens.json` | Spotify-Zugangstoken. **Vertraulich, nicht weitergeben** |
| `match_cache.json` | Gespeicherte Suchergebnisse. Darf gelöscht werden, dann wird neu gesucht |
| `sync_state.json` | Zuordnung der Songs und Titelanzahl des letzten Laufs. Nur löschen, wenn du die Zuordnung zurücksetzen willst |
| `backups/` | Sicherungen vor jedem Entfernen |
| `sync.lock` | Verhindert, dass zwei Läufe gleichzeitig arbeiten |
| `sync.log` | Log-Ausgabe des Cronjobs (wenn wie oben eingerichtet) |

## Fehlerbehebung

**„Kein Refresh-Token. Bitte zuerst 'python sync.py auth' ausführen.“**
Die Anmeldung fehlt oder `spotify_tokens.json` wurde gelöscht. Führe `python sync.py auth` aus.

**„INVALID_CLIENT: Invalid redirect URI“ bei der Anmeldung**
Die Redirect URI in `config.py` stimmt nicht exakt mit der im Spotify-Dashboard überein. Achte auf `127.0.0.1` statt `localhost` und auf den genauen Pfad `/callback`.

**HTTP 403 bei Spotify-Anfragen**
Prüfe, ob dein Spotify-Premium-Abo aktiv ist und ob die Ziel-Playlist dir gehört.

**HTTP 400 oder 401 beim Erneuern des Tokens**
Der Zugang wurde widerrufen, z. B. weil du der App in deinen Spotify-Kontoeinstellungen den Zugriff entzogen hast. Lösche `spotify_tokens.json` und führe `python sync.py auth` erneut aus.

**„Playwright ist nicht installiert“ oder Browser startet nicht**
Prüfe, ob das venv aktiv ist bzw. der Cronjob `venv/bin/python` verwendet und ob `playwright install chromium` unter demselben Benutzer ausgeführt wurde.

**„Keine Titel von Apple Music erhalten“**
Prüfe, ob die Apple-Playlist öffentlich geteilt ist und der Link in `config.py` stimmt. Teste mit `python sync.py apple` und probiere `APPLE_METHOD = "browser"`. Funktioniert beides nicht, hat Apple wahrscheinlich den Aufbau der Seite geändert.

**Ein Song wird nicht gefunden**
Manche Songs gibt es nicht auf Spotify oder sie heißen dort anders. Nicht gefundene Titel stehen im Log und werden nach `RETRY_NOT_FOUND_DAYS` Tagen erneut gesucht. Du kannst den Song auch manuell in Spotify hinzufügen. Er wird dann beim nächsten Lauf als vorhanden erkannt.

**Ein falscher Song wurde hinzugefügt**
Die Zuordnung läuft über eine Suche nach Titel und Künstler. Bei Live-Versionen, Remixes oder sehr allgemeinen Titeln kann es den falschen Treffer geben. Entferne den Eintrag mit dem passenden Titel aus `match_cache.json` und den falschen Song aus der Spotify-Playlist. Beim nächsten Lauf wird neu gesucht.

## Bekannte Einschränkungen

- **Manuell entfernte Songs:** Entfernst du einen Song in Spotify, der noch in der Apple-Playlist steht, fügt das Script ihn beim nächsten Lauf wieder hinzu. Die Apple-Playlist ist die führende Quelle.
- **Abhängigkeit von Apple:** Das Auslesen beruht auf dem aktuellen Aufbau der Apple-Music-Seiten. Apple kann diesen jederzeit ändern. Die Sicherheitsmechanismen verhindern dann Schäden an der Spotify-Playlist, aber neue Songs werden erst wieder übernommen, wenn das Script angepasst ist.
- **Spotify-API-Grenzen:** Im Entwicklermodus darf eine Spotify-App von höchstens 5 Benutzern verwendet werden. Für den privaten Einsatz spielt das keine Rolle.
- **Nutzungsbedingungen:** Das automatisierte Auslesen von Apple-Music-Seiten ist von Apple nicht offiziell vorgesehen. Verwende das Script nur für deine eigenen Playlists.

## Git und Zugangsdaten

Die mitgelieferte `.gitignore` schließt alle Dateien mit Zugangsdaten und persönlichen Daten aus: `config.py`, `spotify_tokens.json`, `sync_state.json`, `match_cache.json` und den Ordner `backups/`. Außerdem werden Logs, die Python-Umgebung (`venv/`) und typische Editor-Dateien ignoriert.

Ins Repository gehört nur die Vorlage `config.example.py` mit Platzhaltern. Kommt eine neue Einstellung hinzu, trägst du sie mit einem Platzhalter bzw. Standardwert in `config.example.py` ein und übernimmst sie von Hand in deine `config.py`.

### Wenn Zugangsdaten schon committet wurden

Die `.gitignore` wirkt nur für Dateien, die Git noch nicht verfolgt. Hast du `config.py` oder `spotify_tokens.json` bereits committet, nimm sie aus dem Repository, ohne sie lokal zu löschen:

```bash
git rm --cached config.py spotify_tokens.json
git commit -m "Zugangsdaten aus dem Repository entfernen"
```

Die Daten stehen danach aber weiterhin in der Git-Historie. War das Repository öffentlich oder hatten andere Zugriff darauf, mach Folgendes:

1. Erzeuge im [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) in den Settings deiner App ein neues Client Secret (**Rotate client secret**) und trag es in `config.py` ein.
2. Entziehe der App in deinen Spotify-Kontoeinstellungen unter **Apps verwalten** den Zugriff. Damit wird das alte Token ungültig.
3. Lösche `spotify_tokens.json` und melde dich mit `python sync.py auth` neu an.
