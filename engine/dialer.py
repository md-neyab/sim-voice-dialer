#!/usr/bin/env python3
"""
SIM Voice Dialer — campaign engine.

CSV list ke numbers ko EK-EK karke Asterisk call-file se dial karta hai:
call uthe to [broadcast] context recording chalata hai aur hangup kar deta hai.
Pichli call poori khatam hue bina agli call NAHI lagti (1 SIM = 1 call).

Modes:
    Real:      python3 dialer.py --csv numbers.csv
    Dry-run:   python3 dialer.py --csv numbers.csv --dry-run   (koi call nahi)
    Simulate:  python3 dialer.py --csv numbers.csv --simulate  (Asterisk nahi chahiye;
               nakli CDR rows SVD_CDR_PATH par banti hain — dashboard/report test ke liye)

Env overrides (testing ke liye):
    SVD_SPOOL_DIR  — Asterisk outgoing spool (default /var/spool/asterisk/outgoing)
    SVD_CDR_PATH   — Master.csv ka path (default /var/log/asterisk/cdr-csv/Master.csv)

Dashboard integration:
    --state-file   — har step par JSON state yahan likhi jati hai (progress ke liye)
    --control-file — is file me 'run' / 'pause' / 'stop' padh kar engine rukta/chalta hai
"""

import argparse
import csv
import json
import os
import random
import signal
import sys
import time
from datetime import datetime, timedelta

DEFAULT_SPOOL = "/var/spool/asterisk/outgoing"
DEFAULT_CDR = "/var/log/asterisk/cdr-csv/Master.csv"
DEVICE = "dongle0"  # dongle.conf me diya naam

_stop_requested = False


def get_spool_dir() -> str:
    return os.environ.get("SVD_SPOOL_DIR", DEFAULT_SPOOL)


def get_cdr_path() -> str:
    return os.environ.get("SVD_CDR_PATH", DEFAULT_CDR)


def _on_signal(signum, frame):  # noqa: ARG001
    global _stop_requested
    _stop_requested = True


# ---------------------------------------------------------------- numbers ----

def normalise(raw: str) -> str | None:
    """Number ko 91XXXXXXXXXX format me lao. Galat number par None."""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) == 10:
        digits = "91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return digits
    return None


def read_numbers(path: str) -> list[str]:
    """CSV se numbers padho, normalise karo, duplicates hatao (order bacha kar)."""
    numbers, seen = [], set()
    with open(path, newline="") as f:
        for row in csv.reader(f):
            if not row:
                continue
            n = normalise(row[0])
            if n and n not in seen:
                seen.add(n)
                numbers.append(n)
    return numbers


# -------------------------------------------------------------- call files ----

def build_call_file(number: str, wait_time: int) -> str:
    """Ek number ke liye Asterisk .call file ka content."""
    return (
        f"Channel: Dongle/{DEVICE}/{number}\n"
        f"Context: broadcast\n"
        f"Extension: s\n"
        f"Priority: 1\n"
        f"WaitTime: {wait_time}\n"
        f"MaxRetries: 0\n"
        f"CallerID: Dialer <{number}>\n"
    )


def queue_call(number: str, wait_time: int, spool: str | None = None) -> str:
    """Call file ko pehle temp me likho, phir atomically spool me move karo.
    (Seedha spool me aadhi file gayi to Asterisk use turant utha lega aur call fail hogi.)"""
    spool = spool or get_spool_dir()
    tmp = os.path.join(spool, f".tmp-dialer-{number}.call")
    final = os.path.join(spool, f"dialer-{number}.call")
    with open(tmp, "w") as f:
        f.write(build_call_file(number, wait_time))
    os.replace(tmp, final)  # same-filesystem atomic rename
    return final


def spool_busy(spool: str | None = None) -> bool:
    spool = spool or get_spool_dir()
    try:
        return any(name.endswith(".call") and not name.startswith(".tmp-")
                   for name in os.listdir(spool))
    except FileNotFoundError:
        return False


# ---------------------------------------------------------------- control ----

def read_control(control_file: str | None) -> str:
    if not control_file:
        return "run"
    try:
        with open(control_file) as f:
            value = f.read().strip().lower()
        return value if value in ("run", "pause", "stop") else "run"
    except FileNotFoundError:
        return "run"


