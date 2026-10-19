#!/usr/bin/env python3
"""PDFStudio — local, offline PDF toolkit (compress / merge / split / page surgery).

Fully offline: binds to localhost by default, no outbound calls, no access logs.
Backends: pypdf (page ops), poppler pdftoppm (thumbnails), Ghostscript (compress).
"""
import glob
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pypdf import PdfReader, PdfWriter

HOME = Path(os.environ.get("PDFSTUDIO_HOME") or Path.home()).resolve()
# jail root for the file browser/save dialogs: home by default, widen with PDFSTUDIO_ROOT
ROOT = Path(os.environ.get("PDFSTUDIO_ROOT") or HOME).resolve()
BASE = Path(__file__).resolve().parent
CACHE = BASE / "cache"
WORK = BASE / "work"
CACHE.mkdir(exist_ok=True)
WORK.mkdir(exist_ok=True)

PRESETS = {"screen", "ebook", "printer", "prepress"}


def find_tool(env_key: str, candidates: list) -> str:
    """Locate an external binary: env override first, then PATH.

    Lets packagers/users point at specific builds, and supports the
    Windows names (gswin64c) alongside the Unix ones (gs).
    Setting the env var to an empty string explicitly disables the tool
    (e.g. PDFSTUDIO_PDFTOPPM= forces the PyMuPDF renderer).
    """
    p = os.environ.get(env_key)
    if p is not None:
        return p if p and Path(p).exists() else ""
    for c in candidates:
        w = shutil.which(c)
        if w:
            return w
    return ""


GS = find_tool("PDFSTUDIO_GS", ["gs", "gswin64c", "gswin32c"])
PDFTOPPM = find_tool("PDFSTUDIO_PDFTOPPM", ["pdftoppm"])  # optional — PyMuPDF is the fallback renderer
if not GS:
    raise SystemExit(
        "PDFStudio needs Ghostscript installed and on PATH.\n"
        "  Debian/Ubuntu:  sudo apt install ghostscript"
        + "\n  macOS:          brew install ghostscript"
        + "\n  Windows:        winget install Artifex.Ghostscript "
        "(or download the gs…w64.exe installer from"
        " https://github.com/ArtifexSoftware/ghostpdl-downloads/releases)\n"
        "Or point PDFSTUDIO_GS at the binary."
    )

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# docId -> {"path": Path, "pages": int, "name": str}
DOCS = {}


def safe_home_path(raw: str) -> Path:
    if not raw:
        raise HTTPException(400, "No folder given")
    p = Path(raw).expanduser().resolve()
    root = str(ROOT)
    ok = str(p) == root or str(p).startswith(root + os.sep)
    if not ok and os.name == "nt":  # Windows: case-insensitive paths
        pn, rn = os.path.normcase(str(p)), os.path.normcase(root + os.sep)
        ok = pn == os.path.normcase(root) or pn.startswith(rn)
    if not ok:
        raise HTTPException(
            400, f"Path is outside the allowed root ({root}) — set PDFSTUDIO_ROOT to widen it")
    return p

def get_doc(doc_id: str):
    d = DOCS.get(doc_id)
    if not d or not d["path"].exists():
        raise HTTPException(404, "Unknown document")
    return d


def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {u}" if u != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


# ---------- file browser ----------

@app.get("/api/browse")
def browse(p: str = ""):
    cur = safe_home_path(p) if p else HOME
    if not cur.is_dir():
        raise HTTPException(400, "Not a directory")
    dirs, pdfs = [], []
    try:
        for e in sorted(cur.iterdir(), key=lambda x: x.name.lower()):
            if e.name.startswith("."):
                continue
            if e.is_dir():
                dirs.append(e.name)
            elif e.is_file() and e.suffix.lower() == ".pdf":
                pdfs.append({"name": e.name, "size": human(e.stat().st_size)})
    except PermissionError:
        pass
    parent = str(cur.parent) if cur != HOME else ""
    return {"path": str(cur), "parent": parent, "dirs": dirs, "pdfs": pdfs, "home": str(HOME)}


# ---------- open / thumbnails ----------

class OpenReq(BaseModel):
    path: str


@app.post("/api/open")
def open_pdf(req: OpenReq):
    p = safe_home_path(req.path)
    if not p.is_file() or p.suffix.lower() != ".pdf":
        raise HTTPException(400, "Not a PDF file")
    try:
        reader = PdfReader(str(p))
        n = len(reader.pages)
        if reader.is_encrypted:
            raise HTTPException(400, "Encrypted PDFs are not supported")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Cannot read PDF: {e}")
    doc_id = uuid.uuid4().hex[:10]
    DOCS[doc_id] = {"path": p, "pages": n, "name": p.name}
    return {"docId": doc_id, "name": p.name, "pages": n, "size": human(p.stat().st_size),
            "path": str(p)}


