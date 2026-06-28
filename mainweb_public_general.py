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
from reportlab.lib.colors import white
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


st.set_page_config(page_title="PaperPort Public", layout="wide")

LEVELS = st.secrets["LEVELS"]
DOWNLOAD_DIR = st.secrets["DOWNLOAD_DIR"]
SESSIONS_ALL = st.secrets["SESSIONS_ALL"]

IGCSE_SUBJECTS = json.loads(st.secrets["IGCSE_SUBJECTS"])
ALEVEL_SUBJECTS = json.loads(st.secrets["ALEVEL_SUBJECTS"])

DATA_FILE = "data.json"
REQUESTS_FILE = "custom_school_requests.json"
DEFAULT_FONT_PATH = "Poppins-Bold.ttf"
GENERAL_COVER_PATH = "GENERAL_COVER.png"

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

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/137.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

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
    ⚠️ 2026 Papers are not available in the repos - please check other resources (6/7/26 Resource Update).
    </div>
    """,
    unsafe_allow_html=True,
)

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


def send_school_request_notification(payload):
    required_keys = [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "NOTIFICATION_EMAIL_TO",
    ]
    missing_keys = [key for key in required_keys if key not in st.secrets]
    if missing_keys:
        return False, f"Missing email secrets: {', '.join(missing_keys)}"

    smtp_host = st.secrets["SMTP_HOST"]
    smtp_port = int(st.secrets["SMTP_PORT"])
    smtp_username = st.secrets["SMTP_USERNAME"]
    smtp_password = st.secrets["SMTP_PASSWORD"]
    notification_to = st.secrets["NOTIFICATION_EMAIL_TO"]
    notification_from = st.secrets.get("NOTIFICATION_EMAIL_FROM", smtp_username)
    use_tls = str(st.secrets.get("SMTP_USE_TLS", "true")).lower() == "true"

    message = EmailMessage()
    message["Subject"] = f"New PaperPort school request: {payload['school_name']}"
    message["From"] = notification_from
    message["To"] = notification_to
    message.set_content(
        "\n".join(
            [
                "A new custom school merger request was submitted.",
                "",
                f"Timestamp: {payload['timestamp']}",
                f"School Name: {payload['school_name']}",
                f"Contact Person: {payload['contact_name']}",
                f"Contact Email: {payload['contact_email']}",
                f"Country: {payload['country']}",
                "",
                "Notes:",
                payload["notes"] or "(No extra notes provided)",
            ]
        )
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        if use_tls:
            server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(message)

    return True, None


def send_requester_confirmation_email(payload):
    required_keys = [
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
    ]
    missing_keys = [key for key in required_keys if key not in st.secrets]
    if missing_keys:
        return False, f"Missing email secrets: {', '.join(missing_keys)}"

    smtp_host = st.secrets["SMTP_HOST"]
    smtp_port = int(st.secrets["SMTP_PORT"])
    smtp_username = st.secrets["SMTP_USERNAME"]
    smtp_password = st.secrets["SMTP_PASSWORD"]
    notification_from = st.secrets.get("NOTIFICATION_EMAIL_FROM", smtp_username)
    use_tls = str(st.secrets.get("SMTP_USE_TLS", "true")).lower() == "true"

    message = EmailMessage()
    message["Subject"] = "PaperPort request received"
    message["From"] = notification_from
    message["To"] = payload["contact_email"]
    message.set_content(
        "\n".join(
            [
                f"Hello {payload['contact_name']},",
                "",
                "Thank you for submitting a request for a custom school past paper merger through PaperPort.",
                "We have received your request successfully and will contact you shortly.",
                "",
                "Please note that this service is provided free of cost for schools.",
                "",
                "This email was generated automatically to confirm that your request has been received.",
                "You do not need to reply unless you want to add more information.",
                "",
                "Best regards,",
                "Fernando Gabriel Morera",
                "PaperPort",
            ]
        )
    )

    with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
        if use_tls:
            server.starttls()
        server.login(smtp_username, smtp_password)
        server.send_message(message)

    return True, None


def format_papers(text):
    cleaned = re.sub(r"\D", "", text)
    groups = [cleaned[i: i + 2] for i in range(0, len(cleaned), 2)]
    return " ".join([g for g in groups if g])


def try_download(url, source="unknown"):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=15,
            allow_redirects=True,
        )
        if response.status_code != 200:
            print(f"[{source}] Failed (HTTP {response.status_code}): {url}")
            return None
        content = response.content
        if b"%PDF" not in content[:1024]:
            print(f"[{source}] Not a PDF: {url}")
            return None
        print(f"[{source}] Success: {url}")
        return BytesIO(content)
    except Exception as e:
        print(f"[{source}] Exception: {url} — {e}")
        return None


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
        print(f"Could not generate URL for {subject_code}")
        return paper_no, filename, subject_code, None

    pdf = try_download(url, source="BestExamHelp")
    if pdf:
        return paper_no, filename, subject_code, pdf

    print(f"[BestExamHelp] Falling back to PapaCambridge for: {filename}")
    fallback_url = _papacambridge_url(filename)
    pdf = try_download(fallback_url, source="PapaCambridge")
    if pdf:
        return paper_no, filename, subject_code, pdf

    print(f"[All Sources] Failed: {filename}")
    return paper_no, filename, subject_code, None


def render_home_page():
    st.markdown(
        """
