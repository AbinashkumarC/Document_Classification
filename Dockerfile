# ================================================
# DocScan AI — Document Classification Service
# Dockerfile
# ================================================

# Use official Python 3.11 slim image (Debian-based)
FROM python:3.11-slim

# ---- Metadata ----
LABEL maintainer="Abinashkumar C <abinashkumarc752@gmail.com>"
LABEL description="DocScan AI — Medical Document Classification Service"
LABEL version="1.0"

# ---- Prevent interactive prompts during apt installs ----
ENV DEBIAN_FRONTEND=noninteractive

# ---- Install system dependencies ----
# poppler-utils  → required by pdf2image (pdftoppm)
# tesseract-ocr  → required by pytesseract (OCR engine)
# tesseract-ocr-eng → English language pack for Tesseract
# libpq-dev      → required to build psycopg2 (PostgreSQL adapter)
# gcc            → C compiler needed for some pip packages
# curl           → useful for health checks
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    tesseract-ocr-eng \
    libpq-dev \
    gcc \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ---- Set working directory inside container ----
WORKDIR /app

# ---- Copy requirements first (Docker layer cache optimization) ----
# If requirements.txt doesn't change, pip install is skipped on rebuild
COPY requirements.txt .

# ---- Install Python dependencies ----
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ---- Copy application source code ----
COPY app.py .
COPY templates/ ./templates/

# ---- Copy keywords folder ----
# The app loads keywords from /app/keywords/ by default inside the container
COPY keywords/ ./keywords/

# ---- Create upload temp directory ----
RUN mkdir -p /tmp/pdf_uploads

# ---- Set environment variables (defaults — override via docker-compose or .env) ----
ENV PYTHONUNBUFFERED=1
ENV KEYWORD_DIR=/app/keywords
ENV UPLOAD_FOLDER=/tmp/pdf_uploads
ENV DOCUMENT_DIR=/opt
ENV STARTUP_DELAY=0

# Database (override these in docker-compose.yml or .env file)
ENV DB_HOST=db
ENV DB_PORT=5432
ENV DB_NAME="postgres"
ENV DB_USER=postgres
ENV DB_PASSWORD=Bu190240

# Email (override in .env)
ENV SMTP_SERVER=smtp.gmail.com
ENV SMTP_PORT=587
ENV SENDER_EMAIL=abinashkumarcabihealth@gmail.com
ENV SENDER_PASSWORD=tmjz_tqza_nikj_lbam

# ---- Expose Flask port ----
EXPOSE 8000

# ---- Health check — Docker will mark container unhealthy if this fails ----
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# ---- Run Flask app with Gunicorn (production WSGI server) ----
# Use gunicorn instead of Flask dev server for production
# 4 worker processes, 2 threads each, 120s timeout for large PDF processing
CMD ["gunicorn", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--threads", "2", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "--error-logfile", "-", \
     "--log-level", "info", \
     "app:app"]