@app.get("/api/thumb/{doc_id}/{page}")
def thumb(doc_id: str, page: int, r: int = 40):
    d = get_doc(doc_id)
    if not 1 <= page <= d["pages"]:
        raise HTTPException(400, "Page out of range")
    if r not in (40, 110):
        raise HTTPException(400, "Bad resolution")
    out = CACHE / f"{doc_id}-p{page}-r{r}.png"
    if not out.exists():
        if PDFTOPPM:
            # Poppler: fast, preferred renderer (Linux/macOS installs)
            prefix = CACHE / f"tmp-{uuid.uuid4().hex[:8]}"
            subprocess.run(
                [PDFTOPPM, "-png", "-r", str(r), "-f", str(page), "-l", str(page),
                 str(d["path"]), str(prefix)],
                capture_output=True, timeout=60)
            produced = sorted(glob.glob(str(prefix) + "*.png"))
            if not produced:
                raise HTTPException(500, "Thumbnail render failed")
            shutil.move(produced[0], out)
        else:
            # PyMuPDF fallback: zero extra install (Windows)
            render_pymupdf(d["path"], page, r, out)
    return FileResponse(out, media_type="image/png",
                        headers={"Cache-Control": "max-age=86400"})


def render_pymupdf(pdf_path: Path, page: int, dpi: int, out: Path):
    try:
        import pymupdf  # lazy
    except ImportError:
        raise HTTPException(
            500, "No renderer available: install Poppler (pdftoppm) or PyMuPDF (pip install pymupdf)")
    try:
        doc = pymupdf.open(str(pdf_path))
        zoom = dpi / 72.0
        pix = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        tmp = out.with_suffix(".tmp.png")
        pix.save(str(tmp))
        doc.close()
        os.replace(str(tmp), str(out))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Thumbnail render failed: {e}")


# ---------- build (merge / reorder / delete / copy) ----------

class PageRef(BaseModel):
    doc: str
    page: int
    rot: int = 0


class BuildReq(BaseModel):
    name: str
    pages: list[PageRef]


def assemble(pages: list[PageRef]) -> PdfWriter:
    readers = {}

    def reader_for(doc_id):
        if doc_id not in readers:
            d = get_doc(doc_id)
            readers[doc_id] = (d, PdfReader(str(d["path"])))
        return readers[doc_id]

    writer = PdfWriter()
    for ref in pages:
        d, r = reader_for(ref.doc)
        if not 1 <= ref.page <= d["pages"]:
            raise HTTPException(400, f"Page {ref.page} out of range in {d['name']}")
        page = r.pages[ref.page - 1]
        if ref.rot:
            page.rotate(ref.rot)
        writer.add_page(page)
    return writer


@app.post("/api/build")
def build(req: BuildReq):
    if not req.pages:
        raise HTTPException(400, "Empty page list")
    name = re.sub(r"[^\w\-. ]", "_", req.name).strip() or "result"
    writer = assemble(req.pages)

    out = WORK / f"{name}-{time.strftime('%Y%m%d-%H%M%S')}.pdf"
    with open(out, "wb") as f:
        writer.write(f)
    doc_id = uuid.uuid4().hex[:10]
    DOCS[doc_id] = {"path": out, "pages": len(req.pages), "name": out.name}
    return {"docId": doc_id, "name": out.name, "pages": len(req.pages),
            "size": human(out.stat().st_size), "path": str(out)}


# ---------- compress ----------

class CompressReq(BaseModel):
    docId: str
    preset: str


@app.post("/api/compress")
def compress(req: CompressReq):
    if req.preset not in PRESETS:
        raise HTTPException(400, "Bad preset")
    d = get_doc(req.docId)
    before = d["path"].stat().st_size
    stem = re.sub(r"\.pdf$", "", d["name"], flags=re.I)
    out = WORK / f"{stem}-{req.preset}-{time.strftime('%Y%m%d-%H%M%S')}.pdf"
    r = subprocess.run(
        [GS, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
         "-dPDFSETTINGS=/" + req.preset, "-dNOPAUSE", "-dBATCH", "-dQUIET",
         "-sOutputFile=" + str(out), str(d["path"])],
        capture_output=True, timeout=300)
    if r.returncode != 0 or not out.exists():
        raise HTTPException(500, "Ghostscript failed")
    after = out.stat().st_size
    doc_id = uuid.uuid4().hex[:10]
    DOCS[doc_id] = {"path": out, "pages": d["pages"], "name": out.name}
    return {"docId": doc_id, "name": out.name, "pages": d["pages"],
            "size": human(after), "path": str(out),
            "before": human(before), "after": human(after),
            "pct": round((1 - after / before) * 100, 1) if before else 0}


# ---------- flatten (bake annotations + form fields into page content) ----------

class FlattenReq(BaseModel):
    docId: str


