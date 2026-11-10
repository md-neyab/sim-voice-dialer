# SIM Voice Dialer

**Kya karta hai:** Ek CSV list ke numbers par **tumhari apni Airtel SIM** se ek-ek karke call lagata hai. Call uthe to tumhari recording (20–30 sec) bajti hai, recording khatam hote hi phone khud kat jata hai, phir agla number. Aakhir me report milti hai — kis ki call uthi, kis ki nahi, kitni der baat hui.

```
CSV list ──► engine/dialer.py ──► Asterisk call files ──► chan_dongle ──► E173 modem ──► Airtel SIM ──► call
                                        │                                                    │
Dashboard (browser) ── start/pause/stop ┘                              report ◄── Master.csv (CDR)
```

## Kaunsi open-source cheezein jod kar bana hai

Ek ready-made repo is pure kaam ke liye mila hi nahi - isliye ye project 3 azmaye hue tukdo ko jodta hai:

| Tukda | Kaam | Kyun yahi |
|---|---|---|
| **Asterisk** (open-source PBX software) | Exchange — call lagana, recording chalana, report (CDR) | Duniya bhar me chalta hai, muft hai |
| **wdoekes/asterisk-chan-dongle** | Huawei USB modem ko Asterisk se jodne wala driver | Yahi fork **maintained** hai. Isi naam ka `bg111` wala repo 2016 se abandoned hai — wo mat lena |
| **Is repo ka engine + dashboard** | List padhna, ek-ek call-file dalna, browser se control, report | Ye chhota sa glue hi kahin ready nahi mila tha |

Reject hue naam, taaki dobara research na karna pade: VICIdial (call-center jitna bada, overkill), AVA/AI-Voice-Agent repos (AI baat-cheet ke liye hain, insaan uthaye to recording nahi chalate, dongle support docs me hai hi nahi), ICTDialer (FreeSWITCH/SIP-only — USB dongle chal hi nahi sakta), RasPBX (**uski website hijack ho chuki hai, wahan se kuch download MAT karna**).

## Hardware jo chahiye

1. **Huawei E173** USB 3G modem (voice-supported, unlocked, firmware 11.x) — ye modems band ho chuke hain, IndiaMART par ~₹999 me purana stock milta hai. Kharidne se pehle seller se voice + replacement chat me likhit me confirm karna.
2. **Ek alag Airtel SIM** — apna main number **kabhi nahi**. Unlimited-calling wala pack.
3. **Ek laptop/computer** jo calling ke time on rahe (purana bhi chalega; Raspberry Pi ki zaroorat nahi).

> ⚠️ **2G/3G wali baat:** E173 us zamane ka modem hai jab 3G chalta tha. Airtel ki 3G band ho chuki hai, isliye ye **Airtel ke 2G network** par chalega. **Jio par ye chal hi nahi sakta** (Jio sirf 4G hai) — isiliye Airtel hi sahi choice hai.

## Install — ek command

Dongle haath me aane ke baad, is folder me:

```bash
sudo ./install.sh
```

Ye khud kar dega: Asterisk + saare packages install, chan_dongle ka clone + compile, saari configs (dobara chalane par duplicate nahi hota), aur dashboard ki systemd service. Script ke checkpoints screen par dikhte rahenge — jahan ruke, wahi dikkat hai.

## Dongle aane par tumhare sirf 3 kaam

1. **SIM ka PIN band karo + ports bharo.** SIM ko pehle phone me daal kar PIN lock OFF karo (chan_dongle PIN wali SIM nahi chalata). Phir dongle laptop me lagakar `ls /dev/ttyUSB*` dekho aur wahi audio/data ports `/etc/asterisk/dongle.conf` me bhar do. Check: `sudo asterisk -rx "dongle show devices"` → State **Free** dikhna chahiye.
2. **Install chalao:** `sudo ./install.sh` aur phir `sudo systemctl restart asterisk`.
3. **Dashboard kholo:** http://127.0.0.1:8080 — list upload karo (ya numbers paste karo), recording upload karo (khud sahi format me badal jayegi), pehle **SIMULATION mode** me ek nakli campaign chalao, phir apna khud ka number list me daal kar pehli asli test call karo.

