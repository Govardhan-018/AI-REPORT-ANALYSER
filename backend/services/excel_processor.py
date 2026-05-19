"""
Excel Processor — Parse and write compliance questionnaire .xlsx files.

Handles:
  - Auto-detecting header rows (skipping merged title/subtitle rows)
  - Reading question rows with existing data
  - Writing RAG results back to BOTH the main sheet and a summary sheet
  - Merged cell handling in question columns
  - Creating a clean ComplianceAI Results summary sheet
"""

import os
import logging
from typing import List, Dict, Tuple, Optional
from copy import copy

from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter

logger = logging.getLogger("complianceai.excel_processor")

# ─── Color fills for result rows ─────────────────────────────────────
FILL_GREEN = PatternFill(start_color="C8E6C9", end_color="C8E6C9", fill_type="solid")
FILL_YELLOW = PatternFill(start_color="FFF9C4", end_color="FFF9C4", fill_type="solid")
FILL_RED = PatternFill(start_color="FFCDD2", end_color="FFCDD2", fill_type="solid")
FILL_ORANGE = PatternFill(start_color="FFE0B2", end_color="FFE0B2", fill_type="solid")
FILL_HEADER = PatternFill(start_color="1F2937", end_color="1F2937", fill_type="solid")

FONT_HEADER = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
FONT_NORMAL = Font(name="Calibri", size=10)
FONT_BOLD = Font(name="Calibri", bold=True, size=10)

THIN_BORDER = Border(
    left=Side(style="thin", color="D4D4D4"),
    right=Side(style="thin", color="D4D4D4"),
    top=Side(style="thin", color="D4D4D4"),
    bottom=Side(style="thin", color="D4D4D4"),
)

# ─── XLSX magic bytes check ──────────────────────────────────────────
XLSX_MAGIC = b"PK"  # ZIP file signature (xlsx is a ZIP archive)

QUESTIONNAIRE_COLUMNS = [
    "No",
    "Question",
    "Answer",
    "Explanation",
    "Status",
    "Comments / Evidence Reference",
]

RESULTS_COLUMNS = [
    "Row",
    "Question",
    "Answer",
    "Explanation",
    "Status",
    "Evidence Strength",
    "Source File",
    "Page",
    "Confidence Score",
    "Gap / Recommendation",
    "Framework Tag",
    "Review Flag",
]

def get_col_index(column_list: list, column_name: str) -> int:
    if column_name not in column_list:
        raise ValueError(f"Column '{column_name}' not found in column list")
    return column_list.index(column_name) + 1


def validate_xlsx(file_path: str) -> bool:
    """Validate that the file is a genuine .xlsx file by checking magic bytes."""
    try:
        with open(file_path, "rb") as f:
            header = f.read(4)
        return header[:2] == XLSX_MAGIC
    except Exception:
        return False

def is_valid_question_row(value: str) -> bool:
    """Filter to skip invalid or summary rows before processing."""
    if not value:
        return False
    value = value.strip()
    if len(value) < 10:
        return False
    if value.isdigit():
        return False
    upper_val = value.upper()
    for prefix in ["TOTAL", "SUMMARY", "NOTE", "INSTRUCTIONS", "STEP"]:
        if upper_val.startswith(prefix):
            return False
    return True


