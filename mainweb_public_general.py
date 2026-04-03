import concurrent.futures
import json
import os
import re
import zipfile
from datetime import datetime
from io import BytesIO

import requests
import streamlit as st
from PyPDF2 import PdfMerger
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


st.set_page_config(page_title="PaperPort Public", page_icon="🎓", layout="wide")

LEVELS = st.secrets["LEVELS"]
DOWNLOAD_DIR = st.secrets["DOWNLOAD_DIR"]
HEADERS = json.loads(st.secrets["HEADERS"])
SESSIONS_ALL = st.secrets["SESSIONS_ALL"]

IGCSE_SUBJECTS = json.loads(st.secrets["IGCSE_SUBJECTS"])
ALEVEL_SUBJECTS = json.loads(st.secrets["ALEVEL_SUBJECTS"])

DATA_FILE = "data.json"
REQUESTS_FILE = "custom_school_requests.json"
DEFAULT_END_PAGE_PATH = "end.pdf"
DEFAULT_FONT_PATH = "Poppins-Bold.ttf"

SESSION_OPTIONS = {
    "FEB/MAR": "m",
    "MAY/JUN": "s",
    "OCT/NOV": "w",
}

PAPER_TYPE_OPTIONS = {
    "Question Paper": "qp",
    "Mark Scheme": "ms",
    "Insert": "in",
    "Grade Thresholds": "gt",
}


st.markdown(
    """
<style>
.logo-container {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    margin-bottom: -10px;
}
.logo-container img {
    height: 80px;
    margin-bottom: 10px;
}
.logo-title {
    font-size: 2rem;
    font-weight: 700;
    color: #0A1D4E;
    font-family: 'Poppins', sans-serif;
}
.page-card {
    background: #f7f9fc;
    border: 1px solid #e3e8f0;
    border-radius: 18px;
    padding: 22px;
}
</style>
<div class="logo-container">
    <img src="https://raw.githubusercontent.com/Fe4nando/ComplieYourPapers/main/logo.png">
    <div class="logo-title">PaperPort Public General Version</div>
</div>
""",
    unsafe_allow_html=True,
)


def ensure_json_file(path, default_content):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_content, f, indent=4)


ensure_json_file(DATA_FILE, {"total_downloads": 0, "logs": []})
ensure_json_file(REQUESTS_FILE, {"requests": []})


def register_cover_font():
    if os.path.exists(DEFAULT_FONT_PATH):
        try:
            pdfmetrics.registerFont(TTFont("PoppinsBoldPublic", DEFAULT_FONT_PATH))
            return "PoppinsBoldPublic"
        except Exception:
            pass
    return "Helvetica-Bold"


COVER_FONT_NAME = register_cover_font()


