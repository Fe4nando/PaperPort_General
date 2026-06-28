import concurrent.futures
import json
import os
import re
import smtplib
import zipfile
from datetime import datetime
from email.message import EmailMessage
from io import BytesIO

import requests
import streamlit as st
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


st.set_page_config(page_title="GMAK Paper Port", layout="wide")

LEVELS = st.secrets["LEVELS"]
DOWNLOAD_DIR = st.secrets["DOWNLOAD_DIR"]
HEADERS = json.loads(st.secrets["HEADERS"])
SESSIONS_ALL = st.secrets["SESSIONS_ALL"]
ACCESS_STUDENT_ID_PREFIX = str(st.secrets.get("ACCESS_STUDENT_ID_PREFIX", "")).strip()
ACCESS_TEACHER_EMAIL_DOMAINS = tuple(
    str(domain).strip().lower()
    for domain in st.secrets.get("ACCESS_TEACHER_EMAIL_DOMAINS", [])
    if str(domain).strip()
)

IGCSE_SUBJECTS = json.loads(st.secrets["IGCSE_SUBJECTS"])
ALEVEL_SUBJECTS = json.loads(st.secrets["ALEVEL_SUBJECTS"])

DATA_FILE = "data.json"
REQUESTS_FILE = "custom_school_requests.json"
DEFAULT_FONT_PATH = "Poppins-Bold.ttf"
GENERAL_COVER_PATH = "template_base.png"

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
.stApp {
    background: #ffffff;
    color: #000000;
}
.stButton > button, .stDownloadButton > button {
    background: #163a8c;
    color: #ffffff;
    border: 1px solid #163a8c;
    border-radius: 12px;
    font-weight: 600;
}
.stButton > button:hover, .stDownloadButton > button:hover {
    background: #102d6f;
    border-color: #102d6f;
    color: #ffffff;
}
.page-card {
    background: #ffffff;
    border: 1px solid #d7deed;
    border-radius: 18px;
    padding: 22px;
}
.download-card {
    background: #f4f7ff;
    border: 1px solid #c7d5fb;
    border-radius: 16px;
    padding: 18px;
}
</style>
""",
    unsafe_allow_html=True,
)


def ensure_json_file(path, default_content):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default_content, f, indent=4)


ensure_json_file(DATA_FILE, {"total_downloads": 0, "logs": []})
ensure_json_file(REQUESTS_FILE, {"requests": []})

if "public_general_zip_bytes" not in st.session_state:
    st.session_state["public_general_zip_bytes"] = None
if "public_general_zip_name" not in st.session_state:
    st.session_state["public_general_zip_name"] = None
if "startup_popup_seen" not in st.session_state:
    st.session_state["startup_popup_seen"] = False
if "access_verification_value" not in st.session_state:
    st.session_state["access_verification_value"] = ""


def register_cover_font():
    if os.path.exists(DEFAULT_FONT_PATH):
        try:
            pdfmetrics.registerFont(TTFont("PoppinsBoldPublic", DEFAULT_FONT_PATH))
            return "PoppinsBoldPublic"
        except Exception:
            pass
    return "Helvetica-Bold"


COVER_FONT_NAME = register_cover_font()


@st.dialog("Welcome to GMAK Paper Port")
def show_startup_popup():
    st.markdown(
        """
**You are accessing GMAK Paper Port.**

To proceed, verification is required.

