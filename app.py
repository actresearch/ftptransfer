from O365 import Account, FileSystemTokenBackend, mailbox, MSGraphProtocol
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import threading
import hashlib
from flask import Flask, Response, stream_with_context, request
from flask_cors import CORS
from flask_sse import sse
from apscheduler.schedulers.background import BackgroundScheduler
import subprocess
import sys
from flask_cors import CORS
import email
import shutil

#documentation -- https://github.com/O365/python-o365

app = Flask(__name__)
CORS(app)
app.config["REDIS_URL"] = os.getenv("REDIS_URL", "redis://redis:6379/0")
app.register_blueprint(sse, url_prefix='/dontwork')

CLIENT_ID = os.getenv('O365_CLIENT_ID')
CLIENT_SECRET = os.getenv('O365_CLIENT_SECRET')
TENANT_ID = os.getenv('O365_TENANT_ID')
MAILBOX_USER = os.getenv('MAILBOX_USER', 'jrobinson@actresearch.net')
COMPLETED_FOLDER = os.getenv('COMPLETED_FOLDER', 'CompletedAutomations')
MOUNTS_ROOT = Path(os.getenv('MOUNTS_ROOT', '/home/actserver/mounts'))
WREPORTS_ROOT = Path(os.getenv('WREPORTS_ROOT', '/mnt/wreports'))
TOKEN_PATH = Path(os.getenv('TOKEN_PATH', str(MOUNTS_ROOT / 'token')))
TOKEN_FILENAME = os.getenv('TOKEN_FILENAME', 'my_token.txt')
JSON_REPORTS_PATH = Path(os.getenv('JSON_REPORTS_PATH', str(MOUNTS_ROOT / 'json' / 'Reports')))
PRELIMS_PATH = Path(os.getenv('PRELIMS_PATH', str(MOUNTS_ROOT / 'prelims')))
SCRIPTS_PATH = Path(os.getenv('SCRIPTS_PATH', str(MOUNTS_ROOT / 'mlp')))
FLASK_HOST = os.getenv('FLASK_HOST', '0.0.0.0')
FLASK_PORT = int(os.getenv('FLASK_PORT', '5000'))
AUTH_CHECK_INTERVAL = int(os.getenv('AUTH_CHECK_INTERVAL') or '300')
O365_AUTH_TIMEOUT = int(os.getenv('O365_AUTH_TIMEOUT') or '20')
STREAM_POLL_INTERVAL = int(os.getenv('STREAM_POLL_INTERVAL') or '60')
SCRIPT_RUN_TIMEOUT = int(os.getenv('SCRIPT_RUN_TIMEOUT') or '900')
MESSAGE_LOCK_TTL = int(os.getenv('MESSAGE_LOCK_TTL') or '3600')

O365_CONFIGURED = bool(CLIENT_ID and CLIENT_SECRET and TENANT_ID)

credentials = (CLIENT_ID, CLIENT_SECRET)

# the default protocol will be Microsoft Graph
token_backend = FileSystemTokenBackend(token_path=str(TOKEN_PATH), token_filename=TOKEN_FILENAME)
account = None
if O365_CONFIGURED:
    account = Account(credentials, auth_flow_type='credentials', tenant_id=TENANT_ID,
                      token_backend=token_backend)
else:
    print("ERROR: O365_CLIENT_ID, O365_CLIENT_SECRET, and O365_TENANT_ID must be set", flush=True)

auth_lock = threading.Lock()
auth_executor = ThreadPoolExecutor(max_workers=1)
last_auth_check = 0
last_auth_ok = False
last_auth_error = None

EXPECTED_TRANSFER_SCRIPTS = [
    "MLPScriptUSEDFlash.sh",
    "MLPScriptFREIGHTOUTLOOK.sh",
    "MLPScriptUSTrailer.sh",
    "MLPScriptUSED.sh",
    "MLPScriptNAC58.sh",
    "MLPScriptNABURS.sh",
    "MLPScriptNACompleteBurs.sh",
    "MLPScriptNACVOUTLOOK.sh",
    "MLPScriptPrelim.sh",
]

