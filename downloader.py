#!/usr/bin/env python3
"""
Mainpost ePaper Downloader - Optimierte Version ohne Browser

Lädt alle verfügbaren Ausgaben der konfigurierten Regionen herunter.
Unterstützt Archiv-Modus für komplettes Archiv seit 2017.
"""

import asyncio
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import aiohttp
import aiofiles
import yaml
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn, TimeElapsedColumn
from rich.table import Table


@dataclass
class Config:
    """Konfigurationsklasse für den Downloader."""
    output_dir: str = "./downloads"
    parallel_downloads: int = 4
    retry_attempts: int = 3
    retry_delay: int = 5
    timeout: int = 120
    skip_existing: bool = True
    editions: list = field(default_factory=list)
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    log_level: str = "INFO"
    log_file: str = "./logs/downloader.log"
    archive_mode: bool = False  # Komplettes Archiv laden
    scan_parallel: int = 10  # Parallele Scans für Archiv-Modus

    @classmethod
    def from_yaml(cls, path: str) -> "Config":
        """Lädt Konfiguration aus YAML-Datei."""
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        config = cls()
        dl = data.get("download", {})
        config.output_dir = dl.get("output_dir", "./downloads")
        config.parallel_downloads = dl.get("parallel_downloads", 4)
        config.retry_attempts = dl.get("retry_attempts", 3)
        config.retry_delay = dl.get("retry_delay", 5)
        config.timeout = dl.get("timeout", 120)
        config.skip_existing = dl.get("skip_existing", True)

        flt = data.get("filter", {})
        config.editions = flt.get("editions", [])
        date_from = flt.get("date_from", "")
        date_to = flt.get("date_to", "")
        if date_from:
            config.date_from = datetime.strptime(date_from, "%Y-%m-%d")
        if date_to:
            config.date_to = datetime.strptime(date_to, "%Y-%m-%d")

        log = data.get("logging", {})
        config.log_level = log.get("level", "INFO")
        config.log_file = log.get("file", "./logs/downloader.log")

        return config


@dataclass
class Edition:
    """Repräsentiert eine Hauptausgabe."""
    name: str
    issue_id: str
    region_code: str
    date: datetime

    @property
    def filename(self) -> str:
        """Generiert Dateinamen."""
        date_str = self.date.strftime("%Y-%m-%d")
        safe_name = re.sub(r'[^\w\-äöüÄÖÜß]', '_', self.name)
        safe_name = re.sub(r'_+', '_', safe_name).strip('_')
        return f"{date_str}_{safe_name}.pdf"


