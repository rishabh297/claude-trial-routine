#!/usr/bin/env python3
"""
Clinical Trials Daily Monitor

Fetches newly posted clinical trials from ClinicalTrials.gov (API v2)
and sends a summary email via SMTP.

Usage:
    python check_new_trials.py              # checks yesterday's new trials
    python check_new_trials.py 2026-04-10   # checks a specific date
"""

import os
import sys
import time
import smtplib
import logging
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
API_BASE_URL = "https://clinicaltrials.gov/api/v2/studies"
PAGE_SIZE = 1000  # API maximum
RATE_LIMIT_DELAY = 1.0  # seconds between paginated requests
MAX_RETRIES = 4

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", "")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL", "rishabh@bondtrials.com")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_config():
    missing = []
    for var in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SENDER_EMAIL"):
        if not os.environ.get(var):
            missing.append(var)
    if missing:
        log.error("Missing required environment variables: %s", ", ".join(missing))
        log.error("Copy .env.example to .env and fill in your SMTP credentials.")
        sys.exit(1)


# ---------------------------------------------------------------------------
# ClinicalTrials.gov API
# ---------------------------------------------------------------------------

FIELDS = "|".join([
    "NCTId",
    "BriefTitle",
    "OfficialTitle",
    "OverallStatus",
    "Condition",
    "Phase",
    "StudyType",
    "LeadSponsorName",
    "StartDate",
    "StudyFirstPostDate",
    "BriefSummary",
    "EnrollmentInfo",
])


SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "ClinicalTrialsMonitor/1.0 (bondtrials.com)"})


def _api_get(params: dict) -> dict:
    """GET with retry + exponential back-off."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = SESSION.get(API_BASE_URL, params=params, timeout=30)
            if resp.status_code == 429:
                wait = 2 ** attempt
                log.warning("Rate-limited (429). Retrying in %ds...", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt == MAX_RETRIES:
                raise
            wait = 2 ** attempt
            log.warning("Request failed (%s). Retrying in %ds...", exc, wait)
            time.sleep(wait)
    return {}


def fetch_new_trials(date_str: str) -> list[dict]:
    """Return every study first-posted on *date_str* (YYYY-MM-DD)."""
    studies: list[dict] = []
    page_token = None

    while True:
        params = {
            "format": "json",
            "query.term": f"AREA[StudyFirstPostDate]RANGE[{date_str},{date_str}]",
            "fields": FIELDS,
            "pageSize": PAGE_SIZE,
            "countTotal": "true",
            "sort": "StudyFirstPostDate:desc",
        }
        if page_token:
            params["pageToken"] = page_token

        data = _api_get(params)
        batch = data.get("studies", [])
        studies.extend(batch)

        total = data.get("totalCount", len(studies))
        log.info("Fetched %d / %d studies so far", len(studies), total)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

        time.sleep(RATE_LIMIT_DELAY)

    return studies


# ---------------------------------------------------------------------------
# Data extraction
# ---------------------------------------------------------------------------

def extract_trial(study: dict) -> dict:
    proto = study.get("protocolSection", {})
    ident = proto.get("identificationModule", {})
    status = proto.get("statusModule", {})
    conds = proto.get("conditionsModule", {})
    design = proto.get("designModule", {})
    sponsor = proto.get("sponsorCollaboratorsModule", {})
    desc = proto.get("descriptionModule", {})

    nct_id = ident.get("nctId", "N/A")
    phases = design.get("phases", [])
    enrollment = design.get("enrollmentInfo", {})

    return {
        "nct_id": nct_id,
        "title": ident.get("briefTitle", "N/A"),
        "official_title": ident.get("officialTitle", ""),
        "status": status.get("overallStatus", "N/A"),
        "conditions": ", ".join(conds.get("conditions", [])) or "N/A",
        "phases": ", ".join(phases) if phases else "N/A",
        "study_type": design.get("studyType", "N/A"),
        "sponsor": sponsor.get("leadSponsor", {}).get("name", "N/A"),
        "enrollment": enrollment.get("count", "N/A"),
        "summary": (desc.get("briefSummary", "") or "")[:300],
        "posted_date": status.get("studyFirstPostDateStruct", {}).get("date", "N/A"),
        "url": f"https://clinicaltrials.gov/study/{nct_id}",
    }


# ---------------------------------------------------------------------------
# Email building
# ---------------------------------------------------------------------------

def _html_email(trials: list[dict], date_str: str) -> str:
    if not trials:
        return (
            f"<html><body>"
            f"<h2>Clinical Trials Daily Report &mdash; {date_str}</h2>"
            f"<p>No new clinical trials were posted on {date_str}.</p>"
            f"</body></html>"
        )

    rows = ""
    for i, t in enumerate(trials, 1):
        bg = "#f9f9f9" if i % 2 == 0 else "#ffffff"
        rows += (
            f'<tr style="background:{bg}; border-bottom:1px solid #e0e0e0;">'
            f'<td style="padding:8px; text-align:center;">{i}</td>'
            f'<td style="padding:8px;"><a href="{t["url"]}">{t["nct_id"]}</a></td>'
            f'<td style="padding:8px;">{t["title"]}</td>'
            f'<td style="padding:8px;">{t["status"]}</td>'
            f'<td style="padding:8px;">{t["conditions"]}</td>'
            f'<td style="padding:8px;">{t["phases"]}</td>'
            f'<td style="padding:8px;">{t["study_type"]}</td>'
            f'<td style="padding:8px;">{t["sponsor"]}</td>'
            f"</tr>\n"
        )

    return f"""\
