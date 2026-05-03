# 📄 Document Classification Service

> A production-grade Flask microservice for automated PDF document classification using OCR, fuzzy keyword matching, and real-time streaming — built for health insurance claim workflows.

---

## 📑 Table of Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Getting Started](#getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
  - [POST /classify](#post-classify)
  - [POST /stream\_classification](#post-stream_classification)
  - [POST /upload\_classify](#post-upload_classify)
  - [GET /health](#get-health)
  - [GET /](#get-)
- [Classification Logic](#classification-logic)
- [Database Schema](#database-schema)
- [Email Alerting](#email-alerting)
- [Error Handling](#error-handling)
- [Project Structure](#project-structure)
- [Running in Production](#running-in-production)
- [Known Limitations](#known-limitations)
- [Contributing](#contributing)
- [License](#license)

---

## Overview

The **Document Classification Service** is a RESTful Flask API that:

1. Accepts PDF documents via file path or direct upload.
2. Extracts text using **Tesseract OCR** (via `pdf2image` + `pytesseract`).
3. Classifies documents into predefined categories (e.g., Discharge Summary, PAN Card, Aadhaar Card, Final Bill) using **fuzzy keyword matching** and **regex patterns**.
4. Logs every classification request and result to a **PostgreSQL** database.
5. Sends **email alerts** on critical failures (file-not-found storms, request timeouts).
6. Streams results back to the caller in real time via **chunked JSON streaming**.

It is designed for use in **health insurance claim automation pipelines** where large volumes of scanned PDF documents need to be identified and routed correctly.

---

## Features

| Feature | Description |
|---|---|
| 🔍 OCR-based extraction | Converts PDF pages to images and runs Tesseract OCR |
| 🧠 Fuzzy classification | Uses RapidFuzz `partial_ratio` for tolerant keyword matching |
| ⚡ Parallel processing | `ThreadPoolExecutor` for concurrent multi-document requests |
| 📡 Streaming API | Real-time chunked JSON streaming endpoint |
| 🌐 Browser upload UI | HTML frontend for direct PDF upload and classification |
| 🗄️ PostgreSQL logging | Every request/response is persisted for audit and analytics |
| 📧 Email alerting | Automated SMTP alerts for file-not-found storms and timeouts |
| ⏱️ Timeout enforcement | Hard 57-second request timeout with graceful degradation |
| 🕐 IST timestamps | All logs and DB records use Asia/Kolkata timezone |
| 🩺 Health check endpoint | `/health` for load balancer and uptime monitoring integration |

---

## Architecture

```
Client (Browser / API Consumer)
         │
         ▼
  ┌──────────────────────┐
  │   Flask Application   │
  │  (app.py, port 8000)  │
  └──────┬───────────────┘
         │
   ┌─────┴──────────────────────────┐
   │                                │
   ▼                                ▼
ThreadPoolExecutor             /upload_classify
(parallel doc processing)      (single upload)
   │
   ▼
┌─────────────────────┐
│  PDF → Images (OCR) │  ← pdf2image + pytesseract
│  Text Extraction    │
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│  Keyword Matching   │  ← JSON keyword files + RapidFuzz
│  + Regex Patterns   │
└────────┬────────────┘
         │
    ┌────┴────┐
    ▼         ▼
PostgreSQL   SMTP Alert
(audit log)  (on errors)
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Web Framework | Flask |
| OCR Engine | Tesseract (`pytesseract`) |
| PDF → Image | `pdf2image` (Poppler backend) |
| Fuzzy Matching | `rapidfuzz` |
| Database | PostgreSQL (`psycopg2`) |
| Email | Python `smtplib` (Gmail SMTP) |
| Concurrency | `concurrent.futures.ThreadPoolExecutor` |
| Image Processing | Pillow (`PIL`) |
| Timezone | `pytz` (Asia/Kolkata) |
| Security | `werkzeug.utils.secure_filename` |

---

## Getting Started

### Prerequisites

- Python **3.9+**
- **Tesseract OCR** installed and accessible in system `PATH`
  - Ubuntu: `sudo apt install tesseract-ocr`
  - macOS: `brew install tesseract`
  - Windows: [Download installer](https://github.com/UB-Mannheim/tesseract/wiki)
- **Poppler** (required by `pdf2image`)
  - Ubuntu: `sudo apt install poppler-utils`
  - macOS: `brew install poppler`
  - Windows: [Poppler for Windows](https://github.com/oschwartz10612/poppler-windows)
- **PostgreSQL** database (local or remote)

---

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-org/document-classification-service.git
cd document-classification-service

# 2. Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Linux/macOS
venv\Scripts\activate           # Windows

# 3. Install Python dependencies
pip install -r requirements.txt

# 4. Copy and configure environment variables
cp .env.example .env
# Edit .env with your actual values (see below)

# 5. Create the database table (see Database Schema section)

# 6. Run the application
python app.py
```

The service will be available at `http://localhost:8000`.

---

### Environment Variables

Create a `.env` file in the project root (or set these in your container/deployment environment):

```env
# --- Application ---
UPLOAD_FOLDER=/tmp/pdf_uploads
STARTUP_DELAY=0

# --- File Paths ---
KEYWORD_DIR=/app/keywords
DOCUMENT_DIR=/opt/documents

# --- PostgreSQL ---
DB_HOST=localhost
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASSWORD=your_secure_password_here

# --- Email (Gmail SMTP) ---
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SENDER_EMAIL=your_service_account@gmail.com
SENDER_PASSWORD=your_app_password_here
```

> ⚠️ **Security Note:** Never commit real credentials to version control. Use a secrets manager (AWS Secrets Manager, Vault, etc.) in production environments.

---

## API Reference

### POST `/classify`

Classifies one or more PDF documents by file path. Processes documents in parallel (up to 4 workers).

**Request Body:**
```json
{
  "documents": [
    {
      "documentName": "claim_12345.pdf",
      "documentPath": "/opt/claims/claim_12345.pdf"
    },
    {
      "documentName": "discharge_summary.pdf",
      "documentPath": "/opt/claims/discharge_summary.pdf"
    }
  ]
}
```

**Success Response `200`:**
```json
{
  "results": [
    {
      "documentName": "claim_12345.pdf",
      "classification": ["Final Bill with Tax Invoice", "Discharge Summary"],
      "totalPages": 4
    }
  ],
  "errors": []
}
```

**Error Response (partial failure):**
```json
{
  "results": [...],
  "errors": [
    {
      "documentName": "discharge_summary.pdf",
      "error": "File not found: /opt/claims/discharge_summary.pdf"
    }
  ]
}
```

---

### POST `/stream_classification`

Same as `/classify` but streams results as they are processed using chunked JSON. Ideal for large document batches where the caller wants progressive results.

**Request Body:** Same as `/classify`

**Response:** `Content-Type: application/json` (streamed)

The response is built incrementally:
```
{"results":[<doc1_result>,<doc2_result>,...], "errors":[...]}
```

---

### POST `/upload_classify`

Accepts a direct PDF file upload from a browser form (multipart/form-data). Suitable for the built-in HTML UI.

**Request:** `multipart/form-data` with field `file` containing a `.pdf` file.

**Success Response `200`:**
```json
{
  "documentName": "invoice.pdf",
  "classification": ["Final Bill with Tax Invoice"],
  "totalPages": 2,
  "processedAt": "2025-07-15 14:32:10 IST"
}
```

**Error Responses:**

| Code | Reason |
|---|---|
| `400` | No file part, no file selected, or non-PDF file |
| `500` | Internal processing error |

---

### GET `/health`

Health check endpoint for load balancers, Kubernetes probes, and uptime monitors.

**Response `200`:**
```json
{
  "status": "ok",
  "timestamp": "2025-07-15 14:32:10 IST"
}
```

---

### GET `/`

Serves the built-in HTML frontend (`templates/index.html`) for browser-based PDF upload and classification.

---

## Classification Logic

Documents are classified using a multi-stage pipeline:

### Stage 1 — Regex Pattern Matching (always runs)

| Pattern | Classification |
|---|---|
| Aadhaar keyword + 12-digit UID pattern | `Aadhar Card copy of Primary Policy Holder` |
| PAN keyword + `AAAAA9999A` format | `PAN Card copy of Primary Policy Holder` |
| ≥ 6 occurrences of `ref`/`lot`/`sterile` | `Implant Stickers` |

### Stage 2 — Keyword File Selection (page-count adaptive)

| Page Count | Keyword File |
|---|---|
| 1 page | `one_two_page_keywords.json` |
| 2–10 pages | `below_10_keywords.json` |
| > 10 pages | `above_10_keywords.json` |

### Stage 3 — Fuzzy Keyword Matching

- Uses `RapidFuzz partial_ratio` with a **90% threshold**
- For 1-page documents: matches per page, requires N keyword hits per doc-type
- For multi-page documents: matches against full concatenated text

### Stage 4 — Inference Rules

If neither Aadhaar nor PAN was matched directly, but 2+ high-confidence document types are present (e.g., Member ID Card + Policy Copy + Discharge Summary + Final Bill), the service infers the KYC documents are likely present and adds them automatically.

### Required Keyword Counts per Document Type

| Document Type | Required Matches |
|---|---|
| Discharge Summary, Final Bill | 3 |
| Investigation reports, KYC forms | 2 |
| All others | 1 |

---

## Database Schema

Run the following SQL to create the audit log table:

```sql
CREATE TABLE document_classifications (
    id                          SERIAL PRIMARY KEY,
    document_name               TEXT,
    document_path               TEXT,
    total_pages                 INTEGER,
    classification_results      TEXT,
    document_types_count        INTEGER,
    request_time                TIMESTAMPTZ,
    response_time               TIMESTAMPTZ,
    processing_duration_seconds FLOAT,
    classification_status       TEXT,   -- 'success' | 'error' | 'timeout'
    error_message               TEXT,
    api_endpoint                TEXT,
    request_payload             TEXT
);
```

---

## Email Alerting

The service sends automated HTML email alerts via Gmail SMTP in two scenarios:

### File Not Found Storm
Triggered when the **same document path** fails with `FileNotFoundError` **3 consecutive times**. Indicates a persistent infrastructure or routing issue.

### Request Timeout
Triggered when any request exceeds the **57-second hard timeout**. Lists all affected documents. Alert is sent asynchronously in a background thread to avoid blocking the response.

> 💡 **Gmail Setup:** Use an [App Password](https://support.google.com/accounts/answer/185833) rather than your main account password. Enable 2FA on the sender account first.

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Document path missing | `FileNotFoundError` raised, logged to DB, returned in `errors[]` |
| File not found on disk | Same as above + email alert after 3 consecutive failures |
| OCR failure | Returns empty text, classifies as `Unknown Document` |
| Request timeout (57s) | Remaining futures cancelled, timeout alert email sent |
| DB connection failure | Logged to application log, request still completes |
| Non-PDF upload | Returns `400` with descriptive error message |

---

## Project Structure

```
document-classification-service/
│
├── app.py                          # Main Flask application
├── requirements.txt                # Python dependencies
├── .env.example                    # Environment variable template
│
├── keywords/                       # Keyword JSON files for classification
│   ├── one_two_page_keywords.json
│   ├── below_10_keywords.json
│   ├── above_10_keywords.json
│   └── keywords.json               # Fallback keyword file
│
├── templates/
│   └── index.html                  # Browser upload UI
│
└── README.md
```

---

## Running in Production

### With Gunicorn (recommended)

```bash
pip install gunicorn
gunicorn app:app \
  --workers 4 \
  --threads 2 \
  --timeout 120 \
  --bind 0.0.0.0:8000 \
  --access-logfile - \
  --error-logfile -
```

### With Docker

```dockerfile
FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["gunicorn", "app:app", "--workers", "4", "--timeout", "120", "--bind", "0.0.0.0:8000"]
```

```bash
docker build -t doc-classification-service .
docker run -p 8000:8000 --env-file .env doc-classification-service
```

### Recommended `requirements.txt`

```
flask
pdf2image
pytesseract
pillow
rapidfuzz
psycopg2-binary
pytz
werkzeug
gunicorn
```

---

## Known Limitations

- **OCR accuracy** depends on scan quality; low-resolution or skewed PDFs may reduce classification accuracy.
- **Windows path issue** with `TemporaryDirectory` has been fixed by using manual `tempfile.mkdtemp()` + `shutil.rmtree()`. See `extract_text_from_pdf()` docstring for details.
- **Streaming endpoint** uses `max_workers=1` — documents are processed sequentially in `/stream_classification`. Increase with caution depending on server resources.
- **Hardcoded recipients** in `EMAIL_CONFIG["recipients"]` — should be moved to environment variables for flexibility.
- **In-memory error tracker** (`file_not_found_errors`) is not shared across multiple worker processes. Use Redis or a DB-backed counter in multi-worker deployments.

---

## Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Commit your changes: `git commit -m 'feat: add my feature'`
4. Push to the branch: `git push origin feature/my-feature`
5. Open a Pull Request

Please follow [Conventional Commits](https://www.conventionalcommits.org/) for commit messages.

---

## License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

---

## 👨‍💻 Author

<table>
  <tr>
    <td align="center">
      <strong>Abinashkumar C</strong><br><br>
      <a href="mailto:abinashkumarc752@gmail.com">📧 abinashkumarc752@gmail.com</a><br><br>
      <a href="https://github.com/AbinashkumarC">🐙 GitHub</a> &nbsp;|&nbsp;
      <a href="https://www.linkedin.com/in/abinashkumar-c-b7222b251/">💼 LinkedIn</a> &nbsp;|&nbsp;
      <a href="https://chimerical-sunshine-442277.netlify.app/">🌐 Portfolio</a>
    </td>
  </tr>
</table>

Feel free to reach out for questions, collaboration, or feedback about this project.

---

<p align="center">
  Built with ❤️ for health insurance document automation by <a href="https://chimerical-sunshine-442277.netlify.app/">Abinashkumar C</a>
</p>

<img width="1919" height="891" alt="image" src="https://github.com/user-attachments/assets/7aaf1a0c-91bb-4ba4-b1f3-cb3217bb56e7" />

<img width="1920" height="1080" alt="image" src="https://github.com/user-attachments/assets/095cd094-72d5-495b-b906-d2efa30890ff" />
<img width="1913" height="893" alt="image" src="https://github.com/user-attachments/assets/324881c5-c6f2-403f-9a1e-970602e4f38a" />