TRANSFER_RULES = [
    ("Used Truck Flash Report", "Used Truck Flash Report", "MLPScriptUSEDFlash.sh"),
    ("Freight Forecast OUTLOOK Report", "Freight Forecast OUTLOOK Report", "MLPScriptFREIGHTOUTLOOK.sh"),
    ("U.S. Trailer Flash", "U.S. Trailer Flash", "MLPScriptUSTrailer.sh"),
    ("U.S. Used Truck Report", "U.S. Used Truck Report", "MLPScriptUSED.sh"),
    ("SOI N.A. Classes 5-8 Vehicles Flash Report", "SOI N.A. Classes 5-8 Vehicles Flash Report", "MLPScriptNAC58.sh"),
    ("Build & Retail Sales Flash Report", "Build & Retail Sales Flash Report", "MLPScriptNABURS.sh"),
    ("Complete BURS Report", "Complete BURS Report", "MLPScriptNACompleteBurs.sh"),
    ("N.A. Commercial Vehicle OUTLOOK Report", "N.A. Commercial Vehicle OUTLOOK Report", "MLPScriptNACVOUTLOOK.sh"),
]

PRELIM_RULES = [
    ("Commercial Vehicle Preliminary Net Orders", "Commercial Vehicle Preliminary Net Orders", "Commercial Vehicle Preliminary Net Orders.eml"),
    ("U.S. Trailer Prelim Net Orders", "U.S. Trailer Prelim Net Orders", "U.S. Trailer Prelim Net Orders.eml"),
]

currentMonth = datetime.now().month
currentYear = datetime.now().year
currentDay = datetime.now().day
threeminago = datetime.now() - timedelta(minutes=3)

#Path = "C:/Users/ITGURU/PycharmProjects/JSON/"
#Path = "C:/Users/ITGURU/PycharmProjects/JSON/"

# Function to extract the content from the email message
import chardet  # Optional, only if you want smarter encoding detection

def get_email_content(message):
    """Extract and decode text/plain parts of an email message."""
    parts = []
    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            content_disposition = part.get("Content-Disposition", "")

            # Skip attachments and non-text parts
            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        parts.append(payload.decode('utf-8'))
                except UnicodeDecodeError:
                    # Fallback: detect encoding or log issue
                    detected = chardet.detect(payload)
                    encoding = detected.get("encoding", "utf-8")
                    try:
                        parts.append(payload.decode(encoding))
                    except Exception:
                        parts.append("[Unreadable text]")
    else:
        try:
            parts.append(message.get_payload(decode=True).decode('utf-8'))
        except UnicodeDecodeError:
            parts.append("[Unreadable non-multipart email]")

    return "\n".join(parts)


# Function to convert email to JSON and save it
def convert_email_to_json(file_path, base_file_name):
    """Convert an email file to a JSON file."""
    with file_path.open('r') as file:
        msg = email.message_from_file(file)

    email_data = {
        'subject': msg['subject'],
        'from': msg['from'],
        'to': msg['to'],
        'date': msg['date'],
        'content': get_email_content(msg)
    }

    json_data = json.dumps(email_data, indent=4)

    # Define the path where the JSON will be saved
    base_path = JSON_REPORTS_PATH
    today = datetime.today()
    first = today.replace(day=1)
    last_month = first - timedelta(days=1)
    month = last_month.strftime("%B")
    year = datetime.now().strftime("%Y")
    write_file_path = base_path / (base_file_name + month + " " + year + '.json')

    # Write the JSON data to a file
    with write_file_path.open('w') as json_file:
        json_file.write(json_data)
        time.sleep(5)

    return write_file_path

# Your original code with the addition of converting email to JSON
eml_dir = PRELIMS_PATH
eml_filename = "Commercial Vehicle Preliminary Net Orders.eml"
eml_path = eml_dir / eml_filename
destination = JSON_REPORTS_PATH  # Replace with actual destination path

# Ensure the directory exists
eml_dir.mkdir(parents=True, exist_ok=True)
JSON_REPORTS_PATH.mkdir(parents=True, exist_ok=True)


def script_path(name):
    return str(SCRIPTS_PATH / name)


def transfer_output_summary(stdout, stderr):
    output = "\n".join(value for value in (stdout or "", stderr or "") if value)
    files_seen = []
    successful = []
    failed = []
    no_files_found = "No files found to upload." in output

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("Uploading: "):
            files_seen.append(line.removeprefix("Uploading: ").strip())
        elif line.startswith("SUCCESS: ") and " uploaded successfully" in line:
            file_path = line.removeprefix("SUCCESS: ").split(" uploaded successfully", 1)[0].strip()
            successful.append(file_path)
        elif line.startswith("ERROR: Failed to upload "):
            file_path = line.removeprefix("ERROR: Failed to upload ").split(" (exit code:", 1)[0].strip()
            failed.append(file_path)

    if not files_seen and successful:
        files_seen = successful

    return {
        "files_seen": files_seen,
        "successful_transfers": successful,
        "failed_transfers": failed,
        "no_files_found": no_files_found
    }


