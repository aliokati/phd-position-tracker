"""Build a static, public-safe GitHub Pages dashboard from positions.json."""

from __future__ import annotations

import html
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "positions.json"
OUT = ROOT / "docs" / "index.html"


def main() -> int:
    state = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {"positions": []}
    positions = state.get("positions", [])
    rows = []
    for p in positions:
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(p.get('institution', '')))}</td>"
            f"<td><a href=\"{html.escape(str(p.get('source_url', '')), quote=True)}\" target=\"_blank\" rel=\"noreferrer\">{html.escape(str(p.get('project', '')))}</a></td>"
            f"<td>{html.escape(str(p.get('country', '')))}</td>"
            f"<td>{html.escape(str(p.get('funding', '')))}</td>"
            f"<td>{html.escape(str(p.get('status', '')))}</td>"
            f"<td>{html.escape(str(p.get('discovered_at', '')))}</td>"
            "</tr>"
        )
    updated = html.escape(str(state.get("updated_at") or "not yet run"))
    document = f"""<!doctype html>
<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">
<title>Funded PhD position tracker</title>
<style>body{{font:16px system-ui,sans-serif;max-width:1300px;margin:2rem auto;padding:0 1rem;color:#17202a;background:#f7f9fb}}h1{{color:#163b5c}}.meta{{color:#52606d;margin-bottom:1rem}}input{{width:100%;padding:.7rem;border:1px solid #ccd6e0;border-radius:6px;margin:.5rem 0 1rem}}table{{border-collapse:collapse;width:100%;background:white}}th,td{{padding:.65rem;border:1px solid #d9e2ec;text-align:left;vertical-align:top}}th{{background:#163b5c;color:white;position:sticky;top:0}}a{{color:#0b63ce}}.note{{font-size:.9rem;color:#52606d}}</style>
</head><body><h1>Funded PhD position tracker</h1>
<div class=\"meta\">Public discovery view · last scan: {updated} · {len(positions)} positions</div>
<div class=\"note\">Only public position metadata is shown. Verify funding, deadline, eligibility, and fit on the official page before applying.</div>
<input id=\"q\" placeholder=\"Filter institution, project, country, or funding…\" oninput=\"filterRows()\">
<table><thead><tr><th>Institution</th><th>Project</th><th>Country</th><th>Funding route</th><th>Status</th><th>Discovered</th></tr></thead>
<tbody id=\"rows\">{''.join(rows)}</tbody></table>
<script>function filterRows(){{const q=document.getElementById('q').value.toLowerCase();document.querySelectorAll('#rows tr').forEach(r=>r.style.display=r.innerText.toLowerCase().includes(q)?'':'none')}}</script>
</body></html>\n"""
    OUT.write_text(document, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