CLI se test call (dashboard se pehle):
```bash
sudo asterisk -rx "channel originate Dongle/dongle0/91<apna-number> extension s@testcall"
```
Call aaye aur "hello-world" sune → system taiyaar hai.

## Dashboard kaise use karein

- **List:** CSV upload ya paste — dono chalega. Lists `campaigns/` folder me save rehti hain.
- **Recording:** mp3/wav upload → sox se 8000 Hz mono `message.wav` bankar Asterisk ke sounds folder me jati hai (na ho paye to project ke `audio/` me — dashboard bata dega).
- **Start / Pause / Stop:** engine alag process me chalta hai; progress bar aur aakhri results apne aap refresh hote hain.
- **SIMULATION mode:** checkbox bilkul saaf label ke saath hai. Is mode me **koi asal call NAHI lagti** — engine nakli CDR rows banata hai, taaki bina dongle ke poora flow (campaign → report) test ho jaye.
- **Report page:** har number ki aakhri sthiti (ANSWERED / NO ANSWER / BUSY / FAILED) + baat ke seconds + `report.csv` download.

CLI se bhi sab chalta hai:
```bash
python3 engine/dialer.py --csv numbers.csv --dry-run     # sirf list check
python3 engine/dialer.py --csv numbers.csv --simulate    # nakli campaign (test)
python3 engine/dialer.py --csv numbers.csv               # ASLI campaign
python3 engine/report.py                                  # report
```

## Roz ke safety niyam (SIM ki sehat ke liye)

- Roz **40–60 attempts** se zyada nahi; hafte me 2–3 din bilkul band.
- **Sirf jaan-pehchan / consent wale numbers.** Ajnabee kharidi hui list = SIM aur number dono khatre me.
- Kuch incoming calls bhi aane do — sirf-outgoing wala pattern sabse jaldi pakda jata hai.
- Airtel ke **kisi bhi recharge se commercial robocalling legal nahi hoti** — saare unlimited packs "personal use" wale hain. Ye setup apne chhote, consent-wale use ke liye hai; bade promotional campaign ka legal rasta DLT + 140-series number hai, SIM nahi.

## Aam dikkat aur hal

| Dikkat | Hal |
|---|---|
| `dongle show devices` me kuch nahi dikha | Ports galat — `ls /dev/ttyUSB*` dobara dekho; modem dusre USB port me lagao |
| Device "Not connected" dikhe | SIM ka PIN on hai, ya SIM 2G par register nahi ho rahi — pehle phone me daal kar test karo |
| Call lagti hai, awaz nahi aati | Recording ka format galat — 8000 Hz mono WAV hi chalega (dashboard se upload karo, khud convert ho jayegi) |
| Call-file dalte hi gayab, call nahi lagi | File hamesha temp me likh kar move karni hoti hai — engine ye khud karta hai; haath se seedha spool me file mat banao |
| Awaz kati-phati aaye | Modem seedha laptop me lagao (saste USB hub se nahi); paas me bada electronic samaan na ho |
| Report khali hai | CDR on hai? `configs/cdr.conf.snippet` dekho — `[csv]` section chahiye; phir `asterisk -rx "cdr csv status"` |
| Dashboard par sox nahi mila | Pehle `sudo ./install.sh` chalao, phir recording dobara upload karo |

## Tests

```bash
python3 -m unittest discover -s tests -v
```
Simulate mode ki wajah se bina dongle/Asterisk ke bhi poora engine test ho jata hai.
GitHub Actions (`.github/workflows/ci.yml`) me do job hain: (1) ye unit tests, (2) **asli Asterisk + chan_dongle compile** — taaki driver ka compile har push par verify hota rahe.

## Folders

```
configs/    dongle.conf, extensions snippet, cdr snippet
engine/     dialer.py (campaign engine), report.py (CDR report)
dashboard/  app.py — sirf Python standard library, koi pip package nahi
audio/      recording ka ghar (README usi me)
tests/      unit tests
state/      campaign ki live state (dashboard/engine yahin se baat karte hain)
campaigns/  saved lists aur logs
install.sh  ek-command installer
```