def describe_files(file_paths, limit=3):
    if not file_paths:
        return ""

    names = [Path(file_path).name for file_path in file_paths[:limit]]
    suffix = "" if len(file_paths) <= limit else f", +{len(file_paths) - limit} more"
    return ": " + ", ".join(names) + suffix


def run_transfer_script(script_name, label):
    path = SCRIPTS_PATH / script_name
    payload = {
        "message": f"Running transfer: {label}",
        "script": script_name,
        "script_path": str(path),
        "working_directory": str(SCRIPTS_PATH),
        "timestamp": datetime.now().isoformat()
    }

    if not path.exists():
        payload.update({
            "status": "script_missing",
            "ok": False,
            "message": f"Transfer script missing: {path}",
            "error": f"Script not found at {path}"
        })
        print(f"ERROR: {payload['error']}", flush=True)
        return payload

    try:
        result = subprocess.run(
            ["/bin/bash", str(path)],
            cwd=str(SCRIPTS_PATH),
            capture_output=True,
            text=True,
            timeout=SCRIPT_RUN_TIMEOUT
        )
    except subprocess.TimeoutExpired as exc:
        summary = transfer_output_summary(exc.stdout, exc.stderr)
        payload.update({
            "status": "script_timeout",
            "ok": False,
            "message": f"Transfer timed out: {label}",
            "error": f"Script timed out after {SCRIPT_RUN_TIMEOUT} seconds",
            "stdout": (exc.stdout or "")[-2000:],
            "stderr": (exc.stderr or "")[-2000:],
            **summary
        })
        print(f"ERROR: {label} timed out running {path}", flush=True)
        return payload
    except Exception as exc:
        payload.update({
            "status": "script_failed",
            "ok": False,
            "message": f"Transfer failed to start: {label}",
            "error": str(exc)
        })
        print(f"ERROR: {label} failed to start {path}: {exc}", flush=True)
        return payload

    summary = transfer_output_summary(result.stdout, result.stderr)
    if result.returncode == 0 and summary["failed_transfers"]:
        status = "transfer_failed"
        ok = False
        message = f"Transfer finished with failed files: {label}"
    elif result.returncode == 0 and summary["successful_transfers"]:
        status = "transfer_success"
        ok = True
        count = len(summary["successful_transfers"])
        message = f"Transfer successful: {label} ({count} file{'s' if count != 1 else ''}){describe_files(summary['successful_transfers'])}"
    elif result.returncode == 0 and summary["no_files_found"]:
        status = "no_files_found"
        ok = True
        message = f"No files found to transfer: {label}"
    elif result.returncode == 0:
        status = "script_ran"
        ok = True
        message = f"Transfer script completed: {label}"
    else:
        status = "script_failed"
        ok = False
        message = f"Transfer failed: {label}"

    payload.update({
        "status": status,
        "ok": ok,
        "message": message,
        "returncode": result.returncode,
        "stdout": (result.stdout or "")[-2000:],
        "stderr": (result.stderr or "")[-2000:],
        **summary,
        "debug": {
            "command": f"/bin/bash {path}",
            "cwd": str(SCRIPTS_PATH),
            "script_exists": path.exists(),
            "script_is_file": path.is_file()
        },
        "visible_files_after_transfer": transfer_file_snapshot()
    })
    if result.returncode == 0:
        print(f"Script completed: {label} ({path})", flush=True)
    else:
        print(f"ERROR: {label} exited {result.returncode}: {result.stderr}", flush=True)
    return payload