def parse_questionnaire(file_path: str) -> Dict:
    """
    Parse an Excel questionnaire file and extract questions.

    Auto-detects the header row (the row containing column labels like
    "Question", "Answer", "Status", etc.), then finds the first actual
    data row by looking for rows where column A contains an integer
    (row number) or the question column has real question text (>20 chars).

    Handles merged title/subtitle rows at the top of the sheet.

    Args:
        file_path: Path to the .xlsx file.

    Returns:
        Dict with:
          - columns: List[str] of detected column headers
          - questions: List[QuestionRow-like dicts] with row_index = actual Excel row
          - preview_rows: First 5 rows as list of dicts
          - total_rows: Total question row count
    """
    logger.info("Parsing questionnaire: %s", file_path)

    wb = load_workbook(file_path, data_only=True)
    ws = wb.active

    # Handle merged cells: unmerge and fill values
    merged_ranges_snapshot = _get_merged_ranges(ws)
    _unmerge_cells(ws)

    # Auto-detect header row (the row with column labels)
    header_row_idx, headers = _detect_header_row(ws, merged_ranges_snapshot)
    logger.info("Detected header row %d with columns: %s", header_row_idx, headers)

    # Find the question column index (1-based)
    question_col_number = _find_question_column(headers)
    logger.info("Question column: index=%d, header='%s'",
                question_col_number, headers[question_col_number - 1])

    # Find where actual data starts (skip title/subtitle/header rows)
    data_start_row = _find_data_start(ws, header_row_idx, question_col_number)
    logger.info("Data starts at row %d", data_start_row)

    # Parse data rows — row_index is always the REAL Excel row number
    questions = []
    preview_rows = []

    for row_idx in range(data_start_row, ws.max_row + 1):
        row_data = {}
        is_empty = True

        for col_idx, header in enumerate(headers, 1):
            cell_value = ws.cell(row=row_idx, column=col_idx).value
            if cell_value is not None:
                cell_value = str(cell_value).strip()
                if cell_value:
                    is_empty = False
            row_data[header] = cell_value or ""

        if is_empty:
            continue

        # Validate that question column has real text, not just a number
        q_text = ws.cell(row=row_idx, column=question_col_number).value
        if q_text is not None:
            q_text = str(q_text).strip()
        
        if not is_valid_question_row(q_text):
            continue

        # Skip rows that look like headers/titles accidentally included
        if _is_header_like(q_text):
            logger.debug("Skipping header-like row %d: %s", row_idx, q_text[:60])
            continue

        question_entry = {
            "row_index": row_idx,       # ← REAL Excel row number (e.g., 4, 5, 6...)
            "raw_row_data": row_data,
        }
        questions.append(question_entry)

        if len(preview_rows) < 5:
            preview_rows.append(row_data)

    wb.close()

    result = {
        "columns": headers,
        "questions": questions,
        "preview_rows": preview_rows,
        "total_rows": len(questions),
    }

    logger.info("Parsed %d question rows from %s (data_start=%d, header=%d)",
                len(questions), file_path, data_start_row, header_row_idx)
    return result


