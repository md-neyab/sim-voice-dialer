#!/usr/bin/env python3
"""
SIM Voice Dialer — local dashboard (Python standard library only, koi pip package nahi).

Chalana:  python3 dashboard/app.py            (http://127.0.0.1:8080)
Env:      SVD_PORT (default 8080), SVD_HOST (default 127.0.0.1),
          SVD_SPOOL_DIR, SVD_CDR_PATH — engine ke env vars ke saath match karne ke liye.

Features:
  /            — status (Asterisk / spool / CDR / recording), campaign banane ka form
  /report      — Master.csv se number-wise report + summary + download
  Campaign Start/Pause/Resume/Stop — engine/dialer.py ko subprocess me chalata hai,
  progress ke liye engine ki likhi state JSON file padhta hai.
  SIMULATION mode bilkul saaf label ke saath — usme koi asal call NAHI lagti.
"""

import html
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(BASE, "engine", "dialer.py")
STATE_DIR = os.path.join(BASE, "state")
CAMPAIGNS_DIR = os.path.join(BASE, "campaigns")
AUDIO_DIR = os.path.join(BASE, "audio")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
CONTROL_FILE = os.path.join(STATE_DIR, "control.txt")
ASTERISK_SOUNDS = "/var/lib/asterisk/sounds"

sys.path.insert(0, os.path.join(BASE, "engine"))
import report as report_mod  # noqa: E402  (engine/report.py)

_proc: subprocess.Popen | None = None


# ------------------------------------------------------------------ helpers ----

def spool_dir() -> str:
    return os.environ.get("SVD_SPOOL_DIR", "/var/spool/asterisk/outgoing")


def cdr_path() -> str:
    return os.environ.get("SVD_CDR_PATH", "/var/log/asterisk/cdr-csv/Master.csv")


def read_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"status": "idle", "total": 0, "current_index": 0, "results": []}


def set_control(value: str):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(CONTROL_FILE, "w") as f:
        f.write(value)


def campaign_running() -> bool:
    return _proc is not None and _proc.poll() is None


def system_status() -> dict:
    msgwav_ast = os.path.join(ASTERISK_SOUNDS, "message.wav")
    msgwav_local = os.path.join(AUDIO_DIR, "message.wav")
    return {
        "asterisk": shutil.which("asterisk") is not None,
        "sox": shutil.which("sox") is not None,
        "spool_ok": os.path.isdir(spool_dir()),
        "spool": spool_dir(),
        "cdr_ok": os.path.exists(cdr_path()),
        "cdr": cdr_path(),
        "message_wav": msgwav_ast if os.path.exists(msgwav_ast)
                       else (msgwav_local if os.path.exists(msgwav_local) else None),
    }


def parse_multipart(body: bytes, content_type: str) -> tuple[dict[str, str], dict[str, tuple[str, bytes]]]:
    """Chhota sa multipart/form-data parser (stdlib se kaam chalane ke liye).
    Returns (text fields, {field: (filename, bytes)})."""
    fields: dict[str, str] = {}
    files: dict[str, tuple[str, bytes]] = {}
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):].strip('"')
    if not boundary:
        return fields, files
    delimiter = ("--" + boundary).encode()
    for chunk in body.split(delimiter):
        if not chunk or chunk in (b"--", b"--\r\n", b"\r\n"):
            continue
        if chunk.startswith(b"\r\n"):
            chunk = chunk[2:]
        if chunk.endswith(b"--"):
            chunk = chunk[:-2]
        if chunk.endswith(b"\r\n"):
            chunk = chunk[:-2]
        head, sep, content = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = head.decode("utf-8", "replace")
        name = filename = None
        for token in headers.replace("\n", " ").split(";"):
            token = token.strip()
            if token.startswith("name="):
                name = token[len("name="):].strip('"')
            elif token.startswith("filename="):
                filename = token[len("filename="):].strip('"')
        if name is None:
            continue
        if filename:
            files[name] = (filename, content)
        else:
            fields[name] = content.decode("utf-8", "replace")
    return fields, files


# -------------------------------------------------------------------- pages ----

