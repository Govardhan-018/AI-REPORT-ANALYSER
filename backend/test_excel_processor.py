"""
Test script for excel_processor.py — verifies all 4 bug fixes.

Creates a synthetic Excel file that mimics the real structure:
  Row 1: Merged title cell spanning A1:E1
  Row 2: Merged subtitle cell spanning A2:E2
  Row 3: Column headers (#, Question, Answer, Status, Comments / Evidence Reference)
  Row 4–103: 100 data rows with question text

Then asserts:
  1. Exactly 100 questions parsed (no title/header rows)
  2. First question row_index == 4 (actual Excel row)
  3. First question text matches expected content
  4. Last question row_index == 103
  5. write_results populates the main sheet cells C4, D4, E4
  6. No header/title text appears in question list
"""

import os
import sys
import pytest
import tempfile

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from openpyxl import Workbook
from openpyxl.styles import Font
from services.excel_processor import parse_questionnaire, write_results, validate_xlsx


@pytest.fixture
def test_xlsx(tmp_path):
    """Create a test Excel file mimicking the real questionnaire structure."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Compliance Questionnaire"

    # Row 1: Merged title
    ws.merge_cells("A1:E1")
    ws["A1"] = "ComplianceAI Test Questionnaire"
    ws["A1"].font = Font(size=16, bold=True)

    # Row 2: Merged subtitle
    ws.merge_cells("A2:E2")
    ws["A2"] = "Scope: ISO 27001 Information Security Management System"
    ws["A2"].font = Font(size=12, italic=True)

    # Row 3: Column headers
    headers = ["#", "Question", "Answer", "Status", "Comments / Evidence Reference"]
    for col_idx, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col_idx, value=header)
        cell.font = Font(bold=True)

    # Rows 4–103: 100 questions
    questions = []
    for i in range(1, 101):
        row_idx = i + 3  # rows 4–103
        ws.cell(row=row_idx, column=1, value=i)  # Column A: row number
        q_text = f"[ISO 27001]  Is there a documented information security policy requirement #{i}? This control requires organizations to establish, implement, maintain, and continuously improve their ISMS."
        ws.cell(row=row_idx, column=2, value=q_text)  # Column B: question
        # Columns C, D, E are intentionally empty (to be filled by RAG)
        questions.append(q_text)

    file_path = os.path.join(str(tmp_path), "ComplianceAI_Test_Questionnaire.xlsx")
    wb.save(file_path)
    wb.close()

    return file_path, questions


def test_parse_returns_exactly_100_questions(test_xlsx):
    """BUG 1 & 4: Must parse exactly 100 questions, no title/header rows."""
    file_path, _ = test_xlsx
    result = parse_questionnaire(file_path)

    assert result["total_rows"] == 100, \
        f"Expected 100 questions, got {result['total_rows']}"


def test_first_question_row_index_is_4(test_xlsx):
    """BUG 4: First question row_index must be actual Excel row 4."""
    file_path, _ = test_xlsx
    result = parse_questionnaire(file_path)
    questions = result["questions"]

    assert len(questions) > 0, "No questions parsed"
    assert questions[0]["row_index"] == 4, \
        f"Expected first row_index=4, got {questions[0]['row_index']}"


def test_first_question_text_matches(test_xlsx):
    """BUG 2: First question text must be the actual question, not empty or from wrong column."""
    file_path, expected_questions = test_xlsx
    result = parse_questionnaire(file_path)
    questions = result["questions"]

    first_q = questions[0]["raw_row_data"]
    # The question column should be "Question" (header from row 3)
    assert "Question" in first_q, \
        f"'Question' key not in raw_row_data: {list(first_q.keys())}"

    q_text = first_q["Question"]
    assert q_text.startswith("[ISO 27001]  Is there a documented information security policy"), \
        f"First question text wrong: '{q_text[:80]}...'"


def test_last_question_row_index_is_103(test_xlsx):
    """BUG 4: Last question row_index must be actual Excel row 103."""
    file_path, _ = test_xlsx
    result = parse_questionnaire(file_path)
    questions = result["questions"]

    assert questions[-1]["row_index"] == 103, \
        f"Expected last row_index=103, got {questions[-1]['row_index']}"


def test_write_results_populates_main_sheet(test_xlsx):
    """BUG 3: write_results must write to the main sheet columns using COLUMN MAP."""
    file_path, _ = test_xlsx
    result = parse_questionnaire(file_path)

    # Create mock results for the first 3 questions
    mock_results = []
    for q in result["questions"][:3]:
        mock_results.append({
            "row_index": q["row_index"],
            "question": q["raw_row_data"].get("Question", ""),
            "answer": f"Yes — ISMS policy exists.",
            "explanation": f"Based on the uploaded documents, this control is implemented through the ISMS policy.",
            "status": "Compliant",
            "top_source_file": "ISO27001_Policy.pdf",
            "top_source_page": 12,
            "confidence_score": 0.85,
            "evidence_strength": "EXPLICIT",
            "gap_recommendation": "No gap identified.",
            "framework_tag": "ISO 27001",
            "review_flag": "NO",
            "evidence_chunks": [],
        })

    # Column mapping is no longer required for MAIN, but passed anyway
    column_mapping = {}

    output_dir = os.path.dirname(file_path)
    output_path = write_results(file_path, mock_results, column_mapping, output_dir)

    # Verify the output file
    from openpyxl import load_workbook
    from services.excel_processor import get_col_index, QUESTIONNAIRE_COLUMNS
    
    wb = load_workbook(output_path)
    ws_main = wb.active  # "Compliance Questionnaire" sheet

    # Get dynamic column indices
    ans_col = get_col_index(QUESTIONNAIRE_COLUMNS, "Answer")
    expl_col = get_col_index(QUESTIONNAIRE_COLUMNS, "Explanation")
    stat_col = get_col_index(QUESTIONNAIRE_COLUMNS, "Status")
    evid_col = get_col_index(QUESTIONNAIRE_COLUMNS, "Comments / Evidence Reference")

    # Check Answer
    assert ws_main.cell(row=4, column=ans_col).value is not None, "Answer is None"
    assert "ISMS policy exists" in ws_main.cell(row=4, column=ans_col).value
    
    # Check Explanation
    assert ws_main.cell(row=4, column=expl_col).value is not None, "Explanation is None"
    assert "implemented through the ISMS policy" in ws_main.cell(row=4, column=expl_col).value

    # Check Status
    assert ws_main.cell(row=4, column=stat_col).value in ["Compliant", "Partial", "No Evidence"]

    # Check Evidence
    e4_val = ws_main.cell(row=4, column=evid_col).value
    assert e4_val is not None and "Source:" in e4_val

    # Check summary sheet also exists and has data
    assert "ComplianceAI Results" in wb.sheetnames, "ComplianceAI Results summary sheet not created"
    ws_results = wb["ComplianceAI Results"]
    assert ws_results.cell(row=2, column=3).value is not None, "Results summary sheet row 2 column 3 is empty"

    wb.close()



def test_no_header_text_in_questions(test_xlsx):
    """BUG 1: Title and header rows must never appear as questions."""
    file_path, _ = test_xlsx
    result = parse_questionnaire(file_path)
    questions = result["questions"]

    forbidden_texts = [
        "Comments / Evidence Reference",
        "Scope: ISO 27001",
        "ComplianceAI Test Questionnaire",
        "Question",  # The literal header word by itself
    ]

    for q in questions:
        q_text = q["raw_row_data"].get("Question", "")
        for forbidden in forbidden_texts:
            assert forbidden not in q_text, \
                f"Header/title text found in question at row {q['row_index']}: '{q_text[:80]}'"


def test_validate_xlsx(test_xlsx):
    """Basic validation: our test file should pass xlsx validation."""
    file_path, _ = test_xlsx
    assert validate_xlsx(file_path), "validate_xlsx failed for a valid .xlsx file"


def test_is_valid_question_row():
    from services.excel_processor import is_valid_question_row
    assert is_valid_question_row("This is a valid question with more than 10 characters.")
    assert not is_valid_question_row("")
    assert not is_valid_question_row("short")
    assert not is_valid_question_row("12345")
    assert not is_valid_question_row("TOTAL QUESTIONS: 100")
    assert not is_valid_question_row("NOTE: This is a note.")
    assert not is_valid_question_row("SUMMARY OF CHANGES")

def test_clean_duplicate_blocks():
    from services.batch_rag import clean_duplicate_blocks
    text = "FINAL VERDICT: Yes\n\nFINAL VERDICT: Yes"
    cleaned = clean_duplicate_blocks(text)
    assert cleaned.count("FINAL VERDICT:") == 1
    
    text2 = "FINAL VERDICT: No\n\nGAP ANALYSIS: None\n\nQUESTION: Duplicate"
    cleaned2 = clean_duplicate_blocks(text2)
    assert cleaned2 == text2

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
