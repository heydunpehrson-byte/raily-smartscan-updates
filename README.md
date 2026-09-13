# RAILY SmartScan updates

Current stable release: **RAILY v72.1 — Fast Date Reader + Teach UI Fix** for Windows 10/11.

- [Download RAILY v72.1](releases/RAILY_SmartScan_v72_1.py)
- [Official stable update manifest](https://raw.githubusercontent.com/heydunpehrson-byte/raily-smartscan-updates/main/update_manifest.json)

## What's new in v72.1

- **Fast date reading:** the exact taught area is read upright first, using
  digits/date-specific OCR. Validated evidence stops the read immediately. The
  normal sorting path is limited to three OCR calls per taught area; the full
  Teach reader has a hard limit of 24 calls, with a five-second timeout per call.
- **Ordered fallbacks:** crop adjustments and multi-line upright reading precede
  90°, 270° and 180° rotations. Handwriting, blue-ink isolation and heavier cleanup
  are last-resort fallbacks. Prepared images are cached during a read, date areas
  on the same page share a render, and the Date Trainer reuses its preview image.
- **Clear progress:** Fast date check, Trying taught date area, Rotation fallback
  and Handwriting fallback explain the current step. Internal logs record OCR
  call counts and elapsed time. Teach date OCR remains on background workers.
- **Accessible teaching forms:** Conductor Review, Date Trainer and split teaching
  fit the usable Windows work area, accounting for the taskbar and window borders.
  They use visible vertical scrollbars, wheel scrolling over form controls, compact
  layouts on short displays, wrapped text and previews sized to their canvas.
  Important actions remain in fixed bottom bars and wrap onto another row when
  needed. Learning, date, memory and duplicate managers have scrollable tables.

Date parsing, allowed year ranges and confidence checks remain in place. A required
date that cannot be confidently read still goes to Teach/Review; RAILY does not
invent a replacement date. Manual dates and learned date areas are retained.

## Update channel

RAILY v72.0 and later use the official GitHub manifest by default on new
installations. Existing blank manifest URLs are populated through the existing
configuration save path after the release-backup step. Custom manifest URLs and
existing update mode, channel and idle preferences are preserved.

In RAILY Setup, use **Check for Updates Now**. For v71.2 installations, enter the
official manifest URL above to receive the current stable release through the
built-in updater.

The unchanged updater verifies SHA-256 and Python syntax, creates a pre-update
archive of the application and state files, stages the replacement, and checks
the startup health marker after restarting. Its Windows helper restores the
previous application if that marker is missing.

## Preservation and validation

Learned document types and date areas, manual dates, Railroad → Location filing,
person-name filename metadata, duplicate detection/review, multi-document splitting,
full-page visual learning, Conductor Review, Windows startup/tray integration,
configuration files, backups and rollback are preserved. The v72.0 and v71.2
release files remain unchanged.

The complete v72.1 Python file passes syntax validation. Real Tesseract tests on
printed samples of `09/12/2026`, `9/12/26`, `09-12-2026` and `2026-09-12` each finish
in two OCR calls. Rotated samples, invalid/ambiguous dates, attempt limits, image
caching and confidence rejection were also checked. Isolated Tk tests with RAILY's
styles cover 1366×728, 1024×700 and 1920×1040 usable work areas, including bottom
access, wheel scrolling and fixed action buttons after resizing. These are
controlled tests, not a guarantee for every scanned or handwritten document.
