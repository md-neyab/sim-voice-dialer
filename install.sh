#!/usr/bin/env bash
# ============================================================
# SIM Voice Dialer — one-shot installer (Debian/Ubuntu, native)
# Chalana:  sudo ./install.sh
# Kya-kya karta hai:
#   1. Packages: asterisk (+dev), git, build tools, sox, python3
#   2. wdoekes/asterisk-chan-dongle clone + compile + install
#   3. Configs: dongle.conf, extensions.conf me broadcast block
#      (marker ke saath — dobara chalane par duplicate nahi), CSV CDR on
#   4. Dashboard ke liye systemd service
# Dobara chalana safe hai (idempotent-ish): jo ho chuka hai wo skip hoga.
# ============================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DONGLE_SRC="/usr/src/asterisk-chan-dongle"
EXT_FILE="/etc/asterisk/extensions.conf"
EXT_BEGIN="; >>> sim-voice-dialer (managed block) >>>"
EXT_END="; <<< sim-voice-dialer (managed block) <<<"
CDR_BEGIN="; >>> sim-voice-dialer csv cdr >>>"
CDR_END="; <<< sim-voice-dialer csv cdr <<<"

step() { echo; echo "==> $*"; }

if [[ "${EUID}" -ne 0 ]]; then
  echo "ERROR: ye script sudo se chalao:  sudo ./install.sh" >&2
  exit 1
fi

# ---------------------------------------------------------- 1. packages ----
step "1/5 Packages install ho rahe hain (asterisk, build tools, sox, python3)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git build-essential autoconf automake libtool pkg-config \
  sox python3 ca-certificates curl

if ! apt-get install -y -qq asterisk; then
  echo "ERROR: 'asterisk' package install nahi hua. Ubuntu par 'universe' repo on karke dobara try karo:" >&2
  echo "  sudo add-apt-repository universe && sudo apt-get update" >&2
  exit 1
fi
# asterisk-dev har jagah ek hi naam se nahi milta — guard ke saath try karo
if ! apt-get install -y -qq asterisk-dev; then
  echo "ERROR: 'asterisk-dev' nahi mila. chan_dongle ko Asterisk headers chahiye hote hain." >&2
  echo "      Debian/Ubuntu par aam taur par 'asterisk-dev' hi hota hai — repo check karke dobara chalao." >&2
  exit 1
fi
echo "Asterisk version: $(asterisk -V 2>/dev/null || echo 'unknown')"

# ASTVER: 'Asterisk 20.8.1' -> '20.8.1' (na mile to major number)
ASTVER="$(asterisk -V 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1 || true)"
if [[ -z "${ASTVER}" ]]; then
  ASTVER="$(asterisk -V 2>/dev/null | grep -oE '[0-9]+' | head -n1 || true)"
fi
if [[ -z "${ASTVER}" ]]; then
  echo "ERROR: Asterisk version parse nahi hua — chan_dongle compile nahi ho payega." >&2
  exit 1
fi
echo "chan_dongle is version ke liye compile hoga: ${ASTVER}"

# ------------------------------------------------------- 2. chan_dongle ----
step "2/5 chan_dongle (wdoekes fork — yahi maintained hai, bg111 wala purana/abandoned hai)..."
if [[ -d "${DONGLE_SRC}/.git" ]]; then
  echo "Pehle se clone hai — update kar raha hoon."
  git -C "${DONGLE_SRC}" pull --ff-only || echo "WARN: pull fail, purane code se aage badh raha hoon."
else
  git clone https://github.com/wdoekes/asterisk-chan-dongle "${DONGLE_SRC}"
fi
cd "${DONGLE_SRC}"
./bootstrap
./configure --with-astversion="${ASTVER}"
make -j"$(nproc)"
make install
echo "chan_dongle module install ho gaya."

# ------------------------------------------------------------- 3. configs ----
step "3/5 Configs lag rahe hain..."
mkdir -p /etc/asterisk /var/log/asterisk/cdr-csv /var/lib/asterisk/sounds

