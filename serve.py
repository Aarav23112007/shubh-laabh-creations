"""Shubh Laabh Creations: local app server + sending service.

Run:   python serve.py            (then open http://localhost:5178)

* Serves the app (index.html) with caching turned off, so a refresh always shows the latest version.
* Sends invoice PDFs directly when config.json is filled in:
    - Email through Gmail (or any SMTP server)       -> POST /api/send-email
    - WhatsApp through the WhatsApp Business Cloud API -> POST /api/send-whatsapp
  GET /api/status tells the app which channels are set up.
config.json is read on every request, so edits work without a restart.
Only standard Python is used (no pip install needed).
"""
import base64, http.server, json, os, smtplib, ssl, sys, urllib.error, urllib.request, uuid
from email.message import EmailMessage

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5178
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
GRAPH = "https://graph.facebook.com/v21.0"


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def email_ready(c):
    e = c.get("email", {})
    return bool(e.get("address") and e.get("app_password"))


def whatsapp_ready(c):
    w = c.get("whatsapp", {})
    return bool(w.get("phone_number_id") and w.get("access_token"))


def send_email(c, d):
    e = c["email"]
    msg = EmailMessage()
    msg["From"] = f'{e.get("from_name", "Shubh Laabh Creations")} <{e["address"]}>'
    msg["To"] = d["to"]
    msg["Subject"] = d.get("subject", "Your invoice")
    msg.set_content(d.get("text", "Your invoice is attached."))
    msg.add_attachment(base64.b64decode(d["pdf"]), maintype="application", subtype="pdf",
                       filename=d.get("filename", "invoice.pdf"))
    host, port = e.get("smtp_host", "smtp.gmail.com"), int(e.get("smtp_port", 465))
    with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=30) as s:
        s.login(e["address"], e["app_password"].replace(" ", ""))
        s.send_message(msg)


def graph(c, path, body=None, data=None, headers=None):
    req = urllib.request.Request(f"{GRAPH}/{path}", data=data if data is not None else json.dumps(body).encode(),
                                 headers={"Authorization": f'Bearer {c["whatsapp"]["access_token"]}',
                                          **(headers or {"Content-Type": "application/json"})})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r)
    except urllib.error.HTTPError as err:
        detail = json.loads(err.read() or b"{}").get("error", {})
        raise RuntimeError(detail.get("error_user_msg") or detail.get("message") or f"WhatsApp API error {err.code}")


def send_whatsapp(c, d):
    w = c["whatsapp"]
    pid, filename, pdf = w["phone_number_id"], d.get("filename", "invoice.pdf"), base64.b64decode(d["pdf"])
    # 1) upload the PDF to WhatsApp
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in (("messaging_product", "whatsapp"), ("type", "application/pdf")):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f'Content-Type: application/pdf\r\n\r\n'.encode() + pdf + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    media = graph(c, f"{pid}/media", data=b"".join(parts),
                  headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    doc = {"id": media["id"], "filename": filename}
    # 2) send it: an approved template (needed to message a customer first), or a plain document
    tpl = w.get("template")
    if tpl:
        body = {"messaging_product": "whatsapp", "to": d["to"], "type": "template",
                "template": {"name": tpl, "language": {"code": w.get("template_language", "en")},
                             "components": [{"type": "header", "parameters": [{"type": "document", "document": doc}]},
                                            {"type": "body", "parameters": [{"type": "text", "text": d.get("invoice", "")}]}]}}
    else:
        body = {"messaging_product": "whatsapp", "to": d["to"], "type": "document",
                "document": {**doc, "caption": f'Invoice {d.get("invoice", "")} · {d.get("total", "")}'}}
    return graph(c, f"{pid}/messages", body)


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=HERE, **k)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Expires", "0")
        # lets the AI model use several CPU cores (multi-threaded WebAssembly) for faster scans
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Embedder-Policy", "credentialless")
        super().end_headers()

    def reply(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/config"):          # never serve the keys file
            return self.reply(404, {"ok": False})
        if self.path.startswith("/api/status"):
            c = load_config()
            return self.reply(200, {"ok": True, "email": email_ready(c), "whatsapp": whatsapp_ready(c)})
        return super().do_GET()

    def do_POST(self):
        c = load_config()
        try:
            d = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if self.path == "/api/send-email":
                if not email_ready(c):
                    raise RuntimeError("email is not set up in config.json")
                send_email(c, d)
                return self.reply(200, {"ok": True})
            if self.path == "/api/send-whatsapp":
                if not whatsapp_ready(c):
                    raise RuntimeError("WhatsApp is not set up in config.json")
                r = send_whatsapp(c, d)
                return self.reply(200, {"ok": True, "id": (r.get("messages") or [{}])[0].get("id")})
            return self.reply(404, {"ok": False, "error": "unknown endpoint"})
        except smtplib.SMTPAuthenticationError:
            return self.reply(400, {"ok": False, "error": "Gmail rejected the login. Use an App Password, not your normal password."})
        except Exception as e:
            return self.reply(400, {"ok": False, "error": str(e)})


if __name__ == "__main__":
    c = load_config()
    print(f"Shubh Laabh Creations running at http://localhost:{PORT}")
    print(f"  Email sending:    {'ON' if email_ready(c) else 'off (fill config.json)'}")
    print(f"  WhatsApp sending: {'ON' if whatsapp_ready(c) else 'off (fill config.json)'}")
    # 127.0.0.1 only: the sending service is not reachable from other devices on the network
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