<html>
<body style="font-family:Arial,Helvetica,sans-serif; color:#333; margin:0; padding:20px;">
  <h2 style="color:#2c3e50;">Clinical Trials Daily Report &mdash; {date_str}</h2>
  <p><strong>{len(trials)}</strong> new trial(s) posted on
     <a href="https://clinicaltrials.gov">ClinicalTrials.gov</a>.</p>

  <table style="border-collapse:collapse; width:100%; font-size:13px; margin-top:16px;">
    <thead>
      <tr style="background:#2c3e50; color:#fff;">
        <th style="padding:10px; text-align:left;">#</th>
        <th style="padding:10px; text-align:left;">NCT&nbsp;ID</th>
        <th style="padding:10px; text-align:left;">Title</th>
        <th style="padding:10px; text-align:left;">Status</th>
        <th style="padding:10px; text-align:left;">Conditions</th>
        <th style="padding:10px; text-align:left;">Phase</th>
        <th style="padding:10px; text-align:left;">Type</th>
        <th style="padding:10px; text-align:left;">Sponsor</th>
      </tr>
    </thead>
    <tbody>
      {rows}
    </tbody>
  </table>

  <hr style="margin-top:30px; border:none; border-top:1px solid #ddd;">
  <p style="font-size:11px; color:#999;">
    Source: <a href="https://clinicaltrials.gov">ClinicalTrials.gov</a> &bull;
    Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
  </p>
</body>
</html>"""


def _plain_email(trials: list[dict], date_str: str) -> str:
    if not trials:
        return f"Clinical Trials Daily Report - {date_str}\n\nNo new trials posted.\n"

    lines = [
        f"Clinical Trials Daily Report - {date_str}",
        f"{len(trials)} new trial(s) posted on ClinicalTrials.gov.\n",
        "=" * 80,
    ]
    for i, t in enumerate(trials, 1):
        lines.append(f"\n{i}. [{t['nct_id']}] {t['title']}")
        lines.append(f"   Status:     {t['status']}")
        lines.append(f"   Conditions: {t['conditions']}")
        lines.append(f"   Phase:      {t['phases']}  |  Type: {t['study_type']}")
        lines.append(f"   Sponsor:    {t['sponsor']}")
        lines.append(f"   Enrollment: {t['enrollment']}")
        lines.append(f"   Link:       {t['url']}")
        lines.append("-" * 80)

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Email sending
# ---------------------------------------------------------------------------

def send_email(subject: str, html_body: str, plain_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECIPIENT_EMAIL

    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, [RECIPIENT_EMAIL], msg.as_string())

    log.info("Email sent to %s", RECIPIENT_EMAIL)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    validate_config()

    # Determine target date (default: yesterday UTC)
    if len(sys.argv) > 1:
        target_date = sys.argv[1]
    else:
        target_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")

    log.info("Checking for trials posted on %s ...", target_date)

    studies = fetch_new_trials(target_date)
    log.info("Total new trials found: %d", len(studies))

    trials = [extract_trial(s) for s in studies]

    subject = f"New Clinical Trials - {target_date} ({len(trials)} trial{'s' if len(trials) != 1 else ''})"
    html = _html_email(trials, target_date)
    plain = _plain_email(trials, target_date)

    send_email(subject, html, plain)
    log.info("Done.")


if __name__ == "__main__":
    main()