if [[ -f /etc/asterisk/dongle.conf ]]; then
  echo "dongle.conf pehle se hai — overwrite NAHI kiya (ports tumne set kiye honge)."
  echo "  Nayi template dekhni ho to: ${PROJECT_DIR}/configs/dongle.conf"
else
  cp "${PROJECT_DIR}/configs/dongle.conf" /etc/asterisk/dongle.conf
  echo "dongle.conf lag gaya — YAAD RAHE: isme audio/data PORTS apne modem ke hisaab se badalne hain."
fi

touch "${EXT_FILE}"
if grep -qF "${EXT_BEGIN}" "${EXT_FILE}"; then
  echo "extensions.conf me broadcast block pehle se hai — skip."
else
  {
    echo ""
    echo "${EXT_BEGIN}"
    cat "${PROJECT_DIR}/configs/extensions_broadcast.conf"
    echo "${EXT_END}"
  } >> "${EXT_FILE}"
  echo "extensions.conf me [broadcast] + [testcall] block jod diya."
fi

touch /etc/asterisk/cdr.conf
if grep -qF "${CDR_BEGIN}" /etc/asterisk/cdr.conf; then
  echo "cdr.conf me CSV CDR pehle se on hai — skip."
else
  {
    echo ""
    echo "${CDR_BEGIN}"
    echo "[csv]"
    echo "usegmtime=yes"
    echo "loguniqueid=yes"
    echo "loguserfield=yes"
    echo "${CDR_END}"
  } >> /etc/asterisk/cdr.conf
  echo "CSV CDR on kar diya (report: /var/log/asterisk/cdr-csv/Master.csv)."
fi

# ---------------------------------------------- 4. permissions/service ----
step "4/5 Dashboard service aur permissions..."
if id asterisk >/dev/null 2>&1; then
  usermod -a -G dialout asterisk || true
fi
mkdir -p "${PROJECT_DIR}/state" "${PROJECT_DIR}/campaigns" "${PROJECT_DIR}/audio/uploads"

if command -v systemctl >/dev/null 2>&1; then
  cat > /etc/systemd/system/sim-voice-dialer.service <<EOF
[Unit]
Description=SIM Voice Dialer dashboard
After=network.target asterisk.service
Wants=asterisk.service

[Service]
Type=simple
WorkingDirectory=${PROJECT_DIR}
ExecStart=/usr/bin/python3 ${PROJECT_DIR}/dashboard/app.py
Restart=on-failure
User=root

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload || true
  systemctl enable sim-voice-dialer.service || echo "WARN: service enable nahi hua — dashboard haath se chala sakte ho."
  echo "Dashboard service lag gaya:  sudo systemctl start sim-voice-dialer"
else
  echo "systemctl nahi mila — dashboard haath se chalao:  python3 dashboard/app.py"
fi

if systemctl is-system-running >/dev/null 2>&1; then
  systemctl restart asterisk || echo "WARN: asterisk restart nahi hua — 'sudo systemctl restart asterisk' khud chalao."
else
  echo "NOTE: systemd is VM/container me active nahi lag raha — laptop par asterisk restart khud hoga."
fi

# ------------------------------------------------------------ 5. finish ----
step "5/5 Ho gaya! Ab ye 3 kaam tumhare hain:"
cat <<EOF

  1. Dongle lagao, SIM ka PIN BAND karo (phone me daal kar),
     phir ports pehchano:   ls /dev/ttyUSB*
     aur wahi ports /etc/asterisk/dongle.conf me bharo.
     Check:  sudo asterisk -rx "dongle show devices"   -> State: Free

  2. Asterisk restart:     sudo systemctl restart asterisk

  3. Dashboard kholo:      http://127.0.0.1:8080
     (service start: sudo systemctl start sim-voice-dialer,
      ya haath se:   python3 ${PROJECT_DIR}/dashboard/app.py)
     - Pehle SIMULATION mode me ek nakli campaign chala kar dekho.
     - Phir apna number list me daal kar test call:
         sudo asterisk -rx "channel originate Dongle/dongle0/91<apna-number> extension s@testcall"

  Recording dashboard se upload karne par khud 8000 Hz mono message.wav ban jayegi.
EOF