def sse_event(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def message_lock_key(message, label):
    candidates = [
        getattr(message, "object_id", None),
        getattr(message, "message_id", None),
        getattr(message, "internet_message_id", None),
        getattr(message, "conversation_id", None),
        message_text(message),
        label,
    ]
    raw = "|".join(str(value) for value in candidates if value)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def claim_message_lock(message, label):
    lock_dir = MOUNTS_ROOT / ".ftptransfer-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / f"{message_lock_key(message, label)}.lock"

    if lock_path.exists():
        try:
            age = time.time() - lock_path.stat().st_mtime
            if age < MESSAGE_LOCK_TTL:
                return None
        except OSError:
            return None

        try:
            lock_path.unlink()
        except OSError:
            return None

    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None

    with os.fdopen(fd, "w") as lock_file:
        lock_file.write(datetime.now().isoformat())
    return lock_path


def move_message_to_completed(message, destination, label):
    payload = {
        "status": "message_moving",
        "message": f"Moving processed email to {COMPLETED_FOLDER}: {label}",
        "destination": COMPLETED_FOLDER,
        "timestamp": datetime.now().isoformat()
    }

    try:
        message.move(destination)
    except Exception as exc:
        payload.update({
            "status": "message_move_failed",
            "ok": False,
            "message": f"Email move failed after transfer: {label}",
            "error": str(exc)
        })
        print(f"ERROR: failed to move {label} to {COMPLETED_FOLDER}: {exc}", flush=True)
        return payload

    payload.update({
        "status": "message_moved",
        "ok": True,
        "message": f"Email moved to {COMPLETED_FOLDER}: {label}"
    })
    print(f"Moved message to {COMPLETED_FOLDER}: {label}", flush=True)
    return payload


def transfer_file_snapshot(limit=50):
    files = []
    if not SCRIPTS_PATH.exists():
        return files

    for path in SCRIPTS_PATH.iterdir():
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError as exc:
            files.append({
                "name": path.name,
                "path": str(path),
                "error": str(exc)
            })
            continue

        files.append({
            "name": path.name,
            "path": str(path),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        })

    files.sort(key=lambda item: item.get("modified", ""), reverse=True)
    return files[:limit]


def transfer_start_payload(label, script_name, subject):
    path = SCRIPTS_PATH / script_name
    return {
        "status": "script_starting",
        "message": f"Transfer triggered: {label}",
        "subject": subject,
        "script": script_name,
        "script_path": str(path),
        "working_directory": str(SCRIPTS_PATH),
        "script_exists": path.exists(),
        "script_is_file": path.is_file(),
        "visible_files_before_transfer": transfer_file_snapshot(),
        "timestamp": datetime.now().isoformat()
    }


def message_text(message):
    subject = getattr(message, "subject", None)
    return str(subject or message)


def ensure_authenticated(force=False):
    """Cache O365 auth checks so connected streams do not hammer MSAL."""
    global last_auth_check, last_auth_ok, last_auth_error

    if not O365_CONFIGURED or account is None:
        return False, "O365_CLIENT_ID, O365_CLIENT_SECRET, and O365_TENANT_ID must be set"

    now = time.monotonic()
    if not force and last_auth_check and now - last_auth_check < AUTH_CHECK_INTERVAL:
        return last_auth_ok, last_auth_error

    with auth_lock:
        now = time.monotonic()
        if not force and last_auth_check and now - last_auth_check < AUTH_CHECK_INTERVAL:
            return last_auth_ok, last_auth_error

        last_auth_check = now
        try:
            future = auth_executor.submit(account.authenticate)
            last_auth_ok = bool(future.result(timeout=O365_AUTH_TIMEOUT))
            last_auth_error = None if last_auth_ok else "O365 authentication returned false"
        except TimeoutError:
            last_auth_ok = False
            last_auth_error = f"O365 authentication timed out after {O365_AUTH_TIMEOUT} seconds"
            print(f"ERROR: {last_auth_error}", flush=True)
        except Exception as exc:
            last_auth_ok = False
            last_auth_error = str(exc)
            print(f"ERROR: O365 authentication failed: {exc}", flush=True)

        return last_auth_ok, last_auth_error


def auth_status_payload(force=False, check_mailbox=False):
    authenticated, auth_error = ensure_authenticated(force=force)
    payload = {
        "status": "authenticated" if authenticated else "not_authenticated",
        "authenticated": authenticated,
        "o365_configured": O365_CONFIGURED,
        "mailbox_user": MAILBOX_USER,
        "completed_folder": COMPLETED_FOLDER,
        "token_path": str(TOKEN_PATH),
        "token_file_exists": (TOKEN_PATH / TOKEN_FILENAME).exists(),
        "auth_timeout_seconds": O365_AUTH_TIMEOUT,
        "timestamp": datetime.now().isoformat()
    }
    if auth_error:
        payload["message"] = auth_error

    if authenticated and check_mailbox:
        try:
            mailbox_obj = account.mailbox(MAILBOX_USER)
            mailbox_obj.inbox_folder()
            mailbox_obj.get_folder(folder_name=COMPLETED_FOLDER)
            payload["mailbox_ok"] = True
        except Exception as exc:
            payload["mailbox_ok"] = False
            payload["mailbox_error"] = str(exc)
            print(f"ERROR: O365 mailbox check failed: {exc}", flush=True)

    return payload


@app.route("/healthz")
def healthz():
    payload = {
        "status": "ok",
        "o365_configured": O365_CONFIGURED,
        "timestamp": datetime.now().isoformat()
    }
    return payload, 200


@app.route("/auth-status")
def auth_status():
    force = request.args.get("force") in ("1", "true", "yes")
    check_mailbox = request.args.get("mailbox") in ("1", "true", "yes")
    return auth_status_payload(force=force, check_mailbox=check_mailbox), 200


@app.route("/transfer-status")
def transfer_status():
    scripts = []
    for name in EXPECTED_TRANSFER_SCRIPTS:
        path = SCRIPTS_PATH / name
        scripts.append({
            "name": name,
            "path": str(path),
            "exists": path.exists(),
            "is_file": path.is_file()
        })

    return {
        "status": "ok",
        "mounts_root": str(MOUNTS_ROOT),
        "mounts_root_exists": MOUNTS_ROOT.exists(),
        "wreports_root": str(WREPORTS_ROOT),
        "wreports_root_exists": WREPORTS_ROOT.exists(),
        "scripts_path": str(SCRIPTS_PATH),
        "scripts_path_exists": SCRIPTS_PATH.exists(),
        "script_run_timeout_seconds": SCRIPT_RUN_TIMEOUT,
        "scripts": scripts,
        "timestamp": datetime.now().isoformat()
    }, 200


@app.route("/stream-test")
def stream_test():
    @stream_with_context
    def test_events():
        while True:
            payload = {
                "status": "ok",
                "timestamp": datetime.now().isoformat()
            }
            yield sse_event("time", payload)
            sys.stdout.flush()
            time.sleep(15)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    }
    return Response(test_events(), mimetype='text/event-stream', headers=headers)

