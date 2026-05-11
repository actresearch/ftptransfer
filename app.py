from O365 import Account, FileSystemTokenBackend, mailbox, MSGraphProtocol
import time
from datetime import datetime, timedelta
from pathlib import Path
import json
import os
import threading
from flask import Flask, Response, stream_with_context
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
TOKEN_PATH = Path(os.getenv('TOKEN_PATH', str(MOUNTS_ROOT / 'token')))
TOKEN_FILENAME = os.getenv('TOKEN_FILENAME', 'my_token.txt')
JSON_REPORTS_PATH = Path(os.getenv('JSON_REPORTS_PATH', str(MOUNTS_ROOT / 'json' / 'Reports')))
PRELIMS_PATH = Path(os.getenv('PRELIMS_PATH', str(MOUNTS_ROOT / 'prelims')))
SCRIPTS_PATH = Path(os.getenv('SCRIPTS_PATH', str(MOUNTS_ROOT / 'mlp')))
FLASK_HOST = os.getenv('FLASK_HOST', '0.0.0.0')
FLASK_PORT = int(os.getenv('FLASK_PORT', '5000'))
AUTH_CHECK_INTERVAL = int(os.getenv('AUTH_CHECK_INTERVAL') or '300')
STREAM_POLL_INTERVAL = int(os.getenv('STREAM_POLL_INTERVAL') or '60')

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
last_auth_check = 0
last_auth_ok = False
last_auth_error = None

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