def write_state(state_file: str | None, state: dict):
    if not state_file:
        return
    state = dict(state)
    state["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = state_file + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, state_file)


def interruptible_sleep(seconds: float, control_file: str | None) -> bool:
    """Chhote tukdo me sooyo taaki pause/stop turant asar kare.
    Agar stop aaya ho ya Ctrl-C pada ho to False return karo."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if _stop_requested or read_control(control_file) == "stop":
            return False
        time.sleep(min(0.25, max(0.0, end - time.monotonic())))
    return not _stop_requested


def wait_while_paused(control_file: str | None, state_file: str | None, state: dict) -> bool:
    """Pause state me ruk jao. Stop aaye to False."""
    paused_announced = False
    while read_control(control_file) == "pause" and not _stop_requested:
        if not paused_announced:
            state["status"] = "paused"
            write_state(state_file, state)
            paused_announced = True
        time.sleep(0.5)
    if paused_announced:
        state["status"] = "running"
        write_state(state_file, state)
    return not _stop_requested and read_control(control_file) != "stop"


# -------------------------------------------------------------- simulation ----

SIM_OUTCOMES = ["ANSWERED", "NO ANSWER", "BUSY", "FAILED"]
SIM_WEIGHTS = [55, 25, 12, 8]  # ANSWERED ~55%


def simulate_call(number: str, rng: random.Random, cdr_path: str) -> str:
    """Ek nakli call: chhota sa 'ring/playback' sleep, phir Master.csv-format ki row.
    Row ka format asal Asterisk Master.csv jaisa hi rakha hai taaki report.py
    aur dashboard bina badle is par bhi chalein."""
    outcome = rng.choices(SIM_OUTCOMES, weights=SIM_WEIGHTS, k=1)[0]
    start = datetime.now()
    ring = rng.uniform(2, 25) if outcome != "FAILED" else rng.uniform(0, 2)
    billsec = rng.randint(18, 32) if outcome == "ANSWERED" else 0
    duration = int(ring) + billsec
    answer = start + timedelta(seconds=int(ring)) if billsec else start
    end = start + timedelta(seconds=duration)
    fmt = "%Y-%m-%d %H:%M:%S"
    uniqueid = f"{start.timestamp():.3f}"
    row = [
        "",                              # accountcode
        "SIMULATOR",                     # src
        number,                          # dst
        "broadcast",                     # dcontext
        f'"Dialer" <{number}>',          # clid
        f"Dongle/{DEVICE}/{number}",     # channel
        "",                              # dstchannel
        "Playback" if billsec else "Dial",  # lastapp
        "message" if billsec else "",    # lastdata
        start.strftime(fmt),             # start
        answer.strftime(fmt),            # answer
        end.strftime(fmt),               # end
        str(duration),                   # duration
        str(billsec),                    # billsec
        outcome,                         # disposition
        "DOCUMENTATION",                 # amaflags
        uniqueid,                        # uniqueid
        "simulated",                     # userfield
    ]
    os.makedirs(os.path.dirname(os.path.abspath(cdr_path)), exist_ok=True)
    with open(cdr_path, "a", newline="") as f:
        csv.writer(f).writerow(row)
    return outcome


# -------------------------------------------------------------------- run ----

def run_campaign(numbers: list[str], *, gap: float, wait_time: int,
                 dry_run: bool, simulate: bool, seed: int | None,
                 state_file: str | None, control_file: str | None,
                 log_path: str) -> dict:
    rng = random.Random(seed)
    results: list[dict] = []
    state = {
        "status": "running",
        "simulate": simulate,
        "dry_run": dry_run,
        "total": len(numbers),
        "current_index": 0,
        "current_number": None,
        "results": results,
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }
    write_state(state_file, state)

    with open(log_path, "a") as log:
        for i, number in enumerate(numbers, 1):
            if _stop_requested or read_control(control_file) == "stop":
                state["status"] = "stopped"
                break
            if not wait_while_paused(control_file, state_file, state):
                state["status"] = "stopped"
                break

            state["current_index"] = i
            state["current_number"] = number
            write_state(state_file, state)
            when = datetime.now().strftime("%H:%M:%S")

            if dry_run:
                print(f"[{i}/{len(numbers)}] {when} DRY-RUN {number}")
                results.append({"number": number, "outcome": "DRY-RUN"})
                log.write(f"{when},{number},dry-run\n")
                log.flush()
                continue

            if simulate:
                # Nakli ring + recording ka time (asli 30-50 sec ki jagah ~1 sec)
                if not interruptible_sleep(rng.uniform(0.4, 0.9), control_file):
                    state["status"] = "stopped"
                    break
                outcome = simulate_call(number, rng, get_cdr_path())
                print(f"[{i}/{len(numbers)}] {when} SIMULATED {number} -> {outcome}")
                results.append({"number": number, "outcome": outcome})
                log.write(f"{when},{number},simulated,{outcome}\n")
                log.flush()
                if not interruptible_sleep(gap * 0.1, control_file):  # gap bhi chhota
                    state["status"] = "stopped"
                    break
                continue

            # ---- asal call ----
            while spool_busy():
                if _stop_requested or read_control(control_file) == "stop":
                    break
                time.sleep(2)
            if _stop_requested or read_control(control_file) == "stop":
                state["status"] = "stopped"
                break
            queue_call(number, wait_time)
            print(f"[{i}/{len(numbers)}] {when} Call lagayi: {number}")
            results.append({"number": number, "outcome": "QUEUED"})
            log.write(f"{when},{number},queued\n")
            log.flush()
            if not interruptible_sleep(gap, control_file):
                state["status"] = "stopped"
                break
        else:
            state["status"] = "completed"

    state["current_number"] = None
    state["finished_at"] = datetime.now().isoformat(timespec="seconds")
    write_state(state_file, state)
    return state


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ek-ek karke recorded-message calls lagao")
    ap.add_argument("--csv", required=True, help="Numbers wali CSV file")
    ap.add_argument("--gap", type=float, default=10, help="Do calls ke beech gap (sec), default 10")
    ap.add_argument("--wait", type=int, default=40, help="Ring wait time (sec), default 40")
    ap.add_argument("--limit", type=int, default=0, help="Sirf pehle N numbers (0 = sab)")
    ap.add_argument("--dry-run", action="store_true", help="Sirf list dikhao, call mat lagao")
    ap.add_argument("--simulate", action="store_true",
                    help="Asterisk ke bina nakli campaign — CDR rows SVD_CDR_PATH par banti hain")
    ap.add_argument("--seed", type=int, default=None, help="Simulate ke liye random seed")
    ap.add_argument("--state-file", default=None, help="Progress JSON yahan likho (dashboard ke liye)")
    ap.add_argument("--control-file", default=None, help="run/pause/stop control file (dashboard ke liye)")
    ap.add_argument("--log", default=None, help="Campaign log file ka path")
    args = ap.parse_args(argv)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    numbers = read_numbers(args.csv)
    if args.limit:
        numbers = numbers[: args.limit]
    if not numbers:
        print("List khali hai ya koi number sahi format me nahi mila.")
        return 1

    if not args.dry_run and not args.simulate:
        spool = get_spool_dir()
        if not os.path.isdir(spool):
            print(f"!! Spool folder nahi mila: {spool}")
            print("   Asterisk install hai? Pehle install.sh chalao, ya test ke liye --simulate use karo.")
            return 1

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = args.log or f"campaign-{stamp}.log"
    print(f"Total numbers: {len(numbers)} | gap: {args.gap}s | "
          f"dry-run: {args.dry_run} | simulate: {args.simulate}")
    print(f"Log: {log_path}\n")

    state = run_campaign(
        numbers, gap=args.gap, wait_time=args.wait,
        dry_run=args.dry_run, simulate=args.simulate, seed=args.seed,
        state_file=args.state_file, control_file=args.control_file,
        log_path=log_path,
    )
    print(f"\nCampaign khatam: status={state['status']}, "
          f"{len(state['results'])}/{len(numbers)} numbers try hue.")
    if not args.simulate and not args.dry_run:
        print("Report ke liye chalao:  python3 engine/report.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
