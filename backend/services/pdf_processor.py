"""
PDF Processor — Extract text page-by-page from PDF files.

Uses PyMuPDF (fitz) as the primary extractor for speed,
with pdfplumber as fallback for pages with low text yield.
"""

import os
import re
import logging
from typing import List

import pymupdf
import pdfplumber

from models.schemas import PageContent

logger = logging.getLogger("complianceai.pdf_processor")

# Minimum character threshold — if PyMuPDF extracts fewer chars, try pdfplumber
MIN_TEXT_THRESHOLD = 50


def extract_pages(filepath: str) -> List[PageContent]:
    """
    Extract text content from each page of a PDF file.

    Args:
        filepath: Absolute path to the PDF file.

    Returns:
        List of PageContent objects, one per page.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"PDF file not found: {filepath}")

    filename = os.path.basename(filepath)
    pages: List[PageContent] = []

    logger.info("Processing PDF: %s", filename)

    # Primary extraction with PyMuPDF
    try:
        doc = pymupdf.open(filepath)
        total_pages = len(doc)
        logger.info("PDF has %d pages", total_pages)

        for page_num in range(total_pages):
            page = doc[page_num]
            text = page.get_text("text")
            text = _clean_text(text)

            # If PyMuPDF yields too little text, try pdfplumber for this page
            if len(text.strip()) < MIN_TEXT_THRESHOLD:
                fallback_text = _extract_page_with_pdfplumber(filepath, page_num)
                if fallback_text and len(fallback_text.strip()) > len(text.strip()):
                    text = fallback_text
                    logger.debug("Used pdfplumber fallback for page %d", page_num + 1)

            if text.strip():
                pages.append(PageContent(
                    page_number=page_num + 1,  # 1-indexed
                    text=text,
                    metadata={
                        "filename": filename,
                        "total_pages": total_pages,
                    }
                ))

        doc.close()

    except Exception as e:
        logger.error("PyMuPDF failed for %s: %s. Trying full pdfplumber extraction.", filename, e)
        pages = _extract_all_with_pdfplumber(filepath, filename)

    logger.info("Extracted %d pages with text from %s", len(pages), filename)
    return pages


def _extract_page_with_pdfplumber(filepath: str, page_index: int) -> str:
    """Extract text from a single page using pdfplumber."""
    try:
        with pdfplumber.open(filepath) as pdf:
            if page_index < len(pdf.pages):
                page = pdf.pages[page_index]
                text = page.extract_text(layout=True) or ""
                return _clean_text(text)
    except Exception as e:
        logger.debug("pdfplumber fallback failed for page %d: %s", page_index, e)
    return ""


def _extract_all_with_pdfplumber(filepath: str, filename: str) -> List[PageContent]:
    """Full extraction using pdfplumber as fallback."""
    pages: List[PageContent] = []
    try:
        with pdfplumber.open(filepath) as pdf:
            total_pages = len(pdf.pages)
            for page_num, page in enumerate(pdf.pages):
                text = page.extract_text(layout=True) or ""
                text = _clean_text(text)
                if text.strip():
                    pages.append(PageContent(
                        page_number=page_num + 1,
                        text=text,
                        metadata={
                            "filename": filename,
                            "total_pages": total_pages,
                            "extractor": "pdfplumber",
                        }
                    ))
    except Exception as e:
        logger.error("pdfplumber extraction also failed: %s", e)
    return pages


def _clean_text(text: str) -> str:
    """Clean extracted text: remove control chars, normalize whitespace."""
    if not text:
        return ""

    # Remove control characters (except newlines and tabs)
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]', '', text)

    # Normalize multiple spaces to single space (preserve newlines)
    text = re.sub(r'[^\S\n]+', ' ', text)

    # Normalize multiple newlines to max double newline
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Strip leading/trailing whitespace per line
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)

    return text.strip()
