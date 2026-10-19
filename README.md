# PDF Studio

**A free, offline-first PDF toolkit in your browser.** Merge, split, reorder, rotate,
compress and flatten PDFs with a visual page editor — and every byte stays on your
machine.

```
python app.py        # that's the whole idea
→ http://127.0.0.1:8380
```

## Why PDF Studio

- **100% offline.** No accounts, no cloud, no telemetry, no analytics. The server
  never makes an outbound connection — you can run it with networking disabled.
- **Private by design.** Documents are processed locally and never uploaded anywhere.
  No access logs are written. The file browser is jailed to your home directory.
- **Free & open source.** MIT-licensed, built entirely on free software.
- **Easy.** No build step, no database, no configuration. One Python file, one HTML
  file.

## Features

- **Page editor** — thumbnail grid or full-page reader view; click / shift-click /
  ctrl-click to select pages; drag to reorder; delete; rotate.
- **Multi-document** — open many PDFs as tabs (multi-select in the file browser),
  reorder tabs by drag & drop, copy pages between documents.
- **Merge all** — one click merges every open document in tab order, respecting your
  edits.
- **Compress** — Ghostscript presets (screen / ebook / printer) with before → after
  sizes. Originals are never modified; results open as a new tab.
- **Flatten** — bake annotations and form fields into page content (optional, uses
  PyMuPDF if installed).
- **Save anywhere** — write results directly to any folder under your home directory;
  last-used folder and timestamped filenames are remembered.

## Install

You need [Python](https://www.python.org/downloads/) 3.10+ and **Ghostscript**
(free, for the Compress feature). Everything else is `pip install`-ed, and page
thumbnails are rendered automatically — by Poppler if you have it, otherwise by
the bundled PyMuPDF (so on Windows you can skip Poppler entirely).

```bash
# 1. get Ghostscript (the only external tool)
Debian/Ubuntu:  sudo apt install ghostscript
macOS:          brew install ghostscript
Windows:        winget install Artifex.Ghostscript   (if winget can't find it,
                download the gs…w64.exe installer from
                https://github.com/ArtifexSoftware/ghostpdl-downloads/releases)

# optional, faster page rendering — only if you want it:
Debian/Ubuntu:  sudo apt install poppler-utils
macOS:          brew install poppler
Windows:        https://github.com/oschwartz10612/poppler-windows/releases

# 2. get PDF Studio
git clone https://github.com/flux72/pdfstudio.git
cd pdfstudio

# 3. install Python dependencies
python -m venv venv
venv/bin/pip install -r requirements.txt      # Windows: venv\Scripts\pip install -r requirements.txt

# 4. run
venv/bin/python app.py                        # Windows: venv\Scripts\python app.py
```

Open **http://127.0.0.1:8380** and you're done. If a tool is missing, the app tells
you exactly what and how to get it; you can also point it at specific binaries with
the `PDFSTUDIO_GS` and `PDFSTUDIO_PDFTOPPM` environment variables.

## Configuration (optional)

| Environment variable | Default | Purpose |
|---|---|---|
| `PDFSTUDIO_HOST` | `127.0.0.1` | Bind address. Keep localhost unless you deliberately want other devices on your LAN/VPN to reach it. |
| `PDFSTUDIO_PORT` | `8380` | HTTP port. |
| `PDFSTUDIO_HOME` | your home directory | Start folder of the file browser. |
| `PDFSTUDIO_ROOT` | `PDFSTUDIO_HOME` | Root of the file browser and save dialogs. Set to e.g. `D:\` if your documents live on another drive. |
| `PDFSTUDIO_GS` | auto-detect | Path to the Ghostscript binary. |
| `PDFSTUDIO_PDFTOPPM` | auto-detect | Path to the Poppler `pdftoppm` binary. Set to an empty value to force the built-in PyMuPDF renderer. |

### Run at boot (Linux, optional)

```ini
# /etc/systemd/system/pdfstudio.service
[Unit]
Description=PDFStudio
After=network-online.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/path/to/pdfstudio
ExecStart=/path/to/pdfstudio/venv/bin/python3 app.py
Restart=always
Environment=HOME=/home/youruser
Environment=PDFSTUDIO_HOST=0.0.0.0   # only if you want LAN access

[Install]
WantedBy=multi-user.target
```

## How it works

A single FastAPI process serves a one-page UI and a small JSON API:

- **pypdf** — page surgery (merge, reorder, rotate, delete, copy across files)
- **Poppler `pdftoppm`** — page thumbnails, rendered on demand and cached on disk
- **Ghostscript** — compression
- **PyMuPDF** (optional) — flatten

The frontend is a single dependency-free HTML file — no CDNs, no external fonts, no
frameworks. The whole UI works with the network disconnected.

## Privacy notes

- Documents are read from disk, processed in memory and temp files, and written only
  where you choose to save them.
- Temp files live in `cache/` (thumbnails) and `work/` (download staging) inside the
  project folder; both are disposable.
- Nothing is logged: the server runs with access logging disabled and never phones
  home.

## API (for tinkerers)

| Endpoint | Purpose |
|---|---|
| `GET /api/browse?p=` | list dirs/PDFs under the home root (path-jailed) |
| `POST /api/open` | open a PDF → docId |
| `GET /api/thumb/{doc}/{page}?r=` | PNG page render (cached) |
| `POST /api/build` | assemble pages `[{doc,page,rot}]` → new PDF |
| `POST /api/compress` | Ghostscript preset run |
| `POST /api/flatten` | bake annotations + form fields (needs PyMuPDF) |
| `POST /api/export` | write pages (or copy a doc) directly to a chosen folder |
| `GET /api/download/{doc}` | download doc |

## License

MIT — see [LICENSE](LICENSE).

PDF Studio is free software, but it *drives* other free tools rather than bundling
them. Install them separately (step 1 above) and their own licenses apply to them:

| Tool | License |
|---|---|
| Ghostscript | AGPL-3.0 |
| Poppler | GPL-2.0-or-later |
| pypdf | BSD-3-Clause |
| FastAPI / Starlette / Pydantic | MIT / BSD |
| uvicorn | BSD-3-Clause |
| PyMuPDF | AGPL-3.0 (optional feature) |
