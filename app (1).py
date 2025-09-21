import fitz  # PyMuPDF for PDF text extraction
import sympy
import gradio as gr
import re

# ---------- Storage ----------
marking_scheme = {}
results = {}


# ---------- PDF Extraction ----------
def extract_latex_from_pdf(pdf_file):
    text = ""
    try:
        # Handle Gradio file type (dict with "name")
        if isinstance(pdf_file, dict) and "name" in pdf_file:
            pdf_file = pdf_file["name"]
        pdf_file = str(pdf_file)

        with fitz.open(pdf_file) as pdf:
            for page in pdf:
                text += page.get_text("text") + "\n"
    except Exception as e:
        return f"ERROR reading PDF: {e}"
    return text.strip()


# ---------- Clean Expressions ----------
def clean_expression(expr):
    expr = expr.strip()
    expr = re.sub(r'^[A-Za-z\s]*=', '', expr)  # remove prefixes like R=
    expr = expr.replace("^", "**")  # convert power to Python
    return expr.strip()


# ---------- Combine Fractions ----------
def combine_fractions(lines):
    """
    Merge numerator, line, denominator into a single fraction expression if detected.
    """
    new_lines = []
    skip_next = False
    for i in range(len(lines)):
        if skip_next:
            skip_next = False
            continue

        if re.fullmatch(r"[-=─_]{3,}", lines[i].strip()) and i > 0 and i < len(lines) - 1:
            numerator = lines[i - 1].strip()
            denominator = lines[i + 1].strip()
            fraction_expr = f"({numerator})/({denominator})"
            new_lines[-1] = fraction_expr
            skip_next = True
        else:
            new_lines.append(lines[i])
    return new_lines


# ---------- Parse Marking Scheme ----------
def parse_marking_scheme(lines):
    """
    Convert filtered lines into marking scheme rows.
    Each line becomes an expected answer, max_marks=2, type=expression, tolerance=0
    """
    scheme = []
    for line in lines:
        if not line.strip():
            continue
        scheme.append([
            line.strip(),
            line.strip(),
            2,
            "expression",
            0
        ])
    return scheme


def process_teacher_pdf(pdf_file):
    text = extract_latex_from_pdf(pdf_file)
    if text.startswith("ERROR"):
        return [[text, text, 0, "expression", 0]]

    lines = text.splitlines()
    answers = []
    buffer = ""

    for l in lines:
        l = l.strip()
        if not l:
            continue
        if l.startswith("R =") or l.startswith("R="):
            l = l.split("=", 1)[1].strip()
        if l == "=":
            continue
        if l.startswith("=") and buffer:
            buffer += " " + l[1:].strip()
        else:
            if buffer:
                answers.append(buffer)
            buffer = l
    if buffer:
        answers.append(buffer)

    return parse_marking_scheme(answers)


# ---------- Save Marking Scheme ----------
def save_marking_scheme(df):
    global marking_scheme
    marking_scheme = {"scheme": df}
    return "Marking scheme saved successfully!"