def sse_event(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def message_text(message):
    subject = getattr(message, "subject", None)
    return str(subject or message)


def ensure_authenticated():
    """Cache O365 auth checks so connected streams do not hammer MSAL."""
    global last_auth_check, last_auth_ok, last_auth_error

    if not O365_CONFIGURED or account is None:
        return False, "O365_CLIENT_ID, O365_CLIENT_SECRET, and O365_TENANT_ID must be set"

    now = time.monotonic()
    if last_auth_check and now - last_auth_check < AUTH_CHECK_INTERVAL:
        return last_auth_ok, last_auth_error

    with auth_lock:
        now = time.monotonic()
        if last_auth_check and now - last_auth_check < AUTH_CHECK_INTERVAL:
            return last_auth_ok, last_auth_error

        last_auth_check = now
        try:
            last_auth_ok = bool(account.authenticate())
            last_auth_error = None if last_auth_ok else "O365 authentication returned false"
        except Exception as exc:
            last_auth_ok = False
            last_auth_error = str(exc)
            print(f"ERROR: O365 authentication failed: {exc}", flush=True)

        return last_auth_ok, last_auth_error


@app.route("/healthz")
def healthz():
    payload = {
        "status": "ok",
        "o365_configured": O365_CONFIGURED,
        "timestamp": datetime.now().isoformat()
    }
    return payload, 200


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

    @stream_with_context
    def my_function():

        while True:
            authenticated, auth_error = ensure_authenticated()

            if authenticated:
                _time = datetime.now().isoformat()
                heartbeat_payload = {
                    "status": "authenticated",
                    "timestamp": _time
                }
                yield f'event: time\ndata: {json.dumps(heartbeat_payload)}\n\n'
                sys.stdout.flush()

                mailbox = account.mailbox(MAILBOX_USER)
                inbox = mailbox.inbox_folder()
                destination = mailbox.get_folder(folder_name=COMPLETED_FOLDER)

                for message in inbox.get_messages(10):
                    messagetocheck = message_text(message)
                    print(f"Checking message: {messagetocheck}", flush=True)
                    payload = {
                        "status": "message_seen",
                        "message": messagetocheck,
                        "timestamp": datetime.now().isoformat()
                    }
                    yield sse_event("ftp_event", payload)
                    sys.stdout.flush()

                    if "Used Truck Flash Report" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "Used Truck Flash Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        subprocess.run(["/bin/bash", script_path("MLPScriptUSEDFlash.sh")])
                        payload = {
                            "status": "script_ran",
                            "message": "Used Truck Flash Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)

                    if "Freight Forecast OUTLOOK Report" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "Freight Forecast OUTLOOK Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        subprocess.run(["/bin/bash", script_path("MLPScriptFREIGHTOUTLOOK.sh")])
                        payload = {
                            "status": "script_ran",
                            "message": "Freight Forecast OUTLOOK Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)


                    if "U.S. Trailer Flash" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "U.S. Trailer Flash",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        subprocess.run(["/bin/bash", script_path("MLPScriptUSTrailer.sh")])
                        payload = {
                            "status": "script_ran",
                            "message": "U.S. Trailer Flash",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)

                    if "U.S. Used Truck Report" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "U.S. Used Truck Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        subprocess.run(["/bin/bash", script_path("MLPScriptUSED.sh")])
                        payload = {
                            "status": "script_ran",
                            "message": "U.S. Used Truck Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)

                    if "SOI N.A. Classes 5-8 Vehicles Flash Report" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "SOI N.A. Classes 5-8 Vehicles Flash Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        subprocess.run(["/bin/bash", script_path("MLPScriptNAC58.sh")])
                        payload = {
                            "status": "script_ran",
                            "message": "SOI N.A. Classes 5-8 Vehicles Flash Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)

                    if "Build & Retail Sales Flash Report" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "Build & Retail Sales Flash Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        subprocess.run(["/bin/bash", script_path("MLPScriptNABURS.sh")])
                        payload = {
                            "status": "script_ran",
                            "message": "Build & Retail Sales Flash Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)

                    if "Complete BURS Report" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "Complete BURS Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        subprocess.run(["/bin/bash", script_path("MLPScriptNACompleteBurs.sh")])
                        payload = {
                            "status": "script_ran",
                            "message": "Complete BURS Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)

                    if "N.A. Commercial Vehicle OUTLOOK Report" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "N.A. Commercial Vehicle OUTLOOK Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        subprocess.run(["/bin/bash", script_path("MLPScriptNACVOUTLOOK.sh")])
                        payload = {
                            "status": "script_ran",
                            "message": "N.A. Commercial Vehicle OUTLOOK Report",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)

                    if "Commercial Vehicle Preliminary Net Orders" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "Commercial Vehicle Preliminary Net Orders",
                            "timestamp": datetime.now().isoformat()
                        }
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
                            "status": "script_ran",
                            "message": "Email converted to JSON",
                            "timestamp": datetime.now().isoformat()
                        }    
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        time.sleep(5)
                        prelim_script_path = script_path("MLPScriptPrelim.sh")
                        try:
                            subprocess.run(["/bin/bash", prelim_script_path], check=True)
                            print("✅ MLP Prelim Script Ran Successfully")
                            payload = {
                                "status": "script_ran",
                                "message": "Commercial Vehicle Preliminary Net Orders",
                                "timestamp": datetime.now().isoformat()
                            }
                        except subprocess.CalledProcessError as e:
                            print(f"❌ ERROR: MLP Prelim Script Failed with error: {e}")
                            payload = {
                                "status": "error",
                                "message": "ERROR: MLP Prelim Script Failed",
                                "timestamp": datetime.now().isoformat()
                            }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)

                    if "U.S. Trailer Prelim Net Orders" in messagetocheck:
                        payload = {
                            "status": "script_starting",
                            "message": "U.S. Trailer Prelim Net Orders",
                            "timestamp": datetime.now().isoformat()
                        }
                        yield sse_event("ftp_event", payload)
                        sys.stdout.flush()
                        #this needs harcoded or set using above Path variable, also this needs to match JSON py directory for email location
                        eml_path = PRELIMS_PATH / 'U.S. Trailer Prelim Net Orders.eml'
                        eml_path.parent.mkdir(parents=True, exist_ok=True)
                        message.save_as_eml(to_path=eml_path)
                        time.sleep(5)
                        json_file_path = convert_email_to_json(eml_path, "U.S. Trailer Prelim Net Orders ")
                        print(f"✅ Email converted to JSON and saved at {json_file_path}")
                        prelim_script_path = script_path("MLPScriptPrelim.sh")
                        try:
                            subprocess.run(["/bin/bash", prelim_script_path], check=True)
                            print("✅ MLP Trailer Prelim Script Ran Successfully")
                            payload = {
                                "status": "script_ran",
                                "message": "U.S. Trailer Prelim Net Orders",
                                "timestamp": datetime.now().isoformat()
                            }
                        except subprocess.CalledProcessError as e:
                            print(f"❌ ERROR: MLP Trailer Prelim Script Failed with error: {e}")
                            payload = {
                                "status": "error",
                                "message": "ERROR: MLP Trailer Prelim Script Failed",
                                "timestamp": datetime.now().isoformat()
                            }
                        yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
                        sys.stdout.flush()
                        message.move(destination)
            else:
                payload = {
                    "status": "not_authenticated",
                    "message": auth_error or "Not Authenticated",
                    "timestamp": datetime.now().isoformat()
                }
                yield f'event: ftp_event\ndata: {json.dumps(payload)}\n\n'
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
    return Response(safe_events(), mimetype='text/event-stream', headers=headers)

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