Type your ID card number if you are a student, or your email address if you are a teacher.
"""
    )

    verification_value = st.text_input(
        "Student ID card number or teacher email",
        value=st.session_state["access_verification_value"],
        placeholder="Enter ID card number or email",
    )
    st.session_state["access_verification_value"] = verification_value

    if st.button("Verify School Access", use_container_width=True):
        cleaned_value = verification_value.strip()

        if not cleaned_value:
            st.error("Please enter your ID card number or teacher email to continue.")
            return

        if not ACCESS_STUDENT_ID_PREFIX or not ACCESS_TEACHER_EMAIL_DOMAINS:
            st.error(
                "Access verification is not configured. Please add ACCESS_STUDENT_ID_PREFIX "
                "and ACCESS_TEACHER_EMAIL_DOMAINS to Streamlit secrets."
            )
            return

        normalized_value = cleaned_value.lower()

        if "@" in normalized_value:
            if not normalized_value.endswith(ACCESS_TEACHER_EMAIL_DOMAINS):
                allowed_domains_text = " or ".join(ACCESS_TEACHER_EMAIL_DOMAINS)
                st.error(f"Teacher email must end with {allowed_domains_text}.")
                return
        else:
            student_digits = re.sub(r"\D", "", cleaned_value)
            required_digits = len(ACCESS_STUDENT_ID_PREFIX)
            if len(student_digits) < required_digits:
                st.error(f"Student ID must include at least the first {required_digits} digits.")
                return
            if student_digits[:required_digits] != ACCESS_STUDENT_ID_PREFIX:
                st.error(
                    f"Student ID must begin with {ACCESS_STUDENT_ID_PREFIX} in the first "
                    f"{required_digits} digits."
                )
                return
            cleaned_value = student_digits[:required_digits]

        st.session_state["access_verification_value"] = cleaned_value
        st.session_state["startup_popup_seen"] = True
        st.rerun()

    st.link_button(
        "Use the Public PaperPort Website",
        "https://paperport.streamlit.app/",
        use_container_width=True,
    )


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


def format_papers(text):
    cleaned = re.sub(r"\D", "", text)
    groups = [cleaned[i: i + 2] for i in range(0, len(cleaned), 2)]
    return " ".join([g for g in groups if g])


def _bestexamhelp_url(subject_code, year_suffix, filename):
    if subject_code in ALEVEL_SUBJECTS.values():
        level = "cambridge-international-a-level"
        subject_name = next(
            k for k, v in ALEVEL_SUBJECTS.items()
            if v == subject_code
        )
    elif subject_code in IGCSE_SUBJECTS.values():
        level = "cambridge-igcse"
        subject_name = next(
            k for k, v in IGCSE_SUBJECTS.items()
            if v == subject_code
        )
    else:
        return None

    slug = (
        subject_name.lower()
        .replace("&", "and")
        .replace("(9-1)", "")
        .replace("(", "")
        .replace(")", "")
        .replace("/", "-")
        .replace(" ", "-")
        .strip("-")
    )
    return (
        f"https://bestexamhelp.com/exam/"
        f"{level}/"
        f"{slug}-{subject_code}/"
        f"20{int(year_suffix):02d}/"
        f"{filename}"
    )


def _papacambridge_url(filename):
    return (
        "https://pastpapers.papacambridge.com/download_file.php"
        "?files=https://pastpapers.papacambridge.com/directories/"
        f"CAIE/CAIE-pastpapers/upload/{filename}"
    )


def try_download(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15,
            allow_redirects=True,
        )
        if response.status_code != 200:
            return None
        content = response.content
        if b"%PDF" not in content[:1024]:
            return None
        return BytesIO(content)
    except Exception:
        return None


def download_paper(args):
    subject_code, session, year_suffix, paper_type_short, paper_no = args

    if paper_type_short == "gt":
        filename = f"{subject_code}_{session}{year_suffix}_gt.pdf"
    else:
        filename = (
            f"{subject_code}_{session}{year_suffix}_"
            f"{paper_type_short}_{paper_no}.pdf"
        )

    url = _bestexamhelp_url(subject_code, year_suffix, filename)
    if not url:
        return paper_no, filename, subject_code, None

    pdf = try_download(url)
    if pdf:
        return paper_no, filename, subject_code, pdf

    fallback_url = _papacambridge_url(filename)
    pdf = try_download(fallback_url)
    if pdf:
        return paper_no, filename, subject_code, pdf

    return paper_no, filename, subject_code, None


def render_home_page():
    logo_col, _ = st.columns([1, 5])
    with logo_col:
        if os.path.exists("logo.png"):
            st.image("logo.png", width=140)

    st.markdown(
        """
        <div style="
            background:#fff3cd;
            border:1px solid #ffe69c;
            color:#664d03;
            padding:15px;
            border-radius:12px;
            margin-bottom:20px;
            font-weight:600;
        ">
        ⚠️ Temporary Outage Notice: Past papers from 2010–2026 are currently the only papers available while our paper providers undergo maintenance.
        </div>
        """,
        unsafe_allow_html=True,
    )
    st.markdown(
        """
