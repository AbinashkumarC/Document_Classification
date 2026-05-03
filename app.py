from flask import Flask, request, Response, jsonify, stream_with_context, render_template, send_from_directory
import os, json, re, pytesseract, time, logging, threading
from pdf2image import convert_from_path
from tempfile import TemporaryDirectory
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from PIL import Image
from rapidfuzz import fuzz
from datetime import datetime
import pytz
import psycopg2
from psycopg2.extras import Json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.utils import secure_filename

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

IST = pytz.timezone("Asia/Kolkata")

app = Flask(__name__, template_folder="templates")

# Upload folder for temporary PDF storage
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "/tmp/pdf_uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf"}

# Configurable delay before requests (default = 0)
STARTUP_DELAY = int(os.getenv("STARTUP_DELAY", 0))

# REQUEST TIMEOUT IN SECONDS
REQUEST_TIMEOUT_SECONDS = 57

# Configurable container paths
KEYWORD_DIR = os.getenv("KEYWORD_DIR", r"D:\personal_projects\document_classification\keywords")
DOCUMENT_DIR = os.getenv("DOCUMENT_DIR", "/opt")

# Database configuration
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", 5432),
    "dbname": os.getenv("DB_NAME", "Document classification"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", "Bu190240")
}

# Email Configuration
EMAIL_CONFIG = {
    "smtp_server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
    "smtp_port": int(os.getenv("SMTP_PORT", 587)),
    "sender_email": os.getenv("SENDER_EMAIL", "abinashkumarcabihealth@gmail.com"),
    "sender_password": os.getenv("SENDER_PASSWORD", "tmjz tqza nikj lbam"),
    "recipients": ["abinashkumarc752@gmail.com"]
}

# Error tracking
file_not_found_errors = {}

# -----------------------
# Utility Helpers
# -----------------------

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# -----------------------
# Email Functions
# -----------------------

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg['From'] = EMAIL_CONFIG['sender_email']
        msg['To'] = ', '.join(EMAIL_CONFIG['recipients'])
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'html'))

        server = smtplib.SMTP(EMAIL_CONFIG['smtp_server'], EMAIL_CONFIG['smtp_port'])
        server.starttls()
        server.login(EMAIL_CONFIG['sender_email'], EMAIL_CONFIG['sender_password'])
        server.send_message(msg)
        server.quit()

        logging.info(f"Email sent successfully: {subject}")
        return True
    except Exception as e:
        logging.error(f"Failed to send email: {e}")
        return False

def send_file_not_found_alert(document_name, document_path, error_count):
    subject = "🚨 Alert: Multiple File Not Found Errors - Document Classification Service"
    body = f"""
    <html><body>
        <h2 style="color: #d9534f;">File Not Found Alert</h2>
        <p>The document classification service has encountered <strong>{error_count}</strong> consecutive file not found errors.</p>
        <ul>
            <li><strong>Document Name:</strong> {document_name}</li>
            <li><strong>Document Path:</strong> {document_path}</li>
            <li><strong>Error Count:</strong> {error_count}</li>
            <li><strong>Timestamp:</strong> {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}</li>
        </ul>
        <hr>
        <p style="color: #777; font-size: 12px;">Automated alert from Document Classification Service.</p>
    </body></html>
    """
    send_email(subject, body)

def send_timeout_alert(documents_list, request_time):
    subject = "⏱️ Alert: Request Timeout - Document Classification Service"
    doc_details = "<ul>" + "".join(f"<li>{doc.get('documentName', 'Unknown')}</li>" for doc in documents_list) + "</ul>"
    body = f"""
    <html><body>
        <h2 style="color: #d9534f;">Request Timeout Alert</h2>
        <p>A request exceeded the maximum allowed processing time of {REQUEST_TIMEOUT_SECONDS}s.</p>
        <ul>
            <li><strong>Request Time:</strong> {request_time.strftime('%Y-%m-%d %H:%M:%S IST')}</li>
            <li><strong>Documents Affected:</strong></li>
        </ul>
        {doc_details}
        <hr>
        <p style="color: #777; font-size: 12px;">Automated alert from Document Classification Service.</p>
    </body></html>
    """
    send_email(subject, body)

# -----------------------
# Database Functions
# -----------------------

def get_db_connection():
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        return conn
    except Exception as e:
        logging.error(f"Database connection error: {e}")
        return None

