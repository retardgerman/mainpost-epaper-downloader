#!/bin/bash
# Setup-Script für Mainpost ePaper Downloader

set -e

echo "=== Mainpost ePaper Downloader Setup ==="
echo

# Prüfe Python
if ! command -v python3 &> /dev/null; then
    echo "ERROR: Python3 nicht gefunden. Bitte installiere Python 3.8+"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "Python Version: $PYTHON_VERSION"

# Virtual Environment erstellen
if [ ! -d "venv" ]; then
    echo "Erstelle Virtual Environment..."
    python3 -m venv venv
fi

# Aktivieren
source venv/bin/activate

# Abhängigkeiten installieren
echo "Installiere Abhängigkeiten..."
pip install --upgrade pip
pip install -r requirements.txt

# Playwright Browser installieren
echo "Installiere Playwright Chromium..."
playwright install chromium

# Verzeichnisse erstellen
mkdir -p downloads logs

echo
echo "=== Setup abgeschlossen ==="
echo
echo "Nächste Schritte:"
echo "1. Bearbeite config.yaml mit deinen Zugangsdaten"
echo "2. Führe aus: source venv/bin/activate && python downloader.py"