<div class="page-card">
<h3 style="margin-top:0;">GMAK Paper Port</h3>
<p style="margin-bottom:0;">Download CAIE papers for GMAK in one place. Each paper is saved as an individual PDF inside a ZIP.</p>
</div>
""",
        unsafe_allow_html=True,
    )
    st.write("")

    # --- Level & multi-subject selection ---
    level_choice = st.radio("Select Level", ["IGCSE", "A Level"], horizontal=True)
    subjects = IGCSE_SUBJECTS if level_choice == "IGCSE" else ALEVEL_SUBJECTS
    sorted_subject_names = sorted(subjects.keys())

    selected_subject_names = st.multiselect(
        "Select Subjects (one or more)",
        sorted_subject_names,
        placeholder="Choose subjects…",
    )

    if selected_subject_names:
        codes_preview = ", ".join(
            f"`{subjects[s]}`" for s in selected_subject_names
        )
        st.info(f"Selected {len(selected_subject_names)} subject(s) — codes: {codes_preview}")

    # --- Shared settings ---
    current_year = int(datetime.now().year)
    col1, col2 = st.columns(2)
    with col1:
        year_start = st.number_input("Start Year", 2002, current_year, current_year - 5)
    with col2:
        year_end = st.number_input("End Year", 2002, current_year, current_year)

    session_labels = list(SESSION_OPTIONS.keys())
    selected_session_labels = st.multiselect(
        "Select Sessions", session_labels, default=session_labels
    )
    sessions = [SESSION_OPTIONS[label] for label in selected_session_labels]

    paper_type = st.selectbox("Paper Type", list(PAPER_TYPE_OPTIONS.keys()))
    paper_type_short = PAPER_TYPE_OPTIONS[paper_type]

    if paper_type_short != "gt":
        paper_input_raw = st.text_input(
            "Enter Paper Numbers (example: 12 22 32)", "12 22 32 42"
        )
    else:
        paper_input_raw = ""

    paper_input = format_papers(paper_input_raw)
    paper_numbers = [p.strip() for p in paper_input.split() if p.strip()]

    if st.button("Generate GMAK Paper Pack"):
        st.session_state["public_general_zip_bytes"] = None
        st.session_state["public_general_zip_name"] = None

        if not selected_subject_names:
            st.error("Please select at least one subject.")
            return
        if paper_type_short != "gt" and not paper_numbers:
            st.error("Please enter at least one paper number.")
            return
        if not sessions:
            st.error("Please select at least one session.")
            return

        # Build all tasks across every selected subject
        tasks = []
        for subject_name in selected_subject_names:
            subject_code = subjects[subject_name]
            for year in range(int(year_start), int(year_end) + 1):
                year_suffix = str(year)[2:]
                for session in sessions:
                    if paper_type_short == "gt":
                        tasks.append(
                            (subject_code, session, year_suffix, paper_type_short, None)
                        )
                    else:
                        for paper_no in paper_numbers:
                            tasks.append(
                                (subject_code, session, year_suffix, paper_type_short, paper_no)
                            )

        downloaded, failed = [], []
        # Map filename -> pdf bytes, preserving subject folder structure
        pdf_files: dict[str, BytesIO] = {}

        st.write("### Download Progress")
        status_placeholder = st.empty()
        progress_bar = st.progress(0)

        total_tasks = len(tasks)
        completed = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(download_paper, task): task for task in tasks}
            for future in concurrent.futures.as_completed(futures):
                paper_no, filename, subject_code, content = future.result()

                # Resolve subject name for folder labelling
                all_subjects = {**IGCSE_SUBJECTS, **ALEVEL_SUBJECTS}
                subject_name_for_folder = next(
                    (k for k, v in all_subjects.items() if v == subject_code),
                    subject_code,
                )
                # Sanitise folder name
                safe_folder = re.sub(r'[\\/*?:"<>|]', "_", subject_name_for_folder)
                zip_path = f"{safe_folder}/{filename}"

                if content:
                    content.seek(0)
                    pdf_files[zip_path] = content
                    downloaded.append(filename)
                else:
                    failed.append(filename)

                completed += 1
                progress_bar.progress(completed / total_tasks)
                status_placeholder.caption(
                    f"Processed {completed}/{total_tasks} files"
                )

        if not pdf_files:
            st.warning("No valid PDFs were downloaded.")
            return

        # Pack every individual PDF into the ZIP (no merging)
        output_zip = BytesIO()
        with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for zip_path, pdf_bytes in pdf_files.items():
                pdf_bytes.seek(0)
                zf.writestr(zip_path, pdf_bytes.read())

        output_zip.seek(0)

        # Log once per subject
        for subject_name in selected_subject_names:
            subject_code = subjects[subject_name]
            subj_downloaded = sum(
                1 for p in downloaded
                if p.startswith(subject_code)
            )
            subj_failed = sum(
                1 for p in failed
                if p.startswith(subject_code)
            )
            update_data_log(
                level_choice,
                subject_name,
                subject_code,
                len(paper_numbers) if paper_type_short != "gt" else 1,
                subj_downloaded,
                subj_failed,
            )

        subject_tag = (
            selected_subject_names[0].replace(" ", "_")
            if len(selected_subject_names) == 1
            else f"{len(selected_subject_names)}_subjects"
        )
        zip_name = f"{level_choice}_{subject_tag}_gmak_paper_pack.zip"

        st.session_state["public_general_zip_bytes"] = output_zip.getvalue()
        st.session_state["public_general_zip_name"] = zip_name

        st.success(
            f"✅ {len(downloaded)} paper(s) downloaded across "
            f"{len(selected_subject_names)} subject(s). "
            f"{len(failed)} failed."
        )

    if st.session_state["public_general_zip_bytes"]:
        st.write("")
        st.markdown(
            """
<div class="download-card">
<strong>Your ZIP is ready.</strong><br>
Each PDF is saved individually inside a folder per subject. Use the button below to download.
</div>
""",
            unsafe_allow_html=True,
        )
        st.download_button(
            "⬇️ Download GMAK Paper Pack",
            st.session_state["public_general_zip_bytes"],
            file_name=st.session_state["public_general_zip_name"],
            mime="application/zip",
            use_container_width=True,
            key="public_general_zip_download",
        )


if not st.session_state["startup_popup_seen"]:
    show_startup_popup()

render_home_page()

st.markdown(
    """
<hr style="margin-top: 50px; border: none; height: 1px; background-color: #333;">
<div style='text-align: center; font-size: 0.8rem; color: #888; padding-bottom: 20px;'>
&copy; 2026 GMAK Paper Port. All rights reserved. <br> Created by Fernando Gabriel Morera.
</div>
""",
    unsafe_allow_html=True,
)