CSS = """
body{font-family:system-ui,sans-serif;max-width:900px;margin:24px auto;padding:0 16px;color:#1c2430}
h1{font-size:22px} h2{font-size:17px;margin-top:28px}
.card{border:1px solid #d7dde6;border-radius:10px;padding:14px 16px;margin:12px 0;background:#fbfcfe}
.ok{color:#0a7d33;font-weight:600}.bad{color:#b3261e;font-weight:600}
table{border-collapse:collapse;width:100%} th,td{border:1px solid #d7dde6;padding:6px 8px;text-align:left;font-size:14px}
button,input[type=submit]{background:#175cd3;color:#fff;border:0;border-radius:8px;padding:8px 14px;cursor:pointer;font-size:14px}
button.gray{background:#5b6b7f} button.red{background:#b3261e}
textarea,input[type=text],input[type=number]{width:100%;padding:8px;border:1px solid #c3ccd8;border-radius:8px;box-sizing:border-box}
.sim{background:#fff7e0;border-color:#e3c964}
.bar{height:14px;background:#e8edf3;border-radius:7px;overflow:hidden}.bar>div{height:100%;background:#175cd3}
small{color:#5b6b7f}
"""


def page(title: str, body: str) -> bytes:
    doc = (f"<!doctype html><html><head><meta charset='utf-8'>"
           f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
           f"<title>{html.escape(title)}</title><style>{CSS}</style></head>"
           f"<body>{body}</body></html>")
    return doc.encode()


def home_page(msg: str = "") -> bytes:
    st = system_status()
    state = read_state()

    def mark(ok: bool) -> str:
        return "<span class='ok'>OK</span>" if ok else "<span class='bad'>NAHI MILA</span>"

    rows = "".join(
        f"<tr><td>{r['number']}</td><td>{html.escape(str(r['outcome']))}</td></tr>"
        for r in state.get("results", [])[-8:]
    )
    total = state.get("total", 0)
    done = len([r for r in state.get("results", [])])
    pct = int(done * 100 / total) if total else 0
    running = campaign_running()
    status_line = state.get("status", "idle")
    if state.get("simulate"):
        status_line += " (SIMULATION)"

    body = f"""
<h1>📞 SIM Voice Dialer</h1>
<p><small>Apni Airtel SIM se recorded-message calls — list, recording, report, sab yahin se.
<a href="/report">Report page →</a></small></p>
{f"<div class='card'><b>{html.escape(msg)}</b></div>" if msg else ""}

<h2>System status</h2>
<div class="card">
  Asterisk install: {mark(st['asterisk'])} &nbsp;|&nbsp; sox: {mark(st['sox'])}<br>
  Spool folder: {mark(st['spool_ok'])} <small>{html.escape(st['spool'])}</small><br>
  CDR file: {mark(st['cdr_ok'])} <small>{html.escape(st['cdr'])}</small><br>
  Recording (message.wav): {"<span class='ok'>READY</span> <small>" + html.escape(st['message_wav'] or '') + "</small>"
                             if st['message_wav'] else "<span class='bad'>ABHI NAHI</span> — neeche upload karo"}
</div>

<h2>Campaign progress</h2>
<div class="card">
  Status: <b>{html.escape(status_line)}</b> &nbsp;|&nbsp; {done}/{total} numbers
  <div class="bar"><div style="width:{pct}%"></div></div>
  <table><tr><th>Number</th><th>Result</th></tr>{rows or "<tr><td colspan=2>Abhi koi call nahi</td></tr>"}</table>
  <p>
    <form method="post" action="/control" style="display:inline">
      <button name="action" value="pause" class="gray" {"disabled" if not running else ""}>⏸ Pause</button>
      <button name="action" value="resume" class="gray" {"disabled" if not running else ""}>▶ Resume</button>
      <button name="action" value="stop" class="red" {"disabled" if not running else ""}>⏹ Stop</button>
    </form>
  </p>
</div>

<h2>1) Numbers ki list</h2>
<div class="card">
  <form method="post" action="/campaign" enctype="multipart/form-data">
    <p>CSV file upload karo (ek column me numbers): <input type="file" name="numbers_file" accept=".csv,.txt"></p>
    <p>— ya numbers yahan paste karo (ek line me ek number):</p>
    <textarea name="numbers_text" rows="6" placeholder="98110xxxxx&#10;98110xxxxx"></textarea>
    <p><label>Campaign ka naam: <input type="text" name="campaign_name" value="campaign"></label></p>
    <p><input type="submit" value="List save karo"></p>
  </form>
  <small>Saved lists <b>campaigns/</b> folder me rehti hain. Start karne se pehle list save karna zaroori hai.</small>
</div>

<h2>2) Recording</h2>
<div class="card">
  <form method="post" action="/recording" enctype="multipart/form-data">
    <p>Apni recording (mp3/wav, 20–30 sec) upload karo:
    <input type="file" name="recording_file" accept=".mp3,.wav,.ogg,.m4a"></p>
    <p><input type="submit" value="Recording set karo"></p>
  </form>
  <small>File sox se 8000 Hz mono <b>message.wav</b> me badli jayegi — pehle Asterisk ke sounds folder me,
  na ho paye to project ke audio/ folder me (install.sh ke baad Asterisk wala chalega).</small>
</div>

<h2>3) Campaign shuru karo</h2>
<div class="card">
  <form method="post" action="/start">
    <p><label>Kaunsi list chalani hai:
      <select name="campaign_dir">{campaign_options()}</select></label></p>
    <p><label>Do calls ke beech gap (sec): <input type="number" name="gap" value="10" min="0" max="300"></label></p>
    <p><label>Ring wait (sec): <input type="number" name="wait" value="40" min="10" max="90"></label></p>
    <p><label><input type="checkbox" name="simulate" value="1">
      <b>SIMULATION mode</b> — nakli campaign, <b>koi asal call NAHI lagegi</b> (test ke liye)</label></p>
    <p><input type="submit" value="🚀 Start Campaign" {"disabled" if running else ""}></p>
  </form>
  <small>Asli mode me call tabhi lagegi jab dongle laga ho, Asterisk chal raha ho aur list asal numbers ki ho.
  Roz 40–60 attempts se aage mat badho, aur sirf consent wale numbers par hi call karo.</small>
</div>
<script>
setInterval(async () => {{
  try {{ const r = await fetch('/status.json'); const s = await r.json();
    if (s.status !== '{state.get("status","idle")}' || s.done !== {done}) location.reload();
  }} catch(e) {{}}
}}, 4000);
</script>
"""
    return page("SIM Voice Dialer", body)