def write_results(
    file_path: str,
    results: List[Dict],
    column_mapping: Dict[str, Optional[str]],
    output_dir: str,
) -> str:
    """
    Write RAG results back to the Excel file.

    Writes answers into the MAIN sheet's mapped columns (Answer, Status,
    Evidence), with per-cell color coding for the status column.
    Also adds a 'ComplianceAI Results' summary sheet.

    Args:
        file_path: Path to the original .xlsx file.
        results: List of AnswerResult dicts.
        column_mapping: Maps result fields to column header names.
        output_dir: Directory to save the output file.

    Returns:
        Path to the output file (originalname_filled.xlsx).
    """
    logger.info("Writing results to Excel: %s (%d results)", file_path, len(results))

    wb = load_workbook(file_path)
    ws = wb.active

    # Detect headers again for column index mapping
    merged_ranges_snapshot = _get_merged_ranges(ws)
    _unmerge_cells(ws)
    header_row_idx, headers = _detect_header_row(ws, merged_ranges_snapshot)
    header_to_col = {h: i + 1 for i, h in enumerate(headers)}

    # Ensure MAIN sheet has the correct headers
    for col_name in QUESTIONNAIRE_COLUMNS:
        col_idx = get_col_index(QUESTIONNAIRE_COLUMNS, col_name)
        cell = ws.cell(row=header_row_idx, column=col_idx, value=col_name)
        cell.font = Font(bold=True)
        if col_name == "Explanation":
            cell.fill = PatternFill(start_color="DDEEFF", end_color="DDEEFF", fill_type="solid")

    # ── Write results to the MAIN sheet ──────────────────────────────
    for result in results:
        row_idx = result["row_index"]

        if row_idx <= header_row_idx:
            continue

        # Write answer
        cell = ws.cell(row=row_idx, column=get_col_index(QUESTIONNAIRE_COLUMNS, "Answer"), value=result.get("answer", ""))
        cell.alignment = Alignment(wrap_text=True, vertical="top")

        # Write explanation
        cell = ws.cell(row=row_idx, column=get_col_index(QUESTIONNAIRE_COLUMNS, "Explanation"), value=result.get("explanation", ""))
        cell.alignment = Alignment(wrap_text=True, vertical="top")

        # Write status with color coding
        status = result.get("status", "")
        cell = ws.cell(row=row_idx, column=get_col_index(QUESTIONNAIRE_COLUMNS, "Status"), value=status)
        cell.alignment = Alignment(horizontal="center", vertical="top")
        cell.fill = _get_status_fill(status)

        # Write evidence reference
        evidence_text = ""
        source = result.get("top_source_file")
        if source:
            evidence_text = f"Source: {source}"
            page = result.get("top_source_page")
            if page:
                evidence_text += f", Page {page}"
            score = result.get("confidence_score", 0)
            evidence_text += f"\nConfidence: {score:.0%}"
        cell = ws.cell(row=row_idx, column=get_col_index(QUESTIONNAIRE_COLUMNS, "Comments / Evidence Reference"), value=evidence_text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")

    # Apply formatting rules to MAIN sheet
    _format_main_sheet(ws, header_row_idx, results)

    # Add ComplianceAI Results summary sheet
    _add_summary_sheet(wb, results)
    _add_dashboard_sheet(wb, results)

    # Save output file
    basename = os.path.splitext(os.path.basename(file_path))[0]
    output_filename = f"{basename}_filled.xlsx"
    output_path = os.path.join(output_dir, output_filename)

    wb.save(output_path)
    wb.close()

    logger.info("Results written to: %s", output_path)
    return output_path


# ═══════════════════════════════════════════════════════════════════════
# Internal helper functions
# ═══════════════════════════════════════════════════════════════════════

def _get_merged_ranges(ws) -> List[str]:
    """Snapshot merged cell ranges before unmerging (for header detection)."""
    return [str(r) for r in ws.merged_cells.ranges]


def _unmerge_cells(ws) -> None:
    """Unmerge all merged cells and fill each with the top-left value."""
    merged_ranges = list(ws.merged_cells.ranges)
    for merged_range in merged_ranges:
        min_row, min_col = merged_range.min_row, merged_range.min_col
        value = ws.cell(row=min_row, column=min_col).value
        ws.unmerge_cells(str(merged_range))
        for row in range(merged_range.min_row, merged_range.max_row + 1):
            for col in range(merged_range.min_col, merged_range.max_col + 1):
                ws.cell(row=row, column=col, value=value)


def _detect_header_row(ws, merged_ranges: List[str] = None) -> Tuple[int, List[str]]:
    """
    Find the header row — the row containing column labels like
    "Question", "Answer", "Status", "#", etc.

    Strategy:
      1. Skip rows that were originally merged across many columns
         (these are title/subtitle rows).
      2. Look for the first row where:
         - There are >3 non-empty cells, AND
         - At least one cell matches a known header keyword, AND
         - The row was NOT a wide merged cell (title row).
      3. Fallback: first row with >3 unique non-empty values.
    """
    # Build a set of rows that were part of wide merged ranges (likely titles)
    title_rows = set()
    if merged_ranges:
        for r_str in merged_ranges:
            try:
                from openpyxl.utils.cell import range_boundaries
                min_col, min_row, max_col, max_row = range_boundaries(r_str)
                col_span = max_col - min_col + 1
                if col_span >= 3:  # Merged across 3+ columns = likely title
                    for r in range(min_row, max_row + 1):
                        title_rows.add(r)
            except Exception:
                pass

    # Known header keywords (case-insensitive)
    header_keywords = {
        "#", "no", "no.", "question", "questions", "requirement", "requirements",
        "control", "answer", "response", "status", "comments", "evidence",
        "reference", "description", "category", "section", "clause",
    }

    for row_idx in range(1, min(ws.max_row + 1, 20)):
        if row_idx in title_rows:
            continue

        non_empty = 0
        headers = []
        has_keyword = False
        unique_values = set()

        for col_idx in range(1, ws.max_column + 1):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None and str(val).strip():
                val_str = str(val).strip()
                non_empty += 1
                headers.append(val_str)
                unique_values.add(val_str.lower())
                # Check if this cell contains a header keyword
                for word in val_str.lower().replace("/", " ").split():
                    if word in header_keywords:
                        has_keyword = True
            else:
                headers.append(f"Column_{col_idx}")

        # A real header row has multiple columns AND at least one keyword match
        # AND has more unique values than a title row (which repeats after unmerge)
        if non_empty >= 3 and has_keyword and len(unique_values) >= 3:
            return row_idx, headers

    # Fallback: first row with >3 non-empty cells and unique values
    for row_idx in range(1, min(ws.max_row + 1, 20)):
        if row_idx in title_rows:
            continue
        non_empty = 0
        headers = []
        unique_values = set()
        for col_idx in range(1, ws.max_column + 1):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None and str(val).strip():
                val_str = str(val).strip()
                non_empty += 1
                headers.append(val_str)
                unique_values.add(val_str.lower())
            else:
                headers.append(f"Column_{col_idx}")
        if non_empty > 3 and len(unique_values) >= 3:
            return row_idx, headers

    # Hard fallback: row 1
    headers = []
    for col_idx in range(1, ws.max_column + 1):
        val = ws.cell(row=1, column=col_idx).value
        if val is not None and str(val).strip():
            headers.append(str(val).strip())
        else:
            headers.append(f"Column_{col_idx}")
    return 1, headers


def _find_question_column(headers: List[str]) -> int:
    """
    Find the 1-based column index of the question/requirement column.

    Returns the column number (1-based) for use with ws.cell(column=N).
    """
    question_keywords = ["question", "requirement", "control", "description"]

    # First pass: exact header name match
    for i, h in enumerate(headers):
        h_lower = h.lower().strip()
        for kw in question_keywords:
            if kw in h_lower:
                return i + 1  # 1-based

    # Second pass: skip column A if it looks like a row number column
    # and return column B (index 2) as the likely question column
    if len(headers) >= 2:
        h0 = headers[0].lower()
        if h0 in ("#", "no", "no.", "s.no", "sr", "sr.", "s.no.", "sno",
                   "column_1", "number", "sl", "sl."):
            return 2  # Column B

    # Fallback: column B (most questionnaires have # in A, questions in B)
    return 2


def _find_data_start(ws, header_row_idx: int, question_col_number: int) -> int:
    """
    Find the first actual data row after the header.

    Strategy:
      1. Start searching from header_row + 1
      2. Look for the first row where the question column has real text
         (non-empty, not a header label, length > 10 chars)
      3. Or find the first row where column A contains an integer (row number)
      4. Fallback: header_row + 1
    """
    start_search = header_row_idx + 1

    # Strategy 1: Find first row with real question text in the question column
    for row_idx in range(start_search, min(start_search + 10, ws.max_row + 1)):
        cell_val = ws.cell(row=row_idx, column=question_col_number).value
        if cell_val is not None:
            text = str(cell_val).strip()
            if len(text) > 10 and not _is_header_like(text):
                return row_idx

    # Strategy 2: Find first row where column A is an integer
    for row_idx in range(start_search, min(start_search + 10, ws.max_row + 1)):
        val_a = ws.cell(row=row_idx, column=1).value
        if isinstance(val_a, (int, float)) and val_a == int(val_a):
            return row_idx

    # Fallback
    return start_search


def _is_header_like(text: str) -> bool:
    """Check if text looks like a header label rather than a question."""
    if not text:
        return True
    t = text.strip().lower()
    # Known header/title strings
    header_patterns = [
        "question", "questions", "requirement", "requirements",
        "control", "answer", "response", "status", "comments",
        "evidence", "reference", "description", "scope:",
        "comments / evidence", "comments/evidence",
    ]
    # Exact match or very short text that matches a header word
    if t in header_patterns:
        return True
    # Starts with "scope:" — title row content
    if t.startswith("scope:"):
        return True
    # Very short text that's just a label
    if len(t) < 5 and t.replace("#", "").replace(".", "").strip() == "":
        return True
    return False


def _resolve_column_mapping(
    ws, headers: List[str], header_to_col: Dict[str, int],
    header_row_idx: int, column_mapping: Dict[str, Optional[str]]
) -> Dict[str, Optional[int]]:
    """
    Resolve column mapping from header names to column indices.
    If a target column doesn't exist, append it at the end.
    """
    write_map = {}
    next_col = ws.max_column + 1

    field_keys = [
        "answer_column", "status_column", "evidence_column",
        "page_column", "confidence_column", "evidence_strength_column",
        "gap_column", "framework_column", "review_flag_column",
        "source_file_column"
    ]

    for field in field_keys:
        col_name = column_mapping.get(field)
        if col_name:
            if col_name in header_to_col:
                write_map[field] = header_to_col[col_name]
            else:
                # Add new column
                ws.cell(row=header_row_idx, column=next_col, value=col_name)
                ws.cell(row=header_row_idx, column=next_col).font = Font(bold=True)
                write_map[field] = next_col
                next_col += 1
        else:
            write_map[field] = None

    return write_map


def _get_status_fill(status: str) -> PatternFill:
    """Return a color fill based on the compliance status string."""
    if status == "Compliant":
        return FILL_GREEN
    elif status == "Partial":
        return FILL_YELLOW
    else:
        return FILL_RED


def _get_score_fill(score: float) -> PatternFill:
    """Return a color fill based on the confidence score."""
    if score > 0.75:
        return FILL_GREEN
    elif score >= 0.5:
        return FILL_YELLOW
    else:
        return FILL_RED


def _add_summary_sheet(wb, results: List[Dict]) -> None:
    """Add a 'ComplianceAI Results' summary sheet with formatted results."""
    # Remove existing summary sheet if present
    if "ComplianceAI Results" in wb.sheetnames:
        del wb["ComplianceAI Results"]

    ws = wb.create_sheet("ComplianceAI Results")

    # Write header row
    for col_name in RESULTS_COLUMNS:
        col_idx = get_col_index(RESULTS_COLUMNS, col_name)
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.fill = FILL_HEADER
        cell.font = FONT_HEADER
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER
        if col_name == "Explanation":
            cell.fill = PatternFill(start_color="DDEEFF", end_color="DDEEFF", fill_type="solid")
            cell.font = Font(name="Calibri", bold=True, color="000000", size=11)

    # Write data rows
    for i, result in enumerate(results, 2):
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Row"), value=result.get("row_index", ""))
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Question"), value=result.get("question", "")[:200])
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Answer"), value=result.get("answer", ""))
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Explanation"), value=result.get("explanation", ""))
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Status"), value=result.get("status", ""))
        
        strength = result.get("evidence_strength", "")
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Evidence Strength"), value=strength)
        
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Source File"), value=result.get("top_source_file", ""))
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Page"), value=result.get("top_source_page", ""))
        
        score = round(result.get("confidence_score", 0), 2)
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Confidence Score"), value=score)
        
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Gap / Recommendation"), value=result.get("gap_recommendation", ""))
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Framework Tag"), value=result.get("framework_tag", ""))
        ws.cell(row=i, column=get_col_index(RESULTS_COLUMNS, "Review Flag"), value=result.get("review_flag", ""))

        fill = _get_score_fill(score)

        for col_name in RESULTS_COLUMNS:
            col_idx = get_col_index(RESULTS_COLUMNS, col_name)
            cell = ws.cell(row=i, column=col_idx)
            if col_name == "Confidence Score":
                cell.fill = fill
            elif col_name == "Evidence Strength":
                if strength == "EXPLICIT":
                    cell.fill = FILL_GREEN
                elif strength == "IMPLICIT":
                    cell.fill = FILL_YELLOW
                elif strength == "ADJACENT":
                    cell.fill = FILL_ORANGE
                elif strength == "MISSING":
                    cell.fill = FILL_RED
            else:
                pass # keep original or white
                
            cell.font = FONT_NORMAL
            cell.border = THIN_BORDER
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    # Apply these widths and wrap settings
    col_widths = {
        "Answer": (35, True),
        "Explanation": (70, True),
        "Status": (15, False),
        "Evidence Strength": (18, False),
        "Gap / Recommendation": (60, True),
    }

    for col_name in RESULTS_COLUMNS:
        col_idx = get_col_index(RESULTS_COLUMNS, col_name)
        width, wrap = col_widths.get(col_name, (20, False))
        ws.column_dimensions[get_column_letter(col_idx)].width = width
        # Note: wrap is applied via the cell alignment already in the loop above for all cells, 
        # but to match instructions we can override:
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=col_idx).alignment = Alignment(vertical="top", wrap_text=wrap)

    # Freeze the header row
    ws.freeze_panes = "A2"

    logger.info("Summary sheet added with %d rows", len(results))