@app.post("/api/flatten")
def flatten_pdf(req: FlattenReq):
    """Convert a PDF to a flat one: comments/annotations and form fields are
    baked into the page content and the interactive objects removed, so the
    output has nothing left to edit or fill."""
    d = get_doc(req.docId)
    try:
        import pymupdf  # lazy: heavy import only when flattening
    except ImportError:
        raise HTTPException(500, "pymupdf not installed")
    try:
        doc = pymupdf.open(str(d["path"]))
    except Exception as e:
        raise HTTPException(400, f"Cannot open PDF: {e}")
    n_annots = sum(len(list(p.annots() or [])) for p in doc)
    n_widgets = sum(len(list(p.widgets() or [])) for p in doc)
    try:
        doc.bake(annots=True, widgets=True)
        stem = re.sub(r"\.pdf$", "", d["name"], flags=re.I)
        out = WORK / f"{stem}-flat-{time.strftime('%Y%m%d-%H%M%S')}.pdf"
        doc.save(str(out), garbage=3, deflate=True)
        n_pages = doc.page_count
        doc.close()
    except Exception as e:
        doc.close()
        raise HTTPException(500, f"Flatten failed: {e}")
    doc_id = uuid.uuid4().hex[:10]
    DOCS[doc_id] = {"path": out, "pages": n_pages, "name": out.name}
    return {"docId": doc_id, "name": out.name, "pages": n_pages,
            "size": human(out.stat().st_size), "path": str(out),
            "annots": n_annots, "widgets": n_widgets}


# ---------- save to folder / download ----------

class ExportReq(BaseModel):
    dir: str
    name: str
    overwrite: bool = False
    copyFrom: str | None = None
    pages: list[PageRef] | None = None


@app.post("/api/export")
def export_pdf(req: ExportReq):
    """Write the result DIRECTLY into the chosen folder — no work-folder copy."""
    target_dir = safe_home_path(req.dir)
    if not target_dir.is_dir():
        raise HTTPException(400, "Target folder does not exist")
    name = re.sub(r"[^\w\-. ()]", "_", req.name).strip()
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    target = target_dir / name
    if target.exists() and not req.overwrite:
        raise HTTPException(409, "File already exists")
    if req.copyFrom:
        d = get_doc(req.copyFrom)
        shutil.copy2(d["path"], target)
    else:
        if not req.pages:
            raise HTTPException(400, "Nothing to save")
        writer = assemble(req.pages)
        with open(target, "wb") as f:
            writer.write(f)
    return {"path": str(target), "size": human(target.stat().st_size)}


@app.get("/api/download/{doc_id}")
def download(doc_id: str):
    d = get_doc(doc_id)
    return FileResponse(d["path"], filename=d["name"],
                        headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def health():
    return JSONResponse({"ok": True})


INBOX = WORK / "inbox"


def register_pdf(p: Path):
    try:
        reader = PdfReader(str(p))
        n = len(reader.pages)
        if reader.is_encrypted:
            raise HTTPException(400, f"{p.name} is encrypted — not supported")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"{p.name} is not a readable PDF ({e})")
    doc_id = uuid.uuid4().hex[:10]
    DOCS[doc_id] = {"path": p, "pages": n, "name": p.name}
    return {"docId": doc_id, "name": p.name, "pages": n,
            "size": human(p.stat().st_size), "path": str(p)}


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    """Native file-picker flow: the browser uploads PDFs chosen in the OS dialog."""
    INBOX.mkdir(exist_ok=True)
    out, errors = [], []
    for f in files:
        if not f.filename or not f.filename.lower().endswith(".pdf"):
            continue
        safe = re.sub(r"[^\w\-. ()\u4e00-\u9fff]", "_", f.filename).strip() or "upload.pdf"
        dest = INBOX / safe
        if dest.exists():  # never overwrite
            dest = INBOX / (Path(safe).stem + "-" + uuid.uuid4().hex[:6] + ".pdf")
        with open(dest, "wb") as fh:
            fh.write(await f.read())
        try:
            out.append(register_pdf(dest))
        except HTTPException as e:
            dest.unlink(missing_ok=True)
            errors.append(e.detail)
    if not out:
        raise HTTPException(400, errors[0] if errors else "No PDF files in the selection")
    out[0]["warnings"] = errors
    return out


@app.get("/api/config")
def config():
    return JSONResponse({"home": str(HOME), "work": str(WORK), "root": str(ROOT)})


app.mount("/", StaticFiles(directory=BASE / "static", html=True), name="static")


if __name__ == "__main__":
    import threading
    import webbrowser
    import uvicorn
    host = os.environ.get("PDFSTUDIO_HOST", "127.0.0.1")
    port = int(os.environ.get("PDFSTUDIO_PORT", "8380"))
    shown_host = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{shown_host}:{port}"
    print(flush=True)
    print("  PDF Studio is running", flush=True)
    print(f"  Open    : {url}", flush=True)
    print(f"  Files   : browses {ROOT} (nothing leaves this machine)", flush=True)
    print("  Privacy : 100% offline, no logs", flush=True)
    print("  Stop    : press Ctrl+C", flush=True)
    print(flush=True)
    # open the default browser once the server is up (no-op on headless systems)
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host=host, port=port,
                    access_log=False, log_level="warning")
    except KeyboardInterrupt:
        pass
