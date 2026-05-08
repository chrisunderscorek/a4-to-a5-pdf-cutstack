#!/usr/bin/env python3
"""
Generic A4/A5-to-A5 duplex cut-stack imposition with automatic signature-page flattening.

Use case:
    Turn A4 documentation into A5 print output on A4 landscape paper.

Default use:
    python pdf_a5_cutstack_auto_signature_flatten.py input.pdf output.pdf

Automatic output filename:
    python pdf_a5_cutstack_auto_signature_flatten.py input.pdf --outdir out
    # writes out/input_cutstack.pdf

Local Python virtual environment setup:
    python3 -m venv .venv
    .venv/bin/python -m pip install pymupdf

Run with the local virtual environment:
    .venv/bin/python pdf_a5_cutstack_auto_signature_flatten.py input.pdf output.pdf

What it does by default:
  1. Opens input.pdf and checks every page for PDF signature widgets/fields.
  2. Renders only pages that contain signature widgets to an image first.
     This preserves visual signature appearances that can disappear when PDF
     pages are copied into a new imposed PDF.
  3. Copies all other pages as vector PDF pages.
  4. Creates an A4 landscape 2-up duplex cut-stack PDF.

The output PDF is intended for:
  - A4 paper
  - landscape orientation
  - duplex printing, usually flip on short edge
  - cutting the full A4 stack in the middle
  - placing the right A5 stack behind the left A5 stack

Requirements:
    Python 3 with PyMuPDF installed in the active environment.
    For this folder, the intended setup is the local .venv shown above.

Recommended for best signature rendering:
    Install Poppler so that the command 'pdftoppm' is on PATH.

Notes:
  - Acrobat Reader is not required.
  - Pages with signatures are rasterized. This preserves the visible signature
    for printing, but the rendered page is no longer searchable/selectable.
  - This script preserves visual appearances, not cryptographic signature validity
    after rewriting/imposition. That is normal for print-preparation workflows.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import fitz  # PyMuPDF


DEFAULT_DPI = 300


@dataclass(frozen=True)
class SignatureHit:
    page_index: int  # zero based
    field_name: str
    xref: Optional[int]
    reason: str


@dataclass(frozen=True)
class ProcessReport:
    source_pages: int
    padded_pages: int
    output_pages: int
    signature_pages: list[int]  # one based
    renderer: str
    mode: str


def warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)


def info(message: str) -> None:
    print(message, file=sys.stderr)


def _fit_rect(src_rect: fitz.Rect, dst_rect: fitz.Rect) -> fitz.Rect:
    """Return a centered rectangle with src aspect ratio inside dst_rect."""
    src_w, src_h = src_rect.width, src_rect.height
    dst_w, dst_h = dst_rect.width, dst_rect.height
    if src_w <= 0 or src_h <= 0 or dst_w <= 0 or dst_h <= 0:
        raise ValueError("Invalid rectangle size")
    scale = min(dst_w / src_w, dst_h / src_h)
    w = src_w * scale
    h = src_h * scale
    x0 = dst_rect.x0 + (dst_w - w) / 2
    y0 = dst_rect.y0 + (dst_h - h) / 2
    return fitz.Rect(x0, y0, x0 + w, y0 + h)


def _parse_page_spec(spec: str, page_count: int) -> set[int]:
    """Parse a 1-based page specification like '1,3-5' into zero-based indices."""
    result: set[int] = set()
    if not spec.strip():
        return result
    for part in spec.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            start = int(a)
            end = int(b)
            if start > end:
                start, end = end, start
            for p in range(start, end + 1):
                if 1 <= p <= page_count:
                    result.add(p - 1)
                else:
                    raise ValueError(f"Page {p} is outside the document range 1-{page_count}")
        else:
            p = int(part)
            if 1 <= p <= page_count:
                result.add(p - 1)
            else:
                raise ValueError(f"Page {p} is outside the document range 1-{page_count}")
    return result


def _annotation_xrefs_from_page(doc: fitz.Document, page: fitz.Page) -> list[int]:
    """Return annotation xrefs listed in a page's Annots array.

    This is a fallback for PDFs where signature widgets are not exposed by
    page.widgets() in the expected way.
    """
    try:
        key_type, key_value = doc.xref_get_key(page.xref, "Annots")
    except Exception:
        return []
    if key_type not in {"array", "xref"} or not key_value:
        return []
    return [int(n) for n in re.findall(r"(\d+)\s+0\s+R", key_value)]


def _xref_looks_like_signature_widget(doc: fitz.Document, xref: int) -> bool:
    try:
        obj = doc.xref_object(xref, compressed=False)
    except Exception:
        return False
    normalized = re.sub(r"\s+", " ", obj)
    # Signature widgets usually contain /Subtype /Widget and /FT /Sig.
    return ("/FT /Sig" in normalized or "/FT/Sig" in normalized) and "/Widget" in normalized


def find_signature_pages(source_pdf: Path, forced_pages: str = "") -> tuple[list[SignatureHit], set[int]]:
    """Find pages containing PDF signature widgets/fields.

    Returns all hits and the set of zero-based page indices to rasterize.
    """
    source_pdf = Path(source_pdf)
    doc = fitz.open(source_pdf)
    hits: list[SignatureHit] = []
    pages_to_rasterize: set[int] = set()

    try:
        forced = _parse_page_spec(forced_pages, len(doc)) if forced_pages else set()
        for page_index in forced:
            hits.append(SignatureHit(page_index, "<forced>", None, "forced by --force-raster-pages"))
            pages_to_rasterize.add(page_index)

        signature_type = getattr(fitz, "PDF_WIDGET_TYPE_SIGNATURE", 6)

        for page_index, page in enumerate(doc):
            # Primary path: PyMuPDF widgets.
            try:
                widgets = list(page.widgets() or [])
            except Exception:
                widgets = []

            for widget in widgets:
                field_type = getattr(widget, "field_type", None)
                field_type_string = (getattr(widget, "field_type_string", "") or "").lower()
                field_name = getattr(widget, "field_name", "") or "<unnamed>"
                xref = getattr(widget, "xref", None)

                is_sig_type = field_type == signature_type or "signature" in field_type_string
                is_sig_xref = bool(xref and _xref_looks_like_signature_widget(doc, int(xref)))
                if is_sig_type or is_sig_xref:
                    reason = "signature widget"
                    if is_sig_xref and not is_sig_type:
                        reason = "widget xref contains /FT /Sig"
                    hits.append(SignatureHit(page_index, field_name, int(xref) if xref else None, reason))
                    pages_to_rasterize.add(page_index)

            # Fallback path: raw page Annots array.
            for xref in _annotation_xrefs_from_page(doc, page):
                if _xref_looks_like_signature_widget(doc, xref):
                    already_seen = any(h.page_index == page_index and h.xref == xref for h in hits)
                    if not already_seen:
                        hits.append(SignatureHit(page_index, f"xref {xref}", xref, "page Annots contains /FT /Sig"))
                        pages_to_rasterize.add(page_index)

        return hits, pages_to_rasterize
    finally:
        doc.close()


def _render_page_with_pdftoppm(source_pdf: Path, page_index: int, output_png: Path, dpi: int) -> None:
    """Render a single page using Poppler/pdftoppm.

    page_index is zero based; pdftoppm expects one based.
    """
    if shutil.which("pdftoppm") is None:
        raise RuntimeError("pdftoppm is not available")

    output_png.parent.mkdir(parents=True, exist_ok=True)
    prefix = output_png.with_suffix("")
    cmd = [
        "pdftoppm",
        "-png",
        "-singlefile",
        "-f",
        str(page_index + 1),
        "-l",
        str(page_index + 1),
        "-r",
        str(dpi),
        str(source_pdf),
        str(prefix),
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            "pdftoppm failed for page "
            f"{page_index + 1}: {result.stderr.strip() or result.stdout.strip()}"
        )
    rendered = prefix.with_suffix(".png")
    if rendered != output_png and rendered.exists():
        shutil.move(str(rendered), str(output_png))
    if not output_png.exists():
        raise RuntimeError(f"pdftoppm did not create expected file: {output_png}")


def _render_page_with_pymupdf(source_pdf: Path, page_index: int, output_png: Path, dpi: int) -> None:
    """Render a single page using PyMuPDF as a fallback."""
    doc = fitz.open(source_pdf)
    try:
        zoom = dpi / 72.0
        matrix = fitz.Matrix(zoom, zoom)
        pix = doc[page_index].get_pixmap(matrix=matrix, alpha=False, annots=True)
        output_png.parent.mkdir(parents=True, exist_ok=True)
        pix.save(output_png)
    finally:
        doc.close()


def choose_renderer(requested: str) -> str:
    """Resolve renderer selection to 'pdftoppm' or 'pymupdf'."""
    requested = requested.lower()
    if requested == "auto":
        if shutil.which("pdftoppm") is not None:
            return "pdftoppm"
        warn("pdftoppm was not found; falling back to PyMuPDF rendering")
        return "pymupdf"
    if requested == "pdftoppm" and shutil.which("pdftoppm") is None:
        raise RuntimeError("--renderer pdftoppm selected, but pdftoppm was not found on PATH")
    if requested not in {"pdftoppm", "pymupdf"}:
        raise ValueError("renderer must be auto, pdftoppm, or pymupdf")
    return requested


def render_page_to_png(source_pdf: Path, page_index: int, output_png: Path, dpi: int, renderer: str) -> None:
    if renderer == "pdftoppm":
        _render_page_with_pdftoppm(source_pdf, page_index, output_png, dpi)
    elif renderer == "pymupdf":
        _render_page_with_pymupdf(source_pdf, page_index, output_png, dpi)
    else:
        raise ValueError(f"Unsupported renderer: {renderer}")


def flatten_signature_pages(
    source_pdf: Path,
    output_pdf: Path,
    pages_to_rasterize: set[int],
    dpi: int = DEFAULT_DPI,
    renderer: str = "auto",
) -> None:
    """Copy source PDF while replacing selected pages with rendered images."""
    source_pdf = Path(source_pdf)
    output_pdf = Path(output_pdf)
    renderer = choose_renderer(renderer)

    src = fitz.open(source_pdf)
    out = fitz.open()
    try:
        with tempfile.TemporaryDirectory(prefix="signature_page_renders_") as tmpdir_name:
            tmpdir = Path(tmpdir_name)
            for page_index in range(len(src)):
                if page_index in pages_to_rasterize:
                    png = tmpdir / f"page_{page_index + 1:05d}.png"
                    render_page_to_png(source_pdf, page_index, png, dpi=dpi, renderer=renderer)
                    rect = src[page_index].rect
                    page = out.new_page(width=rect.width, height=rect.height)
                    # pdftoppm/PyMuPDF already rendered the page at the correct aspect ratio.
                    page.insert_image(page.rect, filename=str(png), keep_proportion=False)
                else:
                    out.insert_pdf(src, from_page=page_index, to_page=page_index)

        out.set_metadata({
            "title": f"Signature-page-flattened copy of {source_pdf.name}",
            "creator": "pdf_a5_cutstack_auto_signature_flatten.py / PyMuPDF",
            "producer": "PyMuPDF",
            "subject": "Pages with PDF signature widgets were rasterized to preserve visible appearances",
        })
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        out.save(output_pdf, garbage=4, deflate=True)
    finally:
        out.close()
        src.close()


def impose_a5_to_a4_cutstack(source_pdf: Path, output_pdf: Path) -> tuple[int, int, int]:
    """Create an A4 landscape 2-up duplex cut-stack PDF.

    Page order per physical A4 sheet, one-based logical pages:
      front:  2*i+1        | 2*S+2*i+1
      back:   2*S+2*i+2    | 2*i+2
    where S is the number of A4 sheets after padding to a multiple of 4.

    Returns: (source_page_count, padded_page_count, output_page_count)
    """
    source_pdf = Path(source_pdf)
    output_pdf = Path(output_pdf)
    src = fitz.open(source_pdf)
    out = fitz.open()

    try:
        page_count = len(src)
        padded_count = ((page_count + 3) // 4) * 4
        sheet_count = padded_count // 4

        # Standard A4 in PDF points. Landscape means width > height.
        a4 = fitz.paper_rect("a4")
        page_w = a4.height
        page_h = a4.width
        half_w = page_w / 2
        left_rect = fitz.Rect(0, 0, half_w, page_h)
        right_rect = fitz.Rect(half_w, 0, page_w, page_h)

        def place(dst_page: fitz.Page, logical_idx: int, rect: fitz.Rect) -> None:
            if logical_idx >= page_count:
                return  # padded blank page
            src_rect = src[logical_idx].rect
            target = _fit_rect(src_rect, rect)
            dst_page.show_pdf_page(target, src, logical_idx, keep_proportion=True)

        for i in range(sheet_count):
            front = out.new_page(width=page_w, height=page_h)
            place(front, 2 * i, left_rect)
            place(front, 2 * sheet_count + 2 * i, right_rect)

            back = out.new_page(width=page_w, height=page_h)
            place(back, 2 * sheet_count + 2 * i + 1, left_rect)
            place(back, 2 * i + 1, right_rect)

        out.set_metadata({
            "title": f"A4 duplex cut-stack imposition - {source_pdf.name}",
            "creator": "pdf_a5_cutstack_auto_signature_flatten.py / PyMuPDF",
            "producer": "PyMuPDF",
            "subject": "2-up A5 on A4 landscape, duplex, cut-stack order",
        })
        output_pdf.parent.mkdir(parents=True, exist_ok=True)
        out.save(output_pdf, garbage=4, deflate=True, clean=True)
        return page_count, padded_count, len(out)
    finally:
        out.close()
        src.close()


def process_pdf(
    input_pdf: Path,
    output_pdf: Path,
    *,
    mode: str = "cutstack",
    dpi: int = DEFAULT_DPI,
    renderer: str = "auto",
    force_raster_pages: str = "",
    keep_flattened: Optional[Path] = None,
) -> ProcessReport:
    input_pdf = Path(input_pdf)
    output_pdf = Path(output_pdf)
    if not input_pdf.exists():
        raise FileNotFoundError(input_pdf)
    if input_pdf.resolve() == output_pdf.resolve():
        raise ValueError("Input and output must be different files")
    if dpi < 72:
        raise ValueError("DPI must be at least 72")
    if mode not in {"cutstack", "flatten"}:
        raise ValueError("mode must be cutstack or flatten")

    hits, pages_to_rasterize = find_signature_pages(input_pdf, force_raster_pages)
    renderer_used = choose_renderer(renderer)
    sig_pages_one_based = sorted(p + 1 for p in pages_to_rasterize)

    if sig_pages_one_based:
        fields_by_page: dict[int, list[str]] = {}
        for hit in hits:
            fields_by_page.setdefault(hit.page_index + 1, []).append(hit.field_name)
        details = ", ".join(
            f"p{page}: {', '.join(names)}" for page, names in sorted(fields_by_page.items())
        )
        info(f"Signature pages detected: {details}")
    else:
        info("No signature widgets detected.")

    with tempfile.TemporaryDirectory(prefix="pdf_signature_flatten_work_") as tmpdir_name:
        tmpdir = Path(tmpdir_name)
        flat_pdf = keep_flattened or tmpdir / "signature_pages_flattened.pdf"

        # Always write a flattened working copy. If no signature pages were found,
        # this is a normal vector copy of the source PDF.
        flatten_signature_pages(
            input_pdf,
            flat_pdf,
            pages_to_rasterize,
            dpi=dpi,
            renderer=renderer_used,
        )

        if mode == "flatten":
            if keep_flattened and Path(keep_flattened).resolve() == output_pdf.resolve():
                # Already written to requested output.
                pass
            else:
                shutil.copyfile(flat_pdf, output_pdf)
            doc = fitz.open(output_pdf)
            try:
                source_pages = len(doc)
            finally:
                doc.close()
            return ProcessReport(
                source_pages=source_pages,
                padded_pages=source_pages,
                output_pages=source_pages,
                signature_pages=sig_pages_one_based,
                renderer=renderer_used,
                mode=mode,
            )

        source_pages, padded_pages, output_pages = impose_a5_to_a4_cutstack(flat_pdf, output_pdf)
        return ProcessReport(
            source_pages=source_pages,
            padded_pages=padded_pages,
            output_pages=output_pages,
            signature_pages=sig_pages_one_based,
            renderer=renderer_used,
            mode=mode,
        )


def output_path_from_outdir(input_pdf: Path, outdir: Path) -> Path:
    """Build the default cut-stack output path for an input PDF and output directory."""
    return Path(outdir) / f"{Path(input_pdf).stem}_cutstack.pdf"


def parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan every PDF page for signature widgets, rasterize those pages, "
            "and create an A4 landscape 2-up duplex cut-stack PDF."
        )
    )
    parser.add_argument("input", type=Path, help="Input PDF, normally A5 portrait")
    parser.add_argument(
        "output",
        type=Path,
        nargs="?",
        help="Output PDF. Omit this when using --outdir.",
    )
    parser.add_argument(
        "--outdir",
        type=Path,
        default=None,
        help="Output directory. Writes <input-name>_cutstack.pdf there.",
    )
    parser.add_argument(
        "--mode",
        choices=["cutstack", "flatten"],
        default="cutstack",
        help="cutstack = print-ready A4 2-up output; flatten = original order with signature pages rasterized",
    )
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="Render DPI for pages with signature widgets")
    parser.add_argument(
        "--renderer",
        choices=["auto", "pdftoppm", "pymupdf"],
        default="auto",
        help="Renderer for signature pages. auto prefers pdftoppm and falls back to PyMuPDF.",
    )
    parser.add_argument(
        "--force-raster-pages",
        default="",
        help="Optional 1-based pages to rasterize even if no signature widget is detected, e.g. '4' or '2,4-5'.",
    )
    parser.add_argument(
        "--keep-flattened",
        type=Path,
        default=None,
        help="Optional path to keep the intermediate original-order PDF with signature pages rasterized.",
    )
    args = parser.parse_args(argv)
    if args.output is None and args.outdir is None:
        parser.error("provide either an output PDF path or --outdir")
    if args.output is not None and args.outdir is not None:
        parser.error("use either an output PDF path or --outdir, not both")
    if args.outdir is not None:
        args.output = output_path_from_outdir(args.input, args.outdir)
    return args


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = parse_args(argv)
    report = process_pdf(
        args.input,
        args.output,
        mode=args.mode,
        dpi=args.dpi,
        renderer=args.renderer,
        force_raster_pages=args.force_raster_pages,
        keep_flattened=args.keep_flattened,
    )

    info(f"Renderer used for signature pages: {report.renderer}")
    info(f"Mode: {report.mode}")
    info(f"Input pages after signature-page flattening: {report.source_pages}")
    if report.mode == "cutstack":
        info(f"Padded logical pages: {report.padded_pages}")
        info(f"Output A4 pages: {report.output_pages}")
    info(f"Wrote: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
