"""Public, privacy-safe PhD discovery state for GitHub Actions and Pages.

This intentionally stores only position metadata and source links. It never stores
the CV, email content, contact details, credentials, or application documents.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phd_gatherer import allowed, fetch_rss, infer_institution, interesting, read_sources  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "positions.json"
LOG = ROOT / "automation" / "cloud_gatherer.log"


def load_state() -> dict:
    if not DATA.exists():
        return {"updated_at": None, "positions": []}
    return json.loads(DATA.read_text(encoding="utf-8"))


def key(value: str) -> str:
    return re.sub(r"\W+", " ", value.lower()).strip()


def main() -> int:
    logging.basicConfig(filename=LOG, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    state = load_state()
    positions = state.get("positions", [])
    seen_urls = {str(p.get("source_url", "")).lower().rstrip("/") for p in positions}
    seen_titles = {key(str(p.get("project", ""))) for p in positions}
    added = 0
    for source in read_sources():
        try:
            items = fetch_rss(source["query"])
        except Exception as exc:
            logging.warning("Source failed: %s (%s)", source["query"], exc)
            continue
        for item in items:
            if not interesting(item) or not allowed(item, source):
                continue
            url = item["link"].strip()
            title = item["title"].strip()
            if url.lower().rstrip("/") in seen_urls or key(title) in seen_titles:
                continue
            positions.append({
                "institution": infer_institution(title, source),
                "project": title,
                "country": source.get("country", "International"),
                "funding": source.get("funding_route", "Funding to verify"),
                "status": "Discovered – verify",
                "deadline": None,
                "fit": "To assess",
                "source_url": url,
                "source": source["name"],
                "discovered_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            })
            seen_urls.add(url.lower().rstrip("/"))
            seen_titles.add(key(title))
            added += 1
    state["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state["positions"] = positions
    DATA.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logging.info("Cloud run complete: %d added, %d total", added, len(positions))
    print(f"Added {added} positions; total {len(positions)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
