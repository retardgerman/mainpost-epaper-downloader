# Mainpost ePaper Downloader

Automatisiertes Tool zum Herunterladen von ePaper-PDFs von epaper.mainpost.de

## Features

- Browser-Automatisierung mit Playwright (umgeht Bot-Erkennung)
- Parallele Downloads (konfigurierbar)
- Retry-Logik bei Fehlern
- Skip bereits heruntergeladener Dateien
- Progress-Anzeige
- Filter nach Datum und Edition
- Logging in Datei
- YAML-Konfigurationsdatei

## Installation

```bash
# In das Verzeichnis wechseln
cd mainpost-epaper-downloader

# Virtual Environment erstellen (empfohlen)
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# oder: venv\Scripts\activate  # Windows

# Abhängigkeiten installieren
pip install -r requirements.txt

# Playwright Browser installieren
playwright install chromium
```

## Konfiguration

Bearbeite `config.yaml`:

```yaml
auth:
  username: "deine@email.de"
  password: "dein_passwort"

download:
  output_dir: "./downloads"
  parallel_downloads: 3
  skip_existing: true

filter:
  editions: []  # Leer = alle, oder z.B. ["Würzburg", "Schweinfurt"]
  date_from: "2024-01-01"
  date_to: ""  # Leer = heute
```

## Verwendung

```bash
# Mit Konfigurationsdatei
python downloader.py

# Mit CLI-Argumenten
python downloader.py -u email@example.com -p passwort

# Nur bestimmter Zeitraum
python downloader.py --from-date 2024-01-01 --to-date 2024-01-31

# Mit 5 parallelen Downloads
python downloader.py --parallel 5

# Debug-Modus
python downloader.py --debug
```

## CLI-Optionen

| Option | Beschreibung |
|--------|--------------|
| `-c, --config` | Pfad zur Konfigurationsdatei (Standard: config.yaml) |
| `-u, --username` | Username/E-Mail |
| `-p, --password` | Passwort |
| `-o, --output` | Ausgabeverzeichnis |
| `--parallel` | Anzahl paralleler Downloads |
| `--from-date` | Startdatum (YYYY-MM-DD) |
| `--to-date` | Enddatum (YYYY-MM-DD) |
| `--debug` | Aktiviert Debug-Logging |

## Ausgabe

PDFs werden mit folgendem Namensschema gespeichert:
```
YYYY-MM-DD_Ausgabenname.pdf
```

Beispiel:
```
2024-01-15_Wuerzburg.pdf
2024-01-15_Schweinfurt.pdf
```

## Troubleshooting

### Login schlägt fehl
- Screenshot wird als `login_debug.png` gespeichert
- Prüfe Zugangsdaten in config.yaml
- Website könnte Struktur geändert haben

### Keine Ausgaben gefunden
- Aktiviere Debug-Modus: `--debug`
- Prüfe Log-Datei in `./logs/downloader.log`
- Website-Struktur könnte sich geändert haben - Selektoren müssen ggf. angepasst werden

### Anpassungen für geänderte Website-Struktur

Die Selektoren in `downloader.py` können angepasst werden:

1. **Login-Selektoren** (~Zeile 150): `login_selectors`, `username_selectors`, `password_selectors`
2. **Archiv-Selektoren** (~Zeile 230): `archive_selectors`
3. **Ausgaben-Selektoren** (~Zeile 250): `edition_selectors`
4. **PDF-Download-Selektoren** (~Zeile 350): `pdf_selectors`

## Hinweise

- Du benötigst ein gültiges Mainpost ePaper-Abonnement
- Respektiere die Nutzungsbedingungen des Anbieters
- Bei Problemen: Debug-Modus aktivieren und Log-Datei prüfen
