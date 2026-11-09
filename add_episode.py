#!/usr/bin/env python3
"""Add a new KI-Wochenpodcast episode to the GitHub Pages feed.

Usage:
  python3 add_episode.py --mp3 PATH --date YYYY-MM-DD --description "..." [--dry-run]

Copies the MP3 into episodes/, inserts a new <item> at the top of feed.xml,
updates lastBuildDate, then commits and pushes (unless --dry-run).
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent
EPISODES_DIR = REPO_ROOT / "episodes"
FEED_PATH = REPO_ROOT / "feed.xml"
BASE_URL = "https://kai-hilker.github.io/ki-podcast"
BERLIN = ZoneInfo("Europe/Berlin")

# Register namespaces so ElementTree keeps prefixes on write
ET.register_namespace("itunes", "http://www.itunes.com/dtds/podcast-1.0.dtd")
ET.register_namespace("content", "http://purl.org/rss/1.0/modules/content/")
ET.register_namespace("atom", "http://www.w3.org/2005/Atom")

ITUNES = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"

MONTHS_DE = {
    1: "Januar", 2: "Februar", 3: "März", 4: "April", 5: "Mai", 6: "Juni",
    7: "Juli", 8: "August", 9: "September", 10: "Oktober", 11: "November", 12: "Dezember",
}


def german_date(dt: datetime) -> str:
    return f"{dt.day}. {MONTHS_DE[dt.month]} {dt.year}"


def rfc2822(dt: datetime) -> str:
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")


def format_duration(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def ffprobe_duration(path: Path) -> float:
    out = subprocess.check_output(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
    ).strip()
    return float(out)


def episode_filename(date: datetime) -> str:
    return f"KI-Wochenpodcast_{date.strftime('%Y-%m-%d')}.mp3"


def build_item(date: datetime, filename: str, length: int, duration: str, description: str) -> ET.Element:
    title = f"KI-Wochenpodcast – {german_date(date)}"
    guid = f"ki-wochenpodcast-{date.strftime('%Y-%m-%d')}"
    url = f"{BASE_URL}/episodes/{filename}"
    cover = f"{BASE_URL}/cover.jpg"
    pub = rfc2822(date.replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=BERLIN))

    item = ET.Element("item")
    ET.SubElement(item, "title").text = title
    ET.SubElement(item, "description").text = description
    ce = ET.SubElement(item, f"{CONTENT}encoded")
    ce.text = f"<![CDATA[<p>{description}</p>]]>"  # will be fixed on serialize
    # ElementTree escapes CDATA; we rewrite feed textually after build for CDATA.
    # Store plain description; serializer below wraps content:encoded properly.
    ce.text = f"<p>{description}</p>"
    ET.SubElement(item, "pubDate").text = pub
    g = ET.SubElement(item, "guid")
    g.set("isPermaLink", "false")
    g.text = guid
    enc = ET.SubElement(item, "enclosure")
    enc.set("url", url)
    enc.set("length", str(length))
    enc.set("type", "audio/mpeg")
    ET.SubElement(item, f"{ITUNES}title").text = title
    ET.SubElement(item, f"{ITUNES}author").text = "Kai Hilker"
    ET.SubElement(item, f"{ITUNES}duration").text = duration
    ET.SubElement(item, f"{ITUNES}explicit").text = "false"
    ET.SubElement(item, f"{ITUNES}summary").text = description
    img = ET.SubElement(item, f"{ITUNES}image")
    img.set("href", cover)
    return item


def insert_item_into_feed(description: str, date: datetime, filename: str, length: int, duration: str) -> str:
    """Insert new item after channel metadata using regex-friendly text edit to preserve formatting."""
    text = FEED_PATH.read_text(encoding="utf-8")
    pub = rfc2822(date.replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=BERLIN))
    title = f"KI-Wochenpodcast – {german_date(date)}"
    guid = f"ki-wochenpodcast-{date.strftime('%Y-%m-%d')}"
    url = f"{BASE_URL}/episodes/{filename}"
    cover = f"{BASE_URL}/cover.jpg"
    # Escape description for XML text (minimal)
    desc_xml = (
        description.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    desc_cdata = description.replace("]]>", "]]]]><![CDATA[>")

    item_xml = f"""    <item>
      <title>{title}</title>
      <description>{desc_xml}</description>
      <content:encoded><![CDATA[<p>{desc_cdata}</p>]]></content:encoded>
      <pubDate>{pub}</pubDate>
      <guid isPermaLink="false">{guid}</guid>
      <enclosure url="{url}" length="{length}" type="audio/mpeg"/>
      <itunes:title>{title}</itunes:title>
      <itunes:author>Kai Hilker</itunes:author>
      <itunes:duration>{duration}</itunes:duration>
      <itunes:explicit>false</itunes:explicit>
      <itunes:summary>{desc_xml}</itunes:summary>
      <itunes:image href="{cover}"/>
    </item>
