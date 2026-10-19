"""
Download third-party JS/CSS assets into app/web/static/vendor/.

Why self-host instead of using jsdelivr/cdnjs directly:
  * Some countries (incl. Turkmenistan ISPs for a lot of mobile users)
    block or throttle large CDNs. The symptom is a bare, unstyled page —
    the HTML loads but Bootstrap CSS never arrives, so the client sees
    stacked raw links instead of a dashboard.
  * Our own server IS reachable from the client (that's where the HTML
    came from), so if we serve the vendor files from /static/vendor/
    the page always renders.

Run this script ONCE on a machine with reliable internet (e.g. your dev
box), then commit the downloaded files. Both your production server and
your friend's server will pick them up on the next `git pull`.

Usage:
    python tools/fetch_vendor.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path


_VENDOR_DIR = Path(__file__).resolve().parents[1] / "app" / "web" / "static" / "vendor"

# Pin exact versions so a fresh install always matches what the templates expect.
_ASSETS = [
    ("bootstrap.min.css",
     "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css"),
    ("bootstrap.bundle.min.js",
     "https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"),
    ("chart.umd.min.js",
     "https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"),
    ("qrcode.min.js",
     "https://cdn.jsdelivr.net/npm/qrcodejs@1.0.0/qrcode.min.js"),
]


def main() -> int:
    _VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    total_bytes = 0

    for name, url in _ASSETS:
        dst = _VENDOR_DIR / name
        print(f"→ {name}  <-  {url}")
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read()
            if len(data) < 1024:
                print(f"  ! {name}: only {len(data)} bytes — looks truncated/error, skipping")
                failed.append(name)
                continue
            dst.write_bytes(data)
            total_bytes += len(data)
            print(f"  ✓ {len(data):,} bytes")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {type(exc).__name__}: {exc}")
            failed.append(name)

    print()
    print(f"Saved {len(_ASSETS) - len(failed)}/{len(_ASSETS)} files "
          f"({total_bytes:,} bytes) to {_VENDOR_DIR}")
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        print("Fix your network (or run from a different machine) and re-run.")
        return 1

    print()
    print("Next steps:")
    print(f"  1. git add {_VENDOR_DIR.relative_to(_VENDOR_DIR.parents[3])}/*.css "
          f"{_VENDOR_DIR.relative_to(_VENDOR_DIR.parents[3])}/*.js")
    print("  2. git commit -m 'vendor: refresh bootstrap/chart.js/qrcode'")
    print("  3. git push  — servers will pick up the files on next pull.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