def log_to_database(document_name, document_path, total_pages, classification_results,
                    request_time, response_time, status, error_message, api_endpoint, request_payload):
    conn = None
    try:
        conn = get_db_connection()
        if not conn:
            logging.error("Failed to connect to database for logging")
            return

        cur = conn.cursor()
        processing_duration = (response_time - request_time).total_seconds()
        document_types_count = len(classification_results) if classification_results else 0
        classification_json = json.dumps(classification_results)

        insert_query = """
        INSERT INTO document_classifications
        (document_name, document_path, total_pages, classification_results,
         document_types_count, request_time, response_time, processing_duration_seconds,
         classification_status, error_message, api_endpoint, request_payload)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        cur.execute(insert_query, (
            document_name, document_path, total_pages, classification_json,
            document_types_count, request_time, response_time, processing_duration,
            status, error_message, api_endpoint, json.dumps(request_payload)
        ))
        conn.commit()
        cur.close()
        logging.info(f"Successfully logged classification for: {document_name}")

    except Exception as e:
        logging.error(f"Error logging to database: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

# -----------------------
# Request / Response Logging
# -----------------------

@app.before_request
def log_request_info():
    request.start_time = time.time()
    request.request_timestamp = datetime.now(IST)
    now_ist = request.request_timestamp.strftime("%Y-%m-%d %H:%M:%S")
    logging.info(
        f"[REQUEST] Time={now_ist}, Path={request.path}, Method={request.method}"
    )

@app.after_request
def log_response_info(response):
    duration = time.time() - getattr(request, "start_time", time.time())
    now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S")
    logging.info(
        f"[RESPONSE] Time={now_ist}, Path={request.path}, Status={response.status}, Duration={duration:.2f}s"
    )
    return response

# -----------------------
# Utility Functions
# -----------------------

def check_timeout(request_time):
    elapsed_seconds = (datetime.now(IST) - request_time).total_seconds()
    return elapsed_seconds > REQUEST_TIMEOUT_SECONDS

def load_keywords(total_pages):
    try:
        if total_pages in [1]:
            filename = "one_two_page_keywords.json"
        elif total_pages > 10:
            filename = "above_10_keywords.json"
        else:
            filename = "below_10_keywords.json"

        filepath = os.path.join(KEYWORD_DIR, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        # Fallback: try loading keywords.json from KEYWORD_DIR
        try:
            fallback_path = os.path.join(KEYWORD_DIR, "keywords.json")
            with open(fallback_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            logging.warning(f"[WARN] Failed to load keywords for {total_pages} pages: {e}")
            return {}

def extract_text_from_pdf(pdf_path):
    """
    Extract text from a PDF using OCR.

    FIX: On Windows, TemporaryDirectory used as a context manager deletes the
    directory (and its files) as soon as the `with` block exits — but
    pdf2image has already returned PIL Image objects whose backing JPEG files
    now live in that deleted directory. Any subsequent read of those image
    objects raises [WinError 267] "The directory name is invalid".

    Solution: create the temp dir manually, do all work (convert + OCR) inside
    a try/finally, and clean it up ourselves after we are done with every image.
    """
    import shutil, tempfile

    temp_dir = tempfile.mkdtemp()
    try:
        images = convert_from_path(pdf_path, output_folder=temp_dir, fmt='jpeg', dpi=200)
        total_pages = len(images)
        full_text = []

        if total_pages <= 1:
            for img in images:
                text = pytesseract.image_to_string(img)
                if text.strip():
                    full_text.append(text)
        else:
            pages_to_process = list(images[:min(30, total_pages)])
            if total_pages > 3:
                pages_to_process.append(images[-1])

            for img in pages_to_process:
                cropped = img.crop((0, 0, img.width, int(img.height * 0.25)))
                text = pytesseract.image_to_string(cropped)
                if text.strip():
                    full_text.append(text)

        return full_text, total_pages

    except Exception as e:
        logging.error(f"[OCR ERROR] {e}")
        return [], 0

    finally:
        # Always clean up the temp directory after we are done with the images
        shutil.rmtree(temp_dir, ignore_errors=True)

def fuzzy_match(text, keyword, threshold=90):
    try:
        return fuzz.partial_ratio(text, keyword) >= threshold
    except Exception:
        return False

# -----------------------
# Classification Logic
# -----------------------

def _required_count_for_doctype(doc_type_name: str):
    name = doc_type_name.lower()
    if any(s in name for s in ("discharge", "final bill")):
        return 3
    if any(s in name for s in ("investigation", "kyc")):
        return 2
    return 1

def classify_document(pages_text, total_pages):
    matched_classes = set()
    full_text = " ".join(pages_text).lower()
    document_keywords = load_keywords(total_pages)

    for page in pages_text:
        lower_page = page.lower()

        if "aadhaar" in lower_page or "uidai" in lower_page:
            if re.search(r'\b[2-9]{1}[0-9]{11}\b', lower_page) or re.search(r'\d{4} \d{4} \d{4}', lower_page):
                matched_classes.add("Aadhar Card copy of Primary Policy Holder")

        if "pan" in lower_page:
            if re.search(r'\b[A-Z]{5}[0-9]{4}[A-Z]\b', page):
                matched_classes.add("PAN Card copy of Primary Policy Holder")

        if len(re.findall(r'\b(ref|lot|sterile)\b', lower_page)) >= 6:
            matched_classes.add("Implant Stickers")

    if total_pages in [1]:
        for page in pages_text:
            page_lower = page.lower()
            for doc_type, keywords in document_keywords.items():
                matched_keywords = set()
                for kw in keywords:
                    if fuzzy_match(page_lower, kw.lower()):
                        matched_keywords.add(kw.lower())
                required = _required_count_for_doctype(doc_type)
                if len(matched_keywords) >= required:
                    matched_classes.add(doc_type)
    else:
        for doc_type, keywords in document_keywords.items():
            for kw in keywords:
                if fuzzy_match(full_text, kw.lower()):
                    matched_classes.add(doc_type)
                    break

    if ("Aadhar Card copy of Primary Policy Holder" not in matched_classes and
            "PAN Card copy of Primary Policy Holder" not in matched_classes):
        first_pref = {"Member ID Card", "cKYC form with photo of the primary policy holder", "Policy Copy"}
        second_pref = {"Pre-Auth Request Form Part C", "Final Approvel Letter", "Final Bill with Tax Invoice", "Discharge Summary"}
        if any(doc in matched_classes for doc in first_pref):
            if sum(1 for doc in matched_classes if doc in second_pref) >= 2:
                matched_classes.add("Aadhar Card copy of Primary Policy Holder")
                matched_classes.add("PAN Card copy of Primary Policy Holder")

    return list(matched_classes) if matched_classes else ["Unknown Document"]

# -----------------------
# Processing Functions
# -----------------------

def process_doc(doc, request_time, api_endpoint, request_payload):
    doc_name = doc.get("documentName", "Unknown")
    doc_path = doc.get("documentPath")
    response_time = None
    status = "success"
    error_message = None
    classifications = []
    total_pages = 0

    try:
        if check_timeout(request_time):
            raise TimeoutError(f"Request exceeded maximum timeout of {REQUEST_TIMEOUT_SECONDS} seconds")

        if not doc_path:
            raise FileNotFoundError(f"No path provided for document: {doc_name}")

        full_path = doc_path.strip()
        if not os.path.exists(full_path):
            raise FileNotFoundError(f"File not found: {full_path}")

        pages_text, total_pages = extract_text_from_pdf(full_path)
        classifications = classify_document(pages_text, total_pages)
        response_time = datetime.now(IST)

        if doc_path in file_not_found_errors:
            del file_not_found_errors[doc_path]

        log_to_database(
            document_name=doc_name, document_path=doc_path, total_pages=total_pages,
            classification_results=classifications, request_time=request_time,
            response_time=response_time, status=status, error_message=error_message,
            api_endpoint=api_endpoint, request_payload=request_payload
        )

        return {"documentName": doc_name, "classification": classifications, "totalPages": total_pages}

    except TimeoutError as te:
        status = "timeout"
        error_message = str(te)
        response_time = datetime.now(IST)
        logging.error(f"Timeout occurred while processing: {doc_name}")
        log_to_database(
            document_name=doc_name, document_path=doc_path, total_pages=total_pages,
            classification_results=classifications, request_time=request_time,
            response_time=response_time, status=status, error_message=error_message,
            api_endpoint=api_endpoint, request_payload=request_payload
        )
        raise

    except FileNotFoundError as fe:
        status = "error"
        error_message = str(fe)
        response_time = datetime.now(IST)

        if doc_path not in file_not_found_errors:
            file_not_found_errors[doc_path] = {"count": 0, "document_name": doc_name}

        file_not_found_errors[doc_path]["count"] += 1
        error_count = file_not_found_errors[doc_path]["count"]
        logging.error(f"File not found error #{error_count} for: {doc_name}")

        if error_count == 3:
            logging.warning(f"Sending email alert for {doc_name} after {error_count} errors")
            send_file_not_found_alert(doc_name, doc_path, error_count)

        log_to_database(
            document_name=doc_name, document_path=doc_path, total_pages=total_pages,
            classification_results=classifications, request_time=request_time,
            response_time=response_time, status=status, error_message=error_message,
            api_endpoint=api_endpoint, request_payload=request_payload
        )
        raise

    except Exception as e:
        status = "error"
        error_message = str(e)
        response_time = datetime.now(IST)
        log_to_database(
            document_name=doc_name, document_path=doc_path, total_pages=total_pages,
            classification_results=classifications, request_time=request_time,
            response_time=response_time, status=status, error_message=error_message,
            api_endpoint=api_endpoint, request_payload=request_payload
        )
        raise

# -----------------------
# File Upload Endpoint (for HTML frontend)
# -----------------------

@app.route('/upload_classify', methods=['POST'])
def upload_classify():
    """
    Accept a PDF file upload directly from browser, run classification, return result.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file part in request"}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No file selected"}), 400

    if not allowed_file(file.filename):
        return jsonify({"error": "Only PDF files are supported"}), 400

    filename = secure_filename(file.filename)
    temp_path = os.path.join(UPLOAD_FOLDER, filename)

    try:
        file.save(temp_path)
        pages_text, total_pages = extract_text_from_pdf(temp_path)
        classifications = classify_document(pages_text, total_pages)

        return jsonify({
            "documentName": filename,
            "classification": classifications,
            "totalPages": total_pages,
            "processedAt": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")
        })

    except Exception as e:
        logging.error(f"Upload classify error: {e}")
        return jsonify({"error": str(e)}), 500

    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass

# -----------------------
# API Routes (Original)
# -----------------------

@app.route('/classify', methods=['POST'])
def classify():
    data = request.get_json()
    if not data or 'documents' not in data:
        return jsonify({"error": "Invalid request format"}), 400

    request_time = datetime.now(IST)
    api_endpoint = "/classify"
    results, errors = [], []
    timeout_occurred = False

    with ThreadPoolExecutor(max_workers=4) as executor:
        future_to_doc = {
            executor.submit(process_doc, doc, request_time, api_endpoint, data): doc
            for doc in data["documents"]
        }

        for future, doc in future_to_doc.items():
            elapsed = (datetime.now(IST) - request_time).total_seconds()
            remaining = REQUEST_TIMEOUT_SECONDS - elapsed

            if remaining <= 0:
                timeout_occurred = True
                errors.append({
                    "documentName": doc.get("documentName", "Unknown"),
                    "error": f"Request timeout: exceeded {REQUEST_TIMEOUT_SECONDS}s limit"
                })
                future.cancel()
                continue

            try:
                result = future.result(timeout=remaining)
                results.append(result)
            except TimeoutError:
                timeout_occurred = True
                logging.error(f"Hard timeout hit for: {doc.get('documentName', 'Unknown')}")
                errors.append({
                    "documentName": doc.get("documentName", "Unknown"),
                    "error": f"Processing timed out after {REQUEST_TIMEOUT_SECONDS}s"
                })
            except FileNotFoundError as fe:
                errors.append({"documentName": doc.get("documentName", "Unknown"), "error": str(fe)})
            except Exception as e:
                errors.append({"documentName": doc.get("documentName", "Unknown"), "error": str(e)})

    if timeout_occurred:
        threading.Thread(target=send_timeout_alert, args=(data.get("documents", []), request_time)).start()

    return jsonify({"results": results, "errors": errors})