def _format_main_sheet(ws, header_row_idx: int, results: List[Dict]) -> None:
    """Apply formatting rules to the MAIN sheet."""
    # 1. Freeze the first row (header row)
    ws.freeze_panes = ws.cell(row=header_row_idx + 1, column=1).coordinate

    # 4. Bold and centre-align all header cells
    for col_idx in range(1, ws.max_column + 1):
        cell = ws.cell(row=header_row_idx, column=col_idx)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center", vertical="center")

    # Formatting widths and wraps from instructions
    col_widths = {
        "Answer": (35, True),
        "Explanation": (70, True),
        "Status": (15, False),
    }

    for col_name in QUESTIONNAIRE_COLUMNS:
        col_idx = get_col_index(QUESTIONNAIRE_COLUMNS, col_name)
        width, wrap = col_widths.get(col_name, (20, False))
        ws.column_dimensions[get_column_letter(col_idx)].width = width
        # Note: wrap is applied during writing, but we make sure the column width is set.

    # 5. Add a summary row at the very bottom with counts
    bottom_row = ws.max_row + 2
    ws.cell(row=bottom_row, column=1, value="Total")
    
    compliant_count = sum(1 for r in results if r.get("status") == "Compliant")
    partial_count = sum(1 for r in results if r.get("status") == "Partial")
    non_compliant_count = sum(1 for r in results if r.get("status") == "Non-Compliant")
    review_flag_count = sum(1 for r in results if r.get("review_flag") == "YES")

    ws.cell(row=bottom_row, column=2, value=f"{len(results)} questions")
    ws.cell(row=bottom_row, column=3, value=f"{compliant_count} Compliant")
    ws.cell(row=bottom_row, column=4, value=f"{partial_count} Partial")
    ws.cell(row=bottom_row, column=5, value=f"{non_compliant_count} Non-Compliant")
    ws.cell(row=bottom_row, column=6, value=f"{review_flag_count} Flagged for Review")

    for c in range(1, 7):
        ws.cell(row=bottom_row, column=c).font = Font(bold=True)