def campaign_options() -> str:
    opts = []
    try:
        for name in sorted(os.listdir(CAMPAIGNS_DIR), reverse=True):
            path = os.path.join(CAMPAIGNS_DIR, name)
            if os.path.isdir(path) and os.path.exists(os.path.join(path, "numbers.csv")):
                opts.append(f"<option value='{html.escape(name)}'>{html.escape(name)}</option>")
    except FileNotFoundError:
        pass
    return "".join(opts) or "<option value=''>— koi list saved nahi —</option>"


def report_page() -> bytes:
    rows = report_mod.load_rows(cdr_path())
    counts, latest = report_mod.summarise(rows)
    summary = " &nbsp;|&nbsp; ".join(f"<b>{html.escape(k)}</b>: {v}" for k, v in
                                     sorted(counts.items(), key=lambda kv: -kv[1]))
    trs = "".join(
        f"<tr><td>{html.escape(num)}</td><td>{html.escape(t)}</td>"
        f"<td>{html.escape(d)}</td><td>{html.escape(b)}</td></tr>"
        for num, (t, d, b) in sorted(latest.items())
    )
    body = f"""
<h1>📊 Report</h1>
<p><small><a href="/">← Dashboard</a> &nbsp;|&nbsp; CDR: {html.escape(cdr_path())}</small></p>
<div class="card">Kul calls: <b>{len(rows)}</b> &nbsp;|&nbsp; {summary or "abhi koi data nahi"}
&nbsp; <a href="/report.csv">⬇ report.csv download</a></div>
<table><tr><th>Number</th><th>Aakhri call</th><th>Result</th><th>Baat (sec)</th></tr>
{trs or "<tr><td colspan=4>Abhi tak koi call record nahi hua</td></tr>"}</table>
"""
    return page("Report — SIM Voice Dialer", body)