@app.route('/stream_classification', methods=['POST'])
def stream_classification():
    data = request.get_json()
    if not data or 'documents' not in data:
        return Response(json.dumps({"error": "Invalid request format"}), mimetype='application/json')

    request_time = datetime.now(IST)
    api_endpoint = "/stream_classification"

    def generate():
        yield '{"results":['
        first = True
        errors = []
        timeout_occurred = False

        with ThreadPoolExecutor(max_workers=1) as executor:
            future_to_doc = {
                executor.submit(process_doc, doc, request_time, api_endpoint, data): doc
                for doc in data['documents']
            }

            for future, doc in future_to_doc.items():
                elapsed = (datetime.now(IST) - request_time).total_seconds()
                remaining = REQUEST_TIMEOUT_SECONDS - elapsed

                if remaining <= 0:
                    timeout_occurred = True
                    errors.append({
                        "documentName": doc.get("documentName", "Unknown"),
                        "error": f"Request timeout: exceeded {REQUEST_TIMEOUT_SECONDS}s limit"
                    })
                    future.cancel()
                    continue

                try:
                    result = future.result(timeout=remaining)
                    if not first:
                        yield ','
                    else:
                        first = False
                    yield json.dumps(result)
                except TimeoutError:
                    timeout_occurred = True
                    errors.append({
                        "documentName": doc.get("documentName", "Unknown"),
                        "error": f"Processing timed out after {REQUEST_TIMEOUT_SECONDS}s"
                    })
                except FileNotFoundError as fe:
                    errors.append({"documentName": doc.get("documentName", "Unknown"), "error": str(fe)})
                except Exception as e:
                    errors.append({
                        "documentName": doc.get("documentName", "Unknown"),
                        "error": f"Unexpected error: {str(e)}"
                    })

        if timeout_occurred:
            threading.Thread(target=send_timeout_alert, args=(data.get("documents", []), request_time)).start()

        yield '], "errors":'
        yield json.dumps(errors)
        yield '}'

    return Response(stream_with_context(generate()), mimetype='application/json')

@app.route('/', methods=['GET'])
def index():
    return render_template('index.html')

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "timestamp": datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S IST")})

# -----------------------
# Main Entry
# -----------------------

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000, debug=True, threaded=True)