def _add_dashboard_sheet(wb, results: List[Dict]) -> None:
    """Add a 'Dashboard' sheet with framework stats."""
    if "Dashboard" in wb.sheetnames:
        del wb["Dashboard"]

    ws = wb.create_sheet("Dashboard")

    frameworks = {}
    for r in results:
        fw = r.get("framework_tag", "Unknown")
        status = r.get("status", "No Evidence")
        flag = r.get("review_flag", "NO")
        
        if fw not in frameworks:
            frameworks[fw] = {"Compliant": 0, "Partial": 0, "Non-Compliant": 0, "Review": 0}
            
        if status in frameworks[fw]:
            frameworks[fw][status] += 1
        frameworks[fw]["Review"] += 1 if flag == "YES" else 0

    # Table 1: Status counts per Framework
    ws.cell(row=2, column=2, value="Compliance Status by Framework").font = Font(bold=True, size=14)
    headers = ["Framework", "Compliant", "Partial", "Non-Compliant"]
    for i, h in enumerate(headers, 2):
        cell = ws.cell(row=4, column=i, value=h)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = FILL_HEADER

    r_idx = 5
    for fw, counts in frameworks.items():
        ws.cell(row=r_idx, column=2, value=fw)
        ws.cell(row=r_idx, column=3, value=counts["Compliant"])
        ws.cell(row=r_idx, column=4, value=counts["Partial"])
        ws.cell(row=r_idx, column=5, value=counts["Non-Compliant"])
        r_idx += 1

    # Table 2: Review Flags per Framework
    r_idx += 3
    ws.cell(row=r_idx, column=2, value="Review Flags by Framework").font = Font(bold=True, size=14)
    r_idx += 2
    ws.cell(row=r_idx, column=2, value="Framework").font = Font(bold=True)
    ws.cell(row=r_idx, column=3, value="Flagged for Review").font = Font(bold=True)
    ws.cell(row=r_idx, column=2).fill = FILL_HEADER
    ws.cell(row=r_idx, column=3).fill = FILL_HEADER
    
    r_idx += 1
    for fw, counts in frameworks.items():
        ws.cell(row=r_idx, column=2, value=fw)
        ws.cell(row=r_idx, column=3, value=counts["Review"])
        r_idx += 1

    ws.column_dimensions['B'].width = 20
    ws.column_dimensions['C'].width = 15
    ws.column_dimensions['D'].width = 15
    ws.column_dimensions['E'].width = 15