"""

    # Update lastBuildDate
    text, n = re.subn(
        r"<lastBuildDate>[^<]*</lastBuildDate>",
        f"<lastBuildDate>{pub}</lastBuildDate>",
        text,
        count=1,
    )
    if n != 1:
        raise RuntimeError("Could not update lastBuildDate in feed.xml")

    # Insert before first <item>
    m = re.search(r"\n    <item>", text)
    if not m:
        # empty channel – insert before </channel>
        m2 = re.search(r"\n  </channel>", text)
        if not m2:
            raise RuntimeError("Malformed feed.xml: no </channel>")
        text = text[: m2.start()] + "\n" + item_xml + text[m2.start() :]
    else:
        text = text[: m.start()] + "\n" + item_xml + text[m.start() :]

    return text


def git(*args: str) -> None:
    subprocess.check_call(["git", *args], cwd=REPO_ROOT)


def main() -> int:
    p = argparse.ArgumentParser(description="Publish a new KI-Wochenpodcast episode")
    p.add_argument("--mp3", required=True, help="Path to the episode MP3")
    p.add_argument("--date", required=True, help="Episode date YYYY-MM-DD")
    p.add_argument("--description", required=True, help="Short German episode description")
    p.add_argument("--dry-run", action="store_true", help="Do everything except write/commit/push")
    args = p.parse_args()

    try:
        date = datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        print("error: --date must be YYYY-MM-DD", file=sys.stderr)
        return 2

    src = Path(args.mp3).expanduser().resolve()
    if not src.is_file():
        print(f"error: MP3 not found: {src}", file=sys.stderr)
        return 2

    filename = episode_filename(date)
    dest = EPISODES_DIR / filename
    length = src.stat().st_size
    duration = format_duration(ffprobe_duration(src))
    new_feed = insert_item_into_feed(args.description, date, filename, length, duration)

    print(f"episode file : {filename}")
    print(f"destination  : {dest}")
    print(f"byte length  : {length}")
    print(f"duration     : {duration}")
    print(f"title        : KI-Wochenpodcast – {german_date(date)}")
    print(f"pubDate      : {rfc2822(date.replace(hour=8, minute=0, second=0, microsecond=0, tzinfo=BERLIN))}")

    if args.dry_run:
        print("--dry-run: not writing files, not committing")
        # Show first item snippet
        snippet = new_feed[new_feed.find("<item>") : new_feed.find("</item>") + 7]
        print("--- proposed item ---")
        print(snippet[:800])
        print("---")
        return 0

    EPISODES_DIR.mkdir(parents=True, exist_ok=True)
    if dest.resolve() != src:
        shutil.copy2(src, dest)
        print(f"copied {src.name} -> episodes/{filename}")
    else:
        print("MP3 already in episodes/")

    FEED_PATH.write_text(new_feed, encoding="utf-8")
    print("updated feed.xml")

    git("add", str(dest.relative_to(REPO_ROOT)), "feed.xml")
    msg = f"Add episode {args.date}"
    git("commit", "-m", msg)
    git("push", "origin", "HEAD")
    print("committed and pushed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
