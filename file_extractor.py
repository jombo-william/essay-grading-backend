"""
file_extractor.py
-----------------
Extract plain text from any file Moodle might send:
  - PDF  (pdfplumber, falls back to pypdf)
  - DOCX (python-docx)
  - Images: jpg/png/bmp/tiff (pytesseract OCR)
  - Plain text / HTML (.txt, .html, .htm)
  - ODT  (basic XML strip)

Usage:
    from file_extractor import extract_text_from_bytes
    text = extract_text_from_bytes(raw_bytes, filename="essay.pdf")
"""

import io
import re
import logging

logger = logging.getLogger(__name__)


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r'<[^>]+>', ' ', html)
    return re.sub(r'\s+', ' ', text).strip()


def extract_text_from_bytes(data: bytes, filename: str = "") -> str:
    """
    Given raw file bytes and the original filename, return extracted plain text.
    Returns empty string if extraction fails or produces nothing useful.
    """
    fname = filename.lower()

    # ── PDF ──────────────────────────────────────────────────────────────
    if fname.endswith(".pdf") or data[:4] == b"%PDF":
        return _extract_pdf(data)

    # ── DOCX ─────────────────────────────────────────────────────────────
    if fname.endswith(".docx") or fname.endswith(".doc"):
        return _extract_docx(data)

    # ── Images → OCR ─────────────────────────────────────────────────────
    if any(fname.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp")):
        return _extract_image_ocr(data)

    # ── Plain text / HTML ─────────────────────────────────────────────────
    if any(fname.endswith(ext) for ext in (".txt", ".html", ".htm")):
        try:
            raw = data.decode("utf-8", errors="replace")
            if fname.endswith((".html", ".htm")):
                return _strip_html(raw)
            return raw.strip()
        except Exception as e:
            logger.warning(f"Text decode failed for {filename}: {e}")
            return ""

    # ── ODT (basic) ───────────────────────────────────────────────────────
    if fname.endswith(".odt"):
        return _extract_odt(data)

    # ── Unknown: try PDF heuristic, then UTF-8 text ───────────────────────
    if data[:4] == b"%PDF":
        return _extract_pdf(data)

    try:
        # might be a plain-text file with a weird extension
        text = data.decode("utf-8", errors="replace").strip()
        if text:
            return text
    except Exception:
        pass

    logger.warning(f"Could not extract text from file: {filename} ({len(data)} bytes)")
    return ""


# ── extraction helpers ────────────────────────────────────────────────────

def _extract_pdf(data: bytes) -> str:
    """Try pdfplumber first (better with tables/columns), fall back to pypdf."""
    text = ""

    # pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            parts = []
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    parts.append(page_text)
            text = "\n\n".join(parts).strip()
        if text:
            return text
    except Exception as e:
        logger.warning(f"pdfplumber failed: {e}")

    # pypdf fallback
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        parts = []
        for page in reader.pages:
            page_text = page.extract_text()
            if page_text:
                parts.append(page_text)
        text = "\n\n".join(parts).strip()
        return text
    except Exception as e:
        logger.warning(f"pypdf failed: {e}")

    return ""


def _extract_docx(data: bytes) -> str:
    try:
        import docx
        doc = docx.Document(io.BytesIO(data))
        parts = [para.text for para in doc.paragraphs if para.text.strip()]
        return "\n\n".join(parts).strip()
    except Exception as e:
        logger.warning(f"DOCX extraction failed: {e}")
        return ""


def _extract_image_ocr(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(img)
        return text.strip()
    except Exception as e:
        logger.warning(f"OCR failed: {e}")
        return ""


def _extract_odt(data: bytes) -> str:
    try:
        import zipfile
        from xml.etree import ElementTree as ET
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            with z.open("content.xml") as f:
                tree = ET.parse(f)
        # strip all XML tags, collapse whitespace
        raw = ET.tostring(tree.getroot(), encoding="unicode")
        return _strip_html(raw)
    except Exception as e:
        logger.warning(f"ODT extraction failed: {e}")
        return ""