@app.route("/stream")
def stream():

    def my_function():
        payload = {
            "status": "connected",
            "message": "SSE stream connected",
            "timestamp": datetime.now().isoformat()
        }
        yield sse_event("stream_status", payload)
        sys.stdout.flush()

        while True:
            auth_payload = auth_status_payload()
            authenticated = auth_payload["authenticated"]

            if authenticated:
                yield sse_event("time", auth_payload)
                sys.stdout.flush()

                mailbox = account.mailbox(MAILBOX_USER)
                inbox = mailbox.inbox_folder()
                destination = mailbox.get_folder(folder_name=COMPLETED_FOLDER)

                for message in inbox.get_messages(10):
                    messagetocheck = message_text(message)
                    print(f"Checking message: {messagetocheck}", flush=True)
                    matching_label = None
                    for trigger, label, _script_name in TRANSFER_RULES:
                        if trigger in messagetocheck:
                            matching_label = label
                            break
                    if matching_label is None:
                        for trigger, label, _eml_filename in PRELIM_RULES:
                            if trigger in messagetocheck:
                                matching_label = label
                                break
                    if matching_label is None:
                        continue
                    if claim_message_lock(message, matching_label) is None:
                        print(f"Skipping already-claimed message: {messagetocheck}", flush=True)
                        continue

                    if "Used Truck Flash Report" in messagetocheck:
                        payload = transfer_start_payload("Used Truck Flash Report", "MLPScriptUSEDFlash.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        payload = run_transfer_script("MLPScriptUSEDFlash.sh", "Used Truck Flash Report")
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if payload["ok"]:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()

                    if "Freight Forecast OUTLOOK Report" in messagetocheck:
                        payload = transfer_start_payload("Freight Forecast OUTLOOK Report", "MLPScriptFREIGHTOUTLOOK.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        payload = run_transfer_script("MLPScriptFREIGHTOUTLOOK.sh", "Freight Forecast OUTLOOK Report")
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if payload["ok"]:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()


                    if "U.S. Trailer Flash" in messagetocheck:
                        payload = transfer_start_payload("U.S. Trailer Flash", "MLPScriptUSTrailer.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        payload = run_transfer_script("MLPScriptUSTrailer.sh", "U.S. Trailer Flash")
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if payload["ok"]:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()

                    if "U.S. Used Truck Report" in messagetocheck:
                        payload = transfer_start_payload("U.S. Used Truck Report", "MLPScriptUSED.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        payload = run_transfer_script("MLPScriptUSED.sh", "U.S. Used Truck Report")
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if payload["ok"]:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()

                    if "SOI N.A. Classes 5-8 Vehicles Flash Report" in messagetocheck:
                        payload = transfer_start_payload("SOI N.A. Classes 5-8 Vehicles Flash Report", "MLPScriptNAC58.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        payload = run_transfer_script("MLPScriptNAC58.sh", "SOI N.A. Classes 5-8 Vehicles Flash Report")
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if payload["ok"]:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()

                    if "Build & Retail Sales Flash Report" in messagetocheck:
                        payload = transfer_start_payload("Build & Retail Sales Flash Report", "MLPScriptNABURS.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        payload = run_transfer_script("MLPScriptNABURS.sh", "Build & Retail Sales Flash Report")
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if payload["ok"]:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()

                    if "Complete BURS Report" in messagetocheck:
                        payload = transfer_start_payload("Complete BURS Report", "MLPScriptNACompleteBurs.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        payload = run_transfer_script("MLPScriptNACompleteBurs.sh", "Complete BURS Report")
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if payload["ok"]:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()

                    if "N.A. Commercial Vehicle OUTLOOK Report" in messagetocheck:
                        payload = transfer_start_payload("N.A. Commercial Vehicle OUTLOOK Report", "MLPScriptNACVOUTLOOK.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        payload = run_transfer_script("MLPScriptNACVOUTLOOK.sh", "N.A. Commercial Vehicle OUTLOOK Report")
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if payload["ok"]:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()

                    if "Commercial Vehicle Preliminary Net Orders" in messagetocheck:
                        payload = transfer_start_payload("Commercial Vehicle Preliminary Net Orders", "MLPScriptPrelim.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        #this needs harcoded or set using above Path variable, also this needs to match JSON py directory for email location
                        eml_path = PRELIMS_PATH / 'Commercial Vehicle Preliminary Net Orders.eml'
                        eml_path.parent.mkdir(parents=True, exist_ok=True)
                        message.save_as_eml(to_path=eml_path)
                        time.sleep(5)
                        json_file_path = convert_email_to_json(eml_path, "Commercial Vehicle Preliminary Net Orders ")
                        print(f"✅ Email converted to JSON and saved at {json_file_path}")
                        payload = {
                            "status": "json_created",
                            "message": f"JSON created: {json_file_path.name}",
                            "eml_path": str(eml_path),
                            "json_file_path": str(json_file_path),
                            "timestamp": datetime.now().isoformat()
                        }    
                        yield sse_event("ftp_event", payload)
                        time.sleep(5)
                        prelim_script_path = script_path("MLPScriptPrelim.sh")
                        script_ok = False
                        try:
                            result = subprocess.run(["/bin/bash", prelim_script_path], check=True, cwd=str(SCRIPTS_PATH), capture_output=True, text=True, timeout=SCRIPT_RUN_TIMEOUT)
                            script_ok = True
                            print("✅ MLP Prelim Script Ran Successfully")
                            payload = {
                                "status": "script_ran",
                                "message": "Commercial Vehicle Preliminary Net Orders",
                                "ok": True,
                                "script": "MLPScriptPrelim.sh",
                                "script_path": prelim_script_path,
                                "working_directory": str(SCRIPTS_PATH),
                                "stdout": (result.stdout or "")[-2000:],
                                "stderr": (result.stderr or "")[-2000:],
                                "visible_files_after_transfer": transfer_file_snapshot(),
                                "timestamp": datetime.now().isoformat()
                            }
                        except subprocess.CalledProcessError as e:
                            print(f"❌ ERROR: MLP Prelim Script Failed with error: {e}")
                            payload = {
                                "status": "script_failed",
                                "message": "ERROR: MLP Prelim Script Failed",
                                "ok": False,
                                "script": "MLPScriptPrelim.sh",
                                "script_path": prelim_script_path,
                                "working_directory": str(SCRIPTS_PATH),
                                "returncode": e.returncode,
                                "stdout": (e.stdout or "")[-2000:],
                                "stderr": (e.stderr or "")[-2000:],
                                "visible_files_after_transfer": transfer_file_snapshot(),
                                "timestamp": datetime.now().isoformat()
                            }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if script_ok:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()

                    if "U.S. Trailer Prelim Net Orders" in messagetocheck:
                        payload = transfer_start_payload("U.S. Trailer Prelim Net Orders", "MLPScriptPrelim.sh", messagetocheck)
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        #this needs harcoded or set using above Path variable, also this needs to match JSON py directory for email location
                        eml_path = PRELIMS_PATH / 'U.S. Trailer Prelim Net Orders.eml'
                        eml_path.parent.mkdir(parents=True, exist_ok=True)
                        message.save_as_eml(to_path=eml_path)
                        time.sleep(5)
                        json_file_path = convert_email_to_json(eml_path, "U.S. Trailer Prelim Net Orders ")
                        payload = {
                            "status": "json_created",
                            "message": f"JSON created: {json_file_path.name}",
                            "eml_path": str(eml_path),
                            "json_file_path": str(json_file_path),
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        time.sleep(5)
                        print(f"✅ Email converted to JSON and saved at {json_file_path}")
                        prelim_script_path = script_path("MLPScriptPrelim.sh")
                        script_ok = False
                        try:
                            result = subprocess.run(["/bin/bash", prelim_script_path], check=True, cwd=str(SCRIPTS_PATH), capture_output=True, text=True, timeout=SCRIPT_RUN_TIMEOUT)
                            script_ok = True
                            print("✅ MLP Trailer Prelim Script Ran Successfully")
                            payload = {
                                "status": "script_ran",
                                "message": "U.S. Trailer Prelim Net Orders",
                                "ok": True,
                                "script": "MLPScriptPrelim.sh",
                                "script_path": prelim_script_path,
                                "working_directory": str(SCRIPTS_PATH),
                                "stdout": (result.stdout or "")[-2000:],
                                "stderr": (result.stderr or "")[-2000:],
                                "visible_files_after_transfer": transfer_file_snapshot(),
                                "timestamp": datetime.now().isoformat()
                            }
                        except subprocess.CalledProcessError as e:
                            print(f"❌ ERROR: MLP Trailer Prelim Script Failed with error: {e}")
                            payload = {
                                "status": "script_failed",
                                "message": "ERROR: MLP Trailer Prelim Script Failed",
                                "ok": False,
                                "script": "MLPScriptPrelim.sh",
                                "script_path": prelim_script_path,
                                "working_directory": str(SCRIPTS_PATH),
                                "returncode": e.returncode,
                                "stdout": (e.stdout or "")[-2000:],
                                "stderr": (e.stderr or "")[-2000:],
                                "visible_files_after_transfer": transfer_file_snapshot(),
                                "timestamp": datetime.now().isoformat()
                            }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        if script_ok:
                            payload = move_message_to_completed(message, destination, matching_label)
                            yield sse_event("ftp_event", payload)
                            sys.stdout.flush()
            else:
                print(f"ERROR: O365 stream not authenticated: {auth_payload.get('message', 'Not Authenticated')}", flush=True)
                yield sse_event("time", auth_payload)
                yield sse_event("ftp_event", auth_payload)
                sys.stdout.flush()
            time.sleep(STREAM_POLL_INTERVAL)

    def safe_events():
        while True:
            try:
                yield from my_function()
            except GeneratorExit:
                raise
            except Exception as e:
                print(f"ERROR: stream failed: {e}", flush=True)
                payload = {
                    "status": "error",
                    "message": str(e),
                    "timestamp": datetime.now().isoformat()
                }
                yield sse_event("stream_error", payload)
                sys.stdout.flush()
                time.sleep(60)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no"
    }
    return Response(stream_with_context(safe_events()), mimetype='text/event-stream', headers=headers)

@app.after_request
def after_request(response):
  response.headers['Access-Control-Allow-Methods']='*'
  response.headers['Access-Control-Allow-Origin']='*'
  response.headers['Vary']='Origin'
  return response

if __name__ == "__main__":
    app.run(host=FLASK_HOST, port=FLASK_PORT)


# def run_function():
  #  thread = threading.Timer(60.0, run_function) # 60 seconds = 1 minute
   # thread.start()
    # my_function()

    # return "ran"

# run_function() # start the timer