class MainpostDownloader:
    """Hauptklasse für den ePaper-Downloader."""

    BASE_URL = "https://epaper.mainpost.de"

    # Archiv-Startdatum (PDFs gibt es erst ab diesem Datum)
    ARCHIVE_START_DATE = datetime(2017, 9, 29)

    # Regionen-Codes für Hauptausgaben (keine Prospekte, Rätsel, etc.)
    REGIONS = {
        "MPWUE": "Würzburg",
        "SWTSW": "Schweinfurt",
        "MPMSP": "Main-Spessart",
        "MPKIT": "Kitzingen",
        "BVHHH": "Hofheim",
        "MPGEO": "Gerolzhofen",
        "MPKIS": "Bad Kissingen",
        "MPKOEN": "Bad Königshofen",
        "MPTBB": "Main-Tauber",
        "MPOCH": "Ochsenfurt",
        "MPNES": "Bad Neustadt",
        "RSP": "Rhön-Saale",
        "RSB": "Mellrichstadt",
        "HT": "Haßfurt",
        "OT": "Obermain",
    }

    def __init__(self, config: Config):
        self.config = config
        self.console = Console()
        self.logger = self._setup_logging()
        self.session: Optional[aiohttp.ClientSession] = None
        self.semaphore = asyncio.Semaphore(config.parallel_downloads)
        self.stats = {"total": 0, "downloaded": 0, "skipped": 0, "failed": 0}

    def _setup_logging(self) -> logging.Logger:
        """Richtet Logging ein."""
        logger = logging.getLogger("mainpost")
        logger.setLevel(getattr(logging, self.config.log_level.upper()))
        logger.handlers.clear()

        log_path = Path(self.config.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_path, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(file_handler)

        console_handler = RichHandler(console=self.console, show_time=False, show_path=False)
        console_handler.setLevel(logging.INFO)
        logger.addHandler(console_handler)

        return logger

    def _get_target_regions(self) -> dict[str, str]:
        """Bestimmt welche Regionen heruntergeladen werden sollen."""
        if not self.config.editions:
            return self.REGIONS

        filtered = {}
        for code, name in self.REGIONS.items():
            for edition_filter in self.config.editions:
                if edition_filter.lower() in name.lower():
                    filtered[code] = name
                    break
        return filtered if filtered else self.REGIONS

    async def _fetch_page(self, url: str) -> str:
        """Lädt eine Seite herunter."""
        async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            resp.raise_for_status()
            return await resp.text()

    async def _get_all_editions(self, region_code: str, region_name: str) -> list[Edition]:
        """Holt alle verfügbaren Ausgaben einer Region vom Gridshelf."""
        editions = []
        url = f"{self.BASE_URL}/gridshelf.act?region={region_code}"

        try:
            html = await self._fetch_page(url)

            # Suche nach allen pdfDownloadClickHandler mit dem Region-Code
            # Format: pdfDownloadClickHandler('Name', 'IssueID', 'RegionCode', 'YYYYMMDD')
            pattern = rf"pdfDownloadClickHandler\('([^']+)',\s*'(\d+)',\s*'({region_code})',\s*'(\d{{8}})'\)"
            matches = re.findall(pattern, html)

            seen_ids = set()
            for name, issue_id, _, date_str in matches:
                # Duplikate vermeiden
                if issue_id in seen_ids:
                    continue
                seen_ids.add(issue_id)

                edition_date = datetime.strptime(date_str, "%Y%m%d")
                editions.append(Edition(
                    name=name,
                    issue_id=issue_id,
                    region_code=region_code,
                    date=edition_date
                ))

            # Nach Datum sortieren (neueste zuerst)
            editions.sort(key=lambda e: e.date, reverse=True)

        except Exception as e:
            self.logger.warning(f"Fehler beim Laden von {region_name}: {e}")

        return editions

    async def _get_editions_page(self, region_code: str, date_to: datetime) -> list[Edition]:
        """Holt eine Seite (ca. 10 Ausgaben) vom Gridshelf bis zu einem bestimmten Datum."""
        editions = []
        date_str = date_to.strftime("%Y-%m-%d")
        url = f"{self.BASE_URL}/gridshelf.act?region={region_code}&dateTo={date_str}"

        try:
            html = await self._fetch_page(url)

            # Suche nach allen pdfDownloadClickHandler
            pattern = rf"pdfDownloadClickHandler\('([^']+)',\s*'(\d+)',\s*'({region_code})',\s*'(\d{{8}})'\)"
            matches = re.findall(pattern, html)

            seen_ids = set()
            for name, issue_id, _, date_str in matches:
                if issue_id in seen_ids:
                    continue
                seen_ids.add(issue_id)

                edition_date = datetime.strptime(date_str, "%Y%m%d")
                editions.append(Edition(
                    name=name,
                    issue_id=issue_id,
                    region_code=region_code,
                    date=edition_date
                ))

        except Exception as e:
            self.logger.debug(f"Fehler bei Gridshelf-Seite {date_str}: {e}")

        return editions

    async def _get_all_editions_paginated(self, region_code: str, region_name: str, progress, task_id) -> list[Edition]:
        """Holt ALLE Ausgaben einer Region durch Pagination des Gridshelf."""
        all_editions = []
        seen_ids = set()

        # Startdatum bestimmen
        current_date = self.config.date_to or datetime.now()
        start_date = self.config.date_from or self.ARCHIVE_START_DATE

        while current_date >= start_date:
            page_editions = await self._get_editions_page(region_code, current_date)

            if not page_editions:
                # Keine weiteren Ausgaben gefunden
                break

            new_count = 0
            oldest_date = current_date
            for edition in page_editions:
                if edition.issue_id not in seen_ids:
                    seen_ids.add(edition.issue_id)
                    # Datumsfilter prüfen
                    if self.config.date_from and edition.date < self.config.date_from:
                        continue
                    if self.config.date_to and edition.date > self.config.date_to:
                        continue
                    all_editions.append(edition)
                    new_count += 1
                if edition.date < oldest_date:
                    oldest_date = edition.date

            progress.update(task_id, description=f"Scanne {region_name}... ({len(all_editions)} gefunden)")

            if new_count == 0:
                # Keine neuen Ausgaben - wir haben alles
                break

            # Nächste Seite: einen Tag vor der ältesten gefundenen Ausgabe
            current_date = oldest_date - timedelta(days=1)

            # Kleine Pause um den Server nicht zu überlasten
            await asyncio.sleep(0.2)

        return all_editions

    async def _get_issue_for_date(self, region_code: str, region_name: str, date: datetime) -> Optional[Edition]:
        """Prüft ob es für ein bestimmtes Datum eine Ausgabe gibt und holt die Issue-ID."""
        date_str = date.strftime("%Y%m%d")
        url = f"{self.BASE_URL}/issue.act?issueMutation={region_code}&issueDate={date_str}&region={region_code}"

        try:
            async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=15), allow_redirects=False) as resp:
                # Redirect zur Paywall = keine Ausgabe oder kein Zugang
                if resp.status == 302:
                    location = resp.headers.get('Location', '')
                    if 'paywall' in location.lower():
                        return None
                    # Anderer Redirect - folgen
                    return None

                if resp.status != 200:
                    return None

                html = await resp.text()

                # Issue-ID aus der Seite extrahieren (verschiedene Formate)
                match = re.search(r'issueId=(\d+)', html)
                if not match:
                    match = re.search(r'var issueId = (\d+);', html)
                if not match:
                    return None

                issue_id = match.group(1)

                # Mutations-Name extrahieren (für korrekten Ordnernamen)
                name_match = re.search(r"var mutationName = '([^']+)';", html)
                name = name_match.group(1) if name_match else region_name

                return Edition(
                    name=name,
                    issue_id=issue_id,
                    region_code=region_code,
                    date=date
                )

        except asyncio.TimeoutError:
            self.logger.debug(f"Timeout für {region_code} {date_str}")
            return None
        except Exception as e:
            self.logger.debug(f"Fehler bei {region_code} {date_str}: {e}")
            return None

    async def _scan_date_range(self, region_code: str, region_name: str, dates: list[datetime], progress, task_id) -> list[Edition]:
        """Scannt einen Datumsbereich für eine Region."""
        editions = []
        scan_semaphore = asyncio.Semaphore(self.config.scan_parallel)

        async def scan_date(date: datetime):
            async with scan_semaphore:
                edition = await self._get_issue_for_date(region_code, region_name, date)
                progress.advance(task_id)
                return edition

        tasks = [scan_date(date) for date in dates]
        results = await asyncio.gather(*tasks)

        for edition in results:
            if edition:
                editions.append(edition)

        return editions

    async def get_archive_editions(self) -> list[Edition]:
        """Scannt das komplette Archiv für alle konfigurierten Regionen via Gridshelf-Pagination."""
        all_editions = []
        regions = self._get_target_regions()

        # Datumsbereich bestimmen
        start_date = self.config.date_from or self.ARCHIVE_START_DATE
        end_date = self.config.date_to or datetime.now()

        self.console.print(f"\n[bold]Archiv-Scan:[/bold] {len(regions)} Region(en)")
        self.console.print(f"[dim]Zeitraum: {start_date.strftime('%Y-%m-%d')} bis {end_date.strftime('%Y-%m-%d')}[/dim]\n")

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            TimeElapsedColumn(),
            console=self.console
        ) as progress:
            for code, name in regions.items():
                task_id = progress.add_task(f"Scanne {name}...", total=None)

                region_editions = await self._get_all_editions_paginated(code, name, progress, task_id)
                all_editions.extend(region_editions)

                progress.update(task_id, description=f"[green]✓[/green] {name}: {len(region_editions)} Ausgaben")

        # Nach Datum sortieren (älteste zuerst für Archiv)
        all_editions.sort(key=lambda e: (e.date, e.name))

        self.console.print(f"\n[bold green]Gefunden: {len(all_editions)} Ausgaben[/bold green]\n")

        return all_editions

    async def get_editions(self) -> list[Edition]:
        """Holt alle verfügbaren Ausgaben der konfigurierten Regionen."""
        all_editions = []
        regions = self._get_target_regions()

        self.logger.info(f"Lade alle Ausgaben für {len(regions)} Region(en)...")

        for code, name in regions.items():
            self.logger.info(f"  → {name}...")
            region_editions = await self._get_all_editions(code, name)

            for edition in region_editions:
                # Datumsfilter prüfen
                if self.config.date_from and edition.date < self.config.date_from:
                    continue
                if self.config.date_to and edition.date > self.config.date_to:
                    continue

                all_editions.append(edition)

            if region_editions:
                self.logger.info(f"    ✓ {len(region_editions)} Ausgabe(n) gefunden")
            else:
                self.logger.warning(f"    ✗ Keine Ausgaben gefunden")

        # Nach Datum sortieren (neueste zuerst)
        all_editions.sort(key=lambda e: e.date, reverse=True)

        return all_editions

    async def download_pdf(self, edition: Edition, progress, task_id) -> bool:
        """Lädt eine PDF herunter."""
        # Ordner pro Region
        output_dir = Path(self.config.output_dir) / edition.name
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / edition.filename

        # Skip wenn vorhanden
        if self.config.skip_existing and output_path.exists() and output_path.stat().st_size > 10000:
            self.logger.debug(f"Überspringe {edition.filename} (existiert)")
            self.stats["skipped"] += 1
            progress.advance(task_id)
            return True

        async with self.semaphore:
            for attempt in range(self.config.retry_attempts):
                try:
                    date_str = edition.date.strftime("%Y%m%d")

                    # Erst Session etablieren durch Aufruf der Issue-Seite
                    issue_url = f"{self.BASE_URL}/issue.act?issueId={edition.issue_id}&issueMutation={edition.region_code}&issueDate={date_str}&region={edition.region_code}"
                    async with self.session.get(issue_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 200:
                            raise Exception(f"Session-Setup fehlgeschlagen: HTTP {resp.status}")

                    # Jetzt PDF laden (mit Session-Cookie)
                    pdf_url = f"{self.BASE_URL}/issue.act?issueId={edition.issue_id}&issueMutation={edition.region_code}&issueDate={date_str}&pdf=PDF"

                    self.logger.debug(f"Download: {pdf_url}")

                    timeout = aiohttp.ClientTimeout(total=self.config.timeout)
                    async with self.session.get(pdf_url, timeout=timeout, allow_redirects=True) as resp:
                        if resp.status != 200:
                            raise Exception(f"HTTP {resp.status}")

                        # Prüfe Content-Type
                        content_type = resp.headers.get('Content-Type', '')

                        # PDF direkt oder als Stream
                        if 'pdf' in content_type.lower() or 'octet-stream' in content_type.lower():
                            async with aiofiles.open(output_path, 'wb') as f:
                                async for chunk in resp.content.iter_chunked(8192):
                                    await f.write(chunk)
                        else:
                            # Könnte HTML sein mit Redirect oder Fehler
                            content = await resp.read()
                            if b'%PDF' in content[:10]:
                                # Ist doch ein PDF
                                async with aiofiles.open(output_path, 'wb') as f:
                                    await f.write(content)
                            elif len(content) < 10000 or b'<!DOCTYPE' in content[:100]:
                                raise Exception("Keine PDF erhalten - HTML-Seite")
                            else:
                                async with aiofiles.open(output_path, 'wb') as f:
                                    await f.write(content)

                    # Größe prüfen
                    file_size = output_path.stat().st_size
                    if file_size < 10000:
                        output_path.unlink(missing_ok=True)
                        raise Exception("PDF zu klein - wahrscheinlich Fehlerseite")

                    self.logger.info(f"[green]✓[/green] {edition.filename} ({file_size / 1024 / 1024:.1f} MB)")
                    self.stats["downloaded"] += 1
                    progress.advance(task_id)
                    return True

                except Exception as e:
                    self.logger.warning(f"Versuch {attempt + 1}/{self.config.retry_attempts} fehlgeschlagen für {edition.filename}: {e}")
                    if attempt < self.config.retry_attempts - 1:
                        await asyncio.sleep(self.config.retry_delay)

            self.logger.error(f"[red]✗[/red] {edition.filename} fehlgeschlagen")
            self.stats["failed"] += 1
            progress.advance(task_id)
            return False

    async def run(self):
        """Hauptmethode."""
        self.console.print("\n[bold blue]Mainpost ePaper Downloader[/bold blue]")
        if self.config.archive_mode:
            self.console.print("[dim]Archiv-Modus: Komplettes Archiv seit 2017[/dim]\n")
        else:
            self.console.print("[dim]Aktuelle Ausgaben vom Gridshelf[/dim]\n")

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
        }

        # Cookie-Jar für Session-Handling
        jar = aiohttp.CookieJar()

        async with aiohttp.ClientSession(headers=headers, cookie_jar=jar) as session:
            self.session = session

            # Ausgaben holen (Archiv oder Gridshelf)
            if self.config.archive_mode:
                editions = await self.get_archive_editions()
            else:
                editions = await self.get_editions()

            if not editions:
                self.console.print("[yellow]Keine Ausgaben gefunden.[/yellow]")
                return

            # Übersicht (nur bei wenigen Ausgaben die Tabelle zeigen)
            if len(editions) <= 50:
                table = Table(title=f"Verfügbare Ausgaben ({len(editions)})")
                table.add_column("Region", style="cyan")
                table.add_column("Datum", style="green")
                table.add_column("Issue-ID", style="dim")
                table.add_column("Status", style="yellow")

                for ed in editions:
                    output_path = Path(self.config.output_dir) / ed.name / ed.filename
                    status = "✓ Existiert" if output_path.exists() else "⬇ Download"
                    table.add_row(ed.name, ed.date.strftime("%Y-%m-%d"), ed.issue_id, status)

                self.console.print(table)
                self.console.print()
            else:
                # Bei vielen Ausgaben nur Zusammenfassung
                existing = sum(1 for ed in editions if (Path(self.config.output_dir) / ed.name / ed.filename).exists())
                to_download = len(editions) - existing
                self.console.print(f"[bold]{len(editions)} Ausgaben gefunden[/bold]")
                self.console.print(f"  [yellow]Bereits vorhanden: {existing}[/yellow]")
                self.console.print(f"  [cyan]Noch zu laden: {to_download}[/cyan]\n")

            self.stats["total"] = len(editions)

            # Downloads
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TimeElapsedColumn(),
                console=self.console
            ) as progress:
                task_id = progress.add_task("Downloading...", total=len(editions))

                tasks = [self.download_pdf(ed, progress, task_id) for ed in editions]
                await asyncio.gather(*tasks)

            # Statistik
            self.console.print("\n[bold]Ergebnis:[/bold]")
            self.console.print(f"  Gesamt:        {self.stats['total']}")
            self.console.print(f"  [green]Heruntergeladen: {self.stats['downloaded']}[/green]")
            self.console.print(f"  [yellow]Übersprungen:    {self.stats['skipped']}[/yellow]")
            self.console.print(f"  [red]Fehlgeschlagen:  {self.stats['failed']}[/red]")


async def main():
    """Einstiegspunkt."""
    import argparse

    parser = argparse.ArgumentParser(description="Mainpost ePaper Downloader")
    parser.add_argument("-c", "--config", default="config.yaml", help="Konfigurationsdatei")
    parser.add_argument("-o", "--output", help="Ausgabeverzeichnis")
    parser.add_argument("--archive", action="store_true", help="Archiv-Modus: Lädt alle Ausgaben seit 2017")
    parser.add_argument("--from", dest="date_from", help="Startdatum (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", help="Enddatum (YYYY-MM-DD)")
    parser.add_argument("--debug", action="store_true", help="Debug-Modus")
    args = parser.parse_args()

    config_path = Path(args.config)
    if config_path.exists():
        config = Config.from_yaml(str(config_path))
    else:
        config = Config()

    if args.output:
        config.output_dir = args.output
    if args.debug:
        config.log_level = "DEBUG"
    if args.archive:
        config.archive_mode = True
    if args.date_from:
        config.date_from = datetime.strptime(args.date_from, "%Y-%m-%d")
    if args.date_to:
        config.date_to = datetime.strptime(args.date_to, "%Y-%m-%d")

    downloader = MainpostDownloader(config)
    await downloader.run()


if __name__ == "__main__":
    asyncio.run(main())
