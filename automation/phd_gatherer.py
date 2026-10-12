"""Discover funded PhD opportunities and append unverified candidates to the tracker.

This process deliberately stops at discovery. It never submits applications or sends
messages. New rows are marked "Discovered – verify" so eligibility, funding, deadline,
and fit are reviewed before any application action.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "outputs" / "phd_application_tracker.xlsx"
SOURCES = Path(__file__).with_name("sources.json")
LOG = Path(__file__).with_name("phd_gatherer.log")

NS = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ET.register_namespace("", NS["x"])


def setup_logging() -> None:
    logging.basicConfig(
        filename=LOG,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def fetch_rss(query: str) -> list[dict[str, str]]:
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote_plus(query)
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "PhDTrackerGatherer/1.0 (+local scheduled task)"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = response.read()
    root = ET.fromstring(payload)
    results: list[dict[str, str]] = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = re.sub(r"\s+", " ", (item.findtext("description") or "").strip())
        pub_date = (item.findtext("pubDate") or "").strip()
        if title and link:
            results.append(
                {"title": title, "link": link, "description": description, "pub_date": pub_date}
            )
    return results


def read_sources() -> list[dict]:
    return json.loads(SOURCES.read_text(encoding="utf-8"))


def interesting(item: dict[str, str]) -> bool:
    text = f"{item['title']} {item['description']}".lower()
    return bool(re.search(r"\b(phd|dphil|doctoral|studentship|scholarship|graduate research)\b", text))


def allowed(item: dict[str, str], source: dict) -> bool:
    domains = source.get("allowed_domains", [])
    host = urllib.parse.urlparse(item["link"]).netloc.lower()
    return not domains or any(domain.lower() in host for domain in domains)


def infer_institution(title: str, source: dict) -> str:
    known = [
        "Oxford", "Monash", "Helmholtz", "Cambridge", "Imperial", "UCL",
        "Auckland", "Adelaide", "Warwick", "Nantes", "Melbourne", "Toronto",
        "McGill", "Leiden", "Karolinska", "ETH", "TUM", "LMU",
    ]
    for name in known:
        if name.lower() in title.lower():
            return name
    return source.get("institution_fallback", "Unknown – verify")


def collect_candidates() -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    for source in read_sources():
        try:
            items = fetch_rss(source["query"])
        except Exception as exc:  # the next scheduled run can retry a failed source
            logging.warning("Source failed: %s (%s)", source["query"], exc)
            continue
        for item in items:
            if interesting(item) and allowed(item, source):
                candidates.append(
                    {
                        "institution": infer_institution(item["title"], source),
                        "project": item["title"],
                        "country": source.get("country", "International"),
                        "funding": source.get("funding_route", "Funding to verify"),
                        "url": item["link"],
                        "description": item["description"],
                        "pub_date": item["pub_date"],
                        "source": source["name"],
                    }
                )
    # Keep one result per URL, then one per normalized title.
    unique: list[dict[str, str]] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate["url"].lower().rstrip("/")
        title_key = re.sub(r"\W+", " ", candidate["project"].lower()).strip()
        dedupe_key = key or title_key
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            unique.append(candidate)
    return unique


def text_of(cell: ET.Element | None, shared: list[str]) -> str:
    if cell is None:
        return ""
    kind = cell.attrib.get("t", "")
    if kind == "inlineStr":
        return "".join(cell.itertext()).strip()
    value = cell.findtext("x:v", namespaces=NS) or ""
    if kind == "s" and value.isdigit() and int(value) < len(shared):
        return shared[int(value)]
    return value.strip()


def shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.itertext()).strip() for node in root.findall("x:si", NS)]


def cell_map(row: ET.Element, shared: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for cell in row.findall("x:c", NS):
        ref = cell.attrib.get("r", "")
        col = re.match(r"[A-Z]+", ref)
        if col:
            result[col.group(0)] = text_of(cell, shared)
    return result


def make_cell(ref: str, value: str, style: str = "28") -> ET.Element:
    cell = ET.Element(f"{{{NS['x']}}}c", {"r": ref, "s": style, "t": "str"})
    value_node = ET.SubElement(cell, f"{{{NS['x']}}}v")
    value_node.text = value
    return cell


def update_tracker(candidates: list[dict[str, str]]) -> int:
    if not candidates or not TRACKER.exists():
        return 0
    with zipfile.ZipFile(TRACKER, "r") as source_zip:
        sheet_root = ET.fromstring(source_zip.read("xl/worksheets/sheet1.xml"))
        table_root = ET.fromstring(source_zip.read("xl/tables/table1.xml"))
        shared = shared_strings(source_zip)
        sheet_rows = sheet_root.find("x:sheetData", NS)
        if sheet_rows is None:
            return 0
        rows = sheet_rows.findall("x:row", NS)
        header_row = next((row for row in rows if row.attrib.get("r") == "8"), None)
        if header_row is None:
            return 0
        headers = cell_map(header_row, shared)
        existing_rows = [cell_map(row, shared) for row in rows if int(row.attrib.get("r", "0")) >= 9]
        existing_urls = {row.get(headers.get("K", "K"), "").lower().rstrip("/") for row in existing_rows}
        existing_titles = {
            re.sub(r"\W+", " ", row.get(headers.get("C", "C"), "").lower()).strip()
            for row in existing_rows
        }
        max_id = 0
        for row in existing_rows:
            try:
                max_id = max(max_id, int(row.get(headers.get("A", "A"), "0")))
            except ValueError:
                pass
        last_row = max(int(row.attrib.get("r", "0")) for row in rows)
        additions = []
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for candidate in candidates:
            url_key = candidate["url"].lower().rstrip("/")
            title_key = re.sub(r"\W+", " ", candidate["project"].lower()).strip()
            if url_key in existing_urls or title_key in existing_titles:
                continue
            max_id += 1
            last_row += 1
            notes = (
                f"Automated discovery from {candidate['source']} on {now}. "
                "Unverified: confirm official page, funding, deadline, eligibility, and fit before applying."
            )
            values = {
                "A": str(max_id),
                "B": candidate["institution"],
                "C": candidate["project"],
                "D": candidate["country"],
                "E": candidate["funding"],
                "F": "Discovered – verify",
                "G": "",
                "H": "To assess",
                "I": "Not started",
                "J": "Verify official page, funding, deadline, Iranian-citizen eligibility, and fit.",
                "K": candidate["url"],
                "L": notes,
            }
            row = ET.Element(f"{{{NS['x']}}}row", {"r": str(last_row), "ht": "55", "customHeight": "1"})
            for column, value in values.items():
                style = "37" if column == "A" else "40" if column == "G" else "44" if column in {"H", "I"} else "31"
                if column in {"J", "K"}:
                    style = "31"
                if column == "L":
                    style = "32"
                row.append(make_cell(f"{column}{last_row}", value, style))
            sheet_rows.append(row)
            existing_urls.add(url_key)
            existing_titles.add(title_key)
            additions.append(values)
        if not additions:
            return 0
        new_last = last_row
        dimension = sheet_root.find("x:dimension", NS)
        if dimension is not None:
            dimension.set("ref", f"A1:L{new_last}")
        table_root.attrib["ref"] = f"A8:L{new_last}"
        for cf in sheet_root.findall("x:conditionalFormatting", NS):
            sqref = cf.attrib.get("sqref", "")
            if sqref.startswith("F9:F"):
                cf.attrib["sqref"] = f"F9:F{new_last}"
            elif sqref.startswith("I9:I"):
                cf.attrib["sqref"] = f"I9:I{new_last}"
        for validation in sheet_root.findall("x:dataValidations/x:dataValidation", NS):
            if validation.attrib.get("sqref", "").startswith("I9:I"):
                validation.attrib["sqref"] = f"I9:I{new_last}"
        xml_sheet = ET.tostring(sheet_root, encoding="utf-8", xml_declaration=True)
        xml_table = ET.tostring(table_root, encoding="utf-8", xml_declaration=True)
        with tempfile.NamedTemporaryFile(prefix="phd_tracker_", suffix=".xlsx", delete=False, dir=TRACKER.parent) as tmp:
            temp_path = Path(tmp.name)
        try:
            with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as output_zip:
                for info in source_zip.infolist():
                    if info.filename == "xl/worksheets/sheet1.xml":
                        output_zip.writestr(info, xml_sheet)
                    elif info.filename == "xl/tables/table1.xml":
                        output_zip.writestr(info, xml_table)
                    else:
                        output_zip.writestr(info, source_zip.read(info.filename))
            # Windows does not allow replacing an open workbook, so close the
            # source archive before taking the backup and swapping in the temp file.
            source_zip.close()
            backup = TRACKER.with_suffix(".xlsx.bak")
            shutil.copy2(TRACKER, backup)
            os.replace(temp_path, TRACKER)
        finally:
            if temp_path.exists():
                temp_path.unlink()
        logging.info("Added %d candidates to %s", len(additions), TRACKER)
        return len(additions)


def main() -> int:
    setup_logging()
    try:
        candidates = collect_candidates()
        added = update_tracker(candidates)
        logging.info("Run complete: %d discovered, %d added", len(candidates), added)
        return 0
    except Exception:
        logging.exception("Run failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