<div class="page-card">
<h3 style="margin-top:0;">Public General Paper Compiler</h3>
<p style="margin-bottom:0;">Download CAIE papers with individual PDFs per paper, packed into a ZIP. Supports multiple subjects at once.</p>
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
        codes_preview = ", ".join(f"`{subjects[s]}`" for s in selected_subject_names)
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

    if st.button("Generate Public General Pack"):
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
        # zip_path -> BytesIO, one entry per individual PDF
        pdf_files: dict = {}

        st.write("### Download Progress")
        status_placeholder = st.empty()
        progress_bar = st.progress(0)

        total_tasks = len(tasks)
        completed = 0

        all_subjects_map = {**IGCSE_SUBJECTS, **ALEVEL_SUBJECTS}

        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            futures = {executor.submit(download_paper, task): task for task in tasks}
            for future in concurrent.futures.as_completed(futures):
                paper_no, filename, subject_code, content = future.result()

                subject_name_for_folder = next(
                    (k for k, v in all_subjects_map.items() if v == subject_code),
                    subject_code,
                )
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

        # Pack every individual PDF into the ZIP — no merging
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
                1 for p in downloaded if p.startswith(subject_code)
            )
            subj_failed = sum(
                1 for p in failed if p.startswith(subject_code)
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
        zip_name = f"{level_choice}_{subject_tag}_public_general_pack.zip"

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
            "⬇️ Download Public General ZIP",
            st.session_state["public_general_zip_bytes"],
            file_name=st.session_state["public_general_zip_name"],
            mime="application/zip",
            use_container_width=True,
            key="public_general_zip_download",
        )


def render_about_page():
    st.markdown(
        """
<div class="page-card">
<h3 style="margin-top:0;">About The Public General Version</h3>
<p>This version is designed for general public use with a simpler interface. Each paper is delivered as an individual PDF inside a per-subject folder in the ZIP.</p>
<ul>
  <li>Multi-subject selection with shared settings</li>
  <li>Individual PDFs — no merging</li>
  <li>Per-subject folder organisation inside the ZIP</li>
  <li>Automatic fallback between paper sources</li>
</ul>
</div>
""",
        unsafe_allow_html=True,
    )


def render_request_page():
    st.markdown(
        """
<div class="page-card">
<h3 style="margin-top:0;">Request A Free Custom School Past Paper Pack</h3>
<p>If your school wants a custom branded past paper pack, you can submit a request here for free.</p>
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

        payload = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "school_name": school_name,
            "contact_name": contact_name,
            "contact_email": contact_email,
            "country": country,
            "notes": notes,
        }

        save_school_request(payload)

        try:
            email_sent, email_error = send_school_request_notification(payload)
        except Exception as exc:
            email_sent, email_error = False, str(exc)

        try:
            confirmation_sent, confirmation_error = send_requester_confirmation_email(payload)
        except Exception as exc:
            confirmation_sent, confirmation_error = False, str(exc)

        if email_sent and confirmation_sent:
            st.success("Your request has been saved, emailed to the admin, and confirmed to the requester.")
        elif email_sent:
            st.warning(
                f"Your request was saved and emailed to the admin, but the requester confirmation email was not sent. {confirmation_error}"
            )
        else:
            st.warning(f"Your request was saved, but email notification was not sent. {email_error}")


page = st.radio(
    "Navigation",
    ["Main Page", "About", "Request Custom School Version"],
    horizontal=True,
    label_visibility="collapsed",
)

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