# ---------- Evaluate Student ----------
def evaluate_student(pdf_file, roll_no):
    global results, marking_scheme
    try:
        if not marking_scheme:
            return "No marking scheme available. Please create it first."

        student_text = extract_latex_from_pdf(pdf_file)
        if student_text.startswith("ERROR"):
            return f"ERROR: Could not read student PDF for {roll_no}"

        student_lines = student_text.splitlines()
        student_lines = combine_fractions(student_lines)

        obtained = 0
        detailed = []

        for i, scheme in enumerate(marking_scheme["scheme"]):
            try:
                expected = clean_expression(str(scheme[1]))
                max_marks = scheme[2]
                compare_type = scheme[3]
                tol = scheme[4]

                student_ans = clean_expression(student_lines[i]) if i < len(student_lines) else ""

                awarded = 0
                reason = ""

                if compare_type == "text":
                    if student_ans.strip().lower() == expected.strip().lower():
                        awarded = max_marks
                    else:
                        reason = f"Expected '{expected}', got '{student_ans}'"

                elif compare_type == "numeric":
                    try:
                        if abs(float(student_ans) - float(expected)) <= tol:
                            awarded = max_marks
                        else:
                            reason = f"Expected {expected}, got {student_ans}"
                    except:
                        reason = f"Unreadable numeric answer: {student_ans}"

                elif compare_type == "expression":
                    try:
                        expr_student = sympy.sympify(student_ans)
                        expr_expected = sympy.sympify(expected)

                        if sympy.simplify(expr_student - expr_expected) == 0:
                            awarded = max_marks
                        elif sympy.simplify(expr_student).evalf() == sympy.simplify(expr_expected).evalf():
                            awarded = max_marks
                        elif abs(float(expr_student.evalf()) - float(expr_expected.evalf())) <= tol:
                            awarded = max_marks
                        else:
                            reason = f"Expression differs: {student_ans}"
                    except Exception as e:
                        reason = f"Parse error: {e}"

                obtained += awarded
                detailed.append({
                    "question": expected,
                    "student_answer": student_ans,
                    "marks_awarded": awarded,
                    "max_marks": max_marks,
                    "reason": reason
                })

            except Exception as e:
                detailed.append({
                    "question": "Error in marking",
                    "student_answer": "",
                    "marks_awarded": 0,
                    "max_marks": scheme[2] if len(scheme) > 2 else 0,
                    "reason": f"Error: {e} -> Manual inspection"
                })

        results[roll_no] = {"total": obtained, "details": detailed}
        return f"Evaluation completed for {roll_no}. Total Marks: {obtained}"

    except Exception as e:
        return f"Unexpected error: {e}"


# ---------- Get Results ----------
def get_result(roll_no):
    if roll_no not in results:
        return "No result found for this roll number."

    data = results[roll_no]
    total = data["total"]
    details = data["details"]

    report = [f"Roll No: {roll_no}", f"Total Marks: {total}\n"]
    for i, d in enumerate(details, start=1):
        line = (
            f"Q{i}: {d['marks_awarded']}/{d['max_marks']} | "
            f"Expected: {d['question']} | Student: {d['student_answer']}"
        )
        if d["reason"]:
            line += f" | Note: {d['reason']}"
        report.append(line)

    return "\n".join(report)


# ---------- Gradio UI ----------
def build_demo():
    with gr.Blocks() as demo:
        with gr.Tab("Teacher: Upload & Marking Scheme"):
            corrected_pdf = gr.File(label="Upload corrected answer sheet PDF")
            scan_btn = gr.Button("Extract Lines")
            df = gr.Dataframe(
                headers=["line_text", "expected_answer", "max_marks", "compare_type", "tolerance"],
                datatype=["str", "str", "number", "str", "number"],
                row_count=(0, "dynamic"),
                interactive=True
            )
            save_btn = gr.Button("Save Marking Scheme")
            save_output = gr.Textbox()

            scan_btn.click(fn=process_teacher_pdf, inputs=corrected_pdf, outputs=df)
            save_btn.click(fn=save_marking_scheme, inputs=df, outputs=save_output)

        with gr.Tab("Student: Upload Answer Sheet"):
            student_pdf = gr.File(label="Upload student answer sheet PDF")
            roll_no = gr.Textbox(label="Enter Roll No")
            eval_btn = gr.Button("Evaluate Answer Sheet")
            eval_output = gr.Textbox()

            eval_btn.click(fn=evaluate_student, inputs=[student_pdf, roll_no], outputs=eval_output)

        with gr.Tab("Results"):
            roll_no_query = gr.Textbox(label="Enter Roll No")
            result_btn = gr.Button("Get Result")
            result_output = gr.Textbox()

            result_btn.click(fn=get_result, inputs=roll_no_query, outputs=result_output)
    return demo


demo = build_demo()