# ------------------------------------------------------------------ handler ----

class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[dashboard] " + fmt % args + "\n")

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8",
              extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _redirect_home(self, msg: str):
        self._send(200, home_page(msg))

    # ------------------------------------------------------------- GET ----
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self._send(200, home_page())
        elif path == "/status.json":
            state = read_state()
            data = {
                "status": state.get("status", "idle"),
                "simulate": state.get("simulate", False),
                "running": campaign_running(),
                "total": state.get("total", 0),
                "done": len(state.get("results", [])),
                "current_index": state.get("current_index", 0),
                "current_number": state.get("current_number"),
                "results": state.get("results", [])[-20:],
            }
            self._send(200, json.dumps(data).encode(), "application/json")
        elif path == "/report":
            self._send(200, report_page())
        elif path == "/report.csv":
            rows = report_mod.load_rows(cdr_path())
            _, latest = report_mod.summarise(rows)
            import csv as csvmod
            import io
            buf = io.StringIO()
            w = csvmod.writer(buf)
            w.writerow(["number", "last_call_time", "result", "talk_seconds"])
            for num in sorted(latest):
                t, d, b = latest[num]
                w.writerow([num, t, d, b])
            self._send(200, buf.getvalue().encode(), "text/csv",
                       {"Content-Disposition": "attachment; filename=report.csv"})
        else:
            self._send(404, page("404", "<h1>Page nahi mila</h1>"))

    # ------------------------------------------------------------ POST ----
    def do_POST(self):  # noqa: N802
        global _proc
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "")

        if ctype.startswith("multipart/form-data"):
            fields, files = parse_multipart(body, ctype)
        elif ctype.startswith("application/x-www-form-urlencoded"):
            qs = parse_qs(body.decode("utf-8", "replace"))
            fields = {k: v[0] for k, v in qs.items()}
            files = {}
        else:
            fields, files = {}, {}

        if path == "/campaign":
            self._save_campaign(fields, files)
        elif path == "/recording":
            self._save_recording(files)
        elif path == "/start":
            self._start_campaign(fields)
        elif path == "/control":
            action = fields.get("action", "")
            if action == "pause":
                set_control("pause")
                self._redirect_home("Campaign pause ho gaya (chal raha tha to).")
            elif action == "resume":
                set_control("run")
                self._redirect_home("Campaign resume ho gaya.")
            elif action == "stop":
                set_control("stop")
                self._redirect_home("Stop bhej diya — engine current call ke baad ruk jayega.")
            else:
                self._redirect_home("Samajh nahi aaya kya karna hai.")
        else:
            self._send(404, page("404", "<h1>Page nahi mila</h1>"))

    # --------------------------------------------------------- actions ----
    def _save_campaign(self, fields, files):
        name = "".join(ch for ch in fields.get("campaign_name", "campaign")
                       if ch.isalnum() or ch in "-_ ")[:40].strip() or "campaign"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        cdir = os.path.join(CAMPAIGNS_DIR, f"{stamp}-{name.replace(' ', '-')}")
        os.makedirs(cdir, exist_ok=True)
        target = os.path.join(cdir, "numbers.csv")

        if "numbers_file" in files and files["numbers_file"][1].strip():
            with open(target, "wb") as f:
                f.write(files["numbers_file"][1])
        elif fields.get("numbers_text", "").strip():
            lines = [ln.strip() for ln in fields["numbers_text"].splitlines() if ln.strip()]
            with open(target, "w") as f:
                f.write("number\n" + "\n".join(lines) + "\n")
        else:
            self._redirect_home("⚠️ Na CSV mili, na paste kiye numbers. List save NAHI hui.")
            return

        import csv as csvmod
        count = 0
        valid = []
        with open(target, newline="") as f:
            for row in csvmod.reader(f):
                if not row:
                    continue
                # dialer ka normalise use karke gin lo
                import importlib
                dialer = importlib.import_module("dialer")
                n = dialer.normalise(row[0])
                if n and n not in valid:
                    valid.append(n)
                    count += 1
        self._redirect_home(f"✅ List save ho gayi: <b>{os.path.basename(cdir)}</b> — "
                            f"{count} sahi numbers mile. Ab recording set karke Start karo.")

    def _save_recording(self, files):
        if "recording_file" not in files or not files["recording_file"][1]:
            self._redirect_home("⚠️ Koi recording file nahi mili.")
            return
        filename, data = files["recording_file"]
        os.makedirs(os.path.join(AUDIO_DIR, "uploads"), exist_ok=True)
        src = os.path.join(AUDIO_DIR, "uploads", os.path.basename(filename) or "recording")
        with open(src, "wb") as f:
            f.write(data)

        dest_ast = os.path.join(ASTERISK_SOUNDS, "message.wav")
        dest_local = os.path.join(AUDIO_DIR, "message.wav")
        dest = None
        note = ""
        if shutil.which("sox"):
            for candidate, where in ((dest_ast, "Asterisk sounds folder"),
                                     (dest_local, "project audio folder")):
                try:
                    subprocess.run(["sox", src, "-r", "8000", "-c", "1", candidate],
                                   check=True, capture_output=True, timeout=60)
                    dest, note = candidate, where
                    break
                except (subprocess.CalledProcessError, OSError, PermissionError):
                    continue
            if dest:
                self._redirect_home(f"✅ Recording taiyaar: <b>{dest}</b> ({note}) — 8000 Hz mono WAV.")
            else:
                self._redirect_home("⚠️ sox se conversion fail hua. File uploads me save hai; "
                                    "khud convert karo: <code>sox file -r 8000 -c 1 message.wav</code>")
        else:
            self._redirect_home("⚠️ is machine par <b>sox install nahi hai</b> — file uploads me save ho gayi hai. "
                                "Pehle <code>sudo ./install.sh</code> chalao, phir recording dobara upload karo.")

    def _start_campaign(self, fields):
        global _proc
        if campaign_running():
            self._redirect_home("⚠️ Ek campaign pehle se chal raha hai. Pehle use Stop karo.")
            return
        cdir_name = fields.get("campaign_dir", "")
        csv_path = os.path.join(CAMPAIGNS_DIR, cdir_name, "numbers.csv")
        if not cdir_name or not os.path.exists(csv_path):
            self._redirect_home("⚠️ Pehle koi list save karo, phir Start karo.")
            return
        simulate = fields.get("simulate") == "1"
        if not simulate:
            st = system_status()
            if not st["message_wav"]:
                self._redirect_home("⚠️ Pehle recording set karo — message.wav abhi ready nahi hai.")
                return
        try:
            gap = float(fields.get("gap", 10))
            wait = int(fields.get("wait", 40))
        except ValueError:
            gap, wait = 10, 40
        set_control("run")
        cmd = [sys.executable, ENGINE, "--csv", csv_path,
               "--gap", str(gap), "--wait", str(wait),
               "--state-file", STATE_FILE, "--control-file", CONTROL_FILE,
               "--log", os.path.join(CAMPAIGNS_DIR, cdir_name, "campaign.log")]
        if simulate:
            cmd.append("--simulate")
        env = dict(os.environ)
        _proc = subprocess.Popen(cmd, cwd=BASE, env=env,
                                 stdout=open(os.path.join(STATE_DIR, "engine.out"), "ab"),
                                 stderr=subprocess.STDOUT)
        mode = "SIMULATION (koi asal call nahi)" if simulate else "ASLI campaign"
        self._redirect_home(f"🚀 Campaign shuru ho gaya — mode: <b>{mode}</b>, list: {html.escape(cdir_name)}. "
                            "Progress upar apne aap refresh hoga.")


def main():
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(CAMPAIGNS_DIR, exist_ok=True)
    host = os.environ.get("SVD_HOST", "127.0.0.1")
    port = int(os.environ.get("SVD_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"SIM Voice Dialer dashboard → http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard band.")


if __name__ == "__main__":
    main()