def update_data_log(level, subject_name, subject_code, num_papers, success_count, fail_count):
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["total_downloads"] += 1
    data["logs"].append(
        {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "subject_name": subject_name,
            "subject_code": subject_code,
            "papers_selected": num_papers,
            "success": success_count,
            "failed": fail_count,
        }
    )

    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def save_school_request(payload):
    with open(REQUESTS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    data["requests"].append(payload)

    with open(REQUESTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def format_papers(text):
    cleaned = re.sub(r"\D", "", text)
    groups = [cleaned[i : i + 2] for i in range(0, len(cleaned), 2)]
    return " ".join([g for g in groups if g])


def build_cover_lines(subject_name, paper_type_short, paper_no, level, subject_code):
    heading_level = "A-LEVEL" if level == "A Level" else "IGCSE"
    heading = f"{heading_level} {subject_code}"

    if paper_type_short == "gt":
        paper_line = "GRADE THRESHOLDS"
    else:
        paper_labels = {
            "qp": "QUESTION PAPER",
            "ms": "MARK SCHEME",
            "in": "INSERT",
        }
        paper_line = f"{paper_labels.get(paper_type_short, paper_type_short.upper())} {paper_no}"

    return heading, subject_name.upper(), paper_line


def draw_public_cover_background(cover, page_width, page_height):
    cover.setFillColor(HexColor("#13245A"))
    cover.rect(0, 0, page_width, page_height, fill=1, stroke=0)

    cover.saveState()
    cover.translate(page_width / 2, page_height / 2)
    cover.rotate(45)
    cover.setFillColor(Color(0.06, 0.10, 0.24, alpha=0.24))
    cover.setFont(COVER_FONT_NAME, 26)
    for x in range(-900, 1000, 290):
        for y in range(-1300, 1400, 145):
            cover.drawString(x, y, "COMPILED BY PAPERPILOT")
    cover.restoreState()


def create_public_cover_pdf(level, subject_name, subject_code, paper_type_short, paper_no):
    packet = BytesIO()
    page_width, page_height = A4
    cover = canvas.Canvas(packet, pagesize=A4)

    draw_public_cover_background(cover, page_width, page_height)
    heading, title, paper_line = build_cover_lines(
        subject_name, paper_type_short, paper_no, level, subject_code
    )

    left_margin = 70
    title_y = 560
    line_gap = 64

    cover.setFillColor(white)
    cover.setFont(COVER_FONT_NAME, 30)
    cover.drawString(left_margin, title_y, heading[:24])

    cover.setFont(COVER_FONT_NAME, 38)
    cover.drawString(left_margin, title_y - line_gap, title[:22])

    cover.setFont(COVER_FONT_NAME, 24)
    cover.drawString(left_margin, title_y - (line_gap * 2), paper_line[:24])

    cover.showPage()
    cover.save()
    packet.seek(0)
    return packet


def download_paper(args):
    subject_code, session, year_suffix, paper_type_short, paper_no = args

    if paper_type_short == "gt":
        filename = f"{subject_code}_{session}{year_suffix}_gt.pdf"
    else:
        filename = f"{subject_code}_{session}{year_suffix}_{paper_type_short}_{paper_no}.pdf"

    url = f"https://pastpapers.papacambridge.com/directories/CAIE/CAIE-pastpapers/upload/{filename}"

    try:
        response = requests.get(url, timeout=8)
        if response.status_code == 200 and response.content.startswith(b"%PDF"):
            return paper_no, filename, BytesIO(response.content)
        return paper_no, filename, None
    except Exception:
        return paper_no, filename, None


def render_home_page():
    st.markdown(
        """
<div class="page-card">
<h3 style="margin-top:0;">Public General Paper Compiler</h3>
<p style="margin-bottom:0;">Download and merge CAIE papers with a public general cover design.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    st.write("")

    level_choice = st.radio("Select Level", ["IGCSE", "A Level"], horizontal=True)
    subjects = IGCSE_SUBJECTS if level_choice == "IGCSE" else ALEVEL_SUBJECTS
    subject_name = st.selectbox("Select Subject", sorted(subjects.keys()))
    subject_code = subjects[subject_name]

    st.info(f"Selected: **{subject_name}** | Code: `{subject_code}`")

    current_year = int(datetime.now().year)
    col1, col2 = st.columns(2)
    with col1:
        year_start = st.number_input("Start Year", 2002, current_year, current_year - 5)
    with col2:
        year_end = st.number_input("End Year", 2002, current_year, current_year)

    session_labels = list(SESSION_OPTIONS.keys())
    selected_session_labels = st.multiselect("Select Sessions", session_labels, default=session_labels)
    sessions = [SESSION_OPTIONS[label] for label in selected_session_labels]

    paper_type = st.selectbox("Paper Type", list(PAPER_TYPE_OPTIONS.keys()))
    paper_type_short = PAPER_TYPE_OPTIONS[paper_type]

    if paper_type_short != "gt":
        paper_input_raw = st.text_input("Enter Paper Numbers (example: 12 22 32)", "12 22 32 42")
    else:
        paper_input_raw = ""

    paper_input = format_papers(paper_input_raw)
    paper_numbers = [p.strip() for p in paper_input.split() if p.strip()]

    if st.button("Generate Public General Pack"):
        if paper_type_short != "gt" and not paper_numbers:
            st.error("Please enter at least one paper number.")
            return
        if not sessions:
            st.error("Please select at least one session.")
            return

        tasks = []
        for year in range(year_start, year_end + 1):
            year_suffix = str(year)[2:]
            for session in sessions:
                if paper_type_short == "gt":
                    tasks.append((subject_code, session, year_suffix, paper_type_short, None))
                else:
                    for paper_no in paper_numbers:
                        tasks.append((subject_code, session, year_suffix, paper_type_short, paper_no))

        downloaded_by_number = {num: [] for num in paper_numbers}
        gt_downloads = []
        downloaded, failed = [], []

        st.write("### Download Progress")
        status_placeholder = st.empty()
        progress = st.progress(0)

        total_tasks = len(tasks)
        completed = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(download_paper, task): task for task in tasks}
            for future in concurrent.futures.as_completed(futures):
                paper_no, filename, content = future.result()

                if content:
                    content.seek(0)
                    if paper_type_short == "gt":
                        gt_downloads.append(content)
                    else:
                        downloaded_by_number[paper_no].append(content)
                    downloaded.append(filename)
                else:
                    failed.append(filename)

                completed += 1
                progress.progress(completed / total_tasks)
                status_placeholder.caption(f"Processed {completed}/{total_tasks} files")

        if not downloaded:
            st.warning("No valid PDFs were downloaded, so no merged files were created.")
            return

        output_zip = BytesIO()
        with zipfile.ZipFile(output_zip, "w") as zf:
            if paper_type_short == "gt":
                merger = PdfMerger()
                merger.append(create_public_cover_pdf(level_choice, subject_name, subject_code, paper_type_short, None))
                for pdf in gt_downloads:
                    pdf.seek(0)
                    merger.append(pdf)
                if os.path.exists(DEFAULT_END_PAGE_PATH):
                    merger.append(DEFAULT_END_PAGE_PATH)

                merged_pdf = BytesIO()
                merger.write(merged_pdf)
                merger.close()
                merged_pdf.seek(0)
                zf.writestr(
                    f"{level_choice}_{subject_code}_Grade_Thresholds_Public_General.pdf",
                    merged_pdf.getvalue(),
                )
            else:
                for num in paper_numbers:
                    pdf_list = downloaded_by_number.get(num, [])
                    if not pdf_list:
                        continue

                    merger = PdfMerger()
                    merger.append(
                        create_public_cover_pdf(level_choice, subject_name, subject_code, paper_type_short, num)
                    )
                    for pdf in pdf_list:
                        pdf.seek(0)
                        merger.append(pdf)
                    if os.path.exists(DEFAULT_END_PAGE_PATH):
                        merger.append(DEFAULT_END_PAGE_PATH)

                    merged_pdf = BytesIO()
                    merger.write(merged_pdf)
                    merger.close()
                    merged_pdf.seek(0)
                    zf.writestr(
                        f"{level_choice}_{subject_code}_Paper_{num}_Public_General.pdf",
                        merged_pdf.getvalue(),
                    )

        output_zip.seek(0)
        update_data_log(
            level_choice,
            subject_name,
            subject_code,
            len(paper_numbers) if paper_type_short != "gt" else 1,
            len(downloaded),
            len(failed),
        )

        st.success(f"Downloaded {len(downloaded)} papers. {len(failed)} failed.")
        st.download_button(
            "Download Public General ZIP",
            output_zip.getvalue(),
            file_name=f"{level_choice}_{subject_code}_public_general_pack.zip",
            mime="application/zip",
        )


def render_about_page():
    st.markdown(
        """
<div class="page-card">
<h3 style="margin-top:0;">About The Public General Version</h3>
<p>This version is designed for general public use with a clean branded cover style and a simpler interface.</p>
<ul>
  <li>Friendly session labels for teachers</li>
  <li>Public general cover generation</li>
  <li>Automatic merging by paper number</li>
  <li>ZIP export of final merged files</li>
</ul>
</div>
""",
        unsafe_allow_html=True,
    )


def render_request_page():
    st.markdown(
        """
<div class="page-card">
<h3 style="margin-top:0;">Request A Free Custom School Past Paper Merger</h3>
<p>If your school wants a custom branded past paper merger, you can submit a request here for free.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    st.write("")

    with st.form("school_request_form"):
        school_name = st.text_input("School Name")
        contact_name = st.text_input("Contact Person")
        contact_email = st.text_input("Contact Email")
        country = st.text_input("Country")
        notes = st.text_area("What would you like in your custom school version?")
        submitted = st.form_submit_button("Submit Request")

    if submitted:
        if not school_name or not contact_name or not contact_email:
            st.error("Please fill in the school name, contact person, and contact email.")
            return

        save_school_request(
            {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "school_name": school_name,
                "contact_name": contact_name,
                "contact_email": contact_email,
                "country": country,
                "notes": notes,
            }
        )
        st.success("Your request has been saved. Thank you.")


page = st.radio("", ["Main Page", "About", "Request Custom School Version"], horizontal=True)

if page == "Main Page":
    render_home_page()
elif page == "About":
    render_about_page()
else:
    render_request_page()


st.markdown(
    """
<hr style="margin-top: 50px; border: none; height: 1px; background-color: #333;">
<div style='text-align: center; font-size: 0.8rem; color: #888; padding-bottom: 20px;'>
© 2026 PaperPort Public General Version. All rights reserved. <br> Created by Fernando Gabriel Morera.
</div>
""",
    unsafe_allow_html=True,
)
