# Smart Electoral Management System

Web + IoT demo voting prototype (Django + ESP32). Academic demo only.
Government Polytechnic College for Women, Madurai-11 - Computer Engineering.

It implements the flow in `docs/flow_diagram.png` and the wiring in `docs/wiring_diagram.png`.

| Flow step | Where it is implemented |
|---|---|
| Voter registers / logs in | `/register/`, `/login/` |
| Name / address / constituency change, complaint | `/request/change/`, `/request/complaint/` |
| Outstation request (temporary address + proof, 30+ days before) | `/request/outstation/` (rule in `voting/forms.py`) |
| Admin review, quota max 10 per route, booth + time slot after approval | Admin panel `/admin/` actions (`voting/models.py`, `TempVotingRequest.approve`) |
| RFID scan -> server check (card valid, not voted, approved slot) | `POST /api/scan/` (`voting/services.py`) |
| LCD candidates -> keypad -> `#` confirm -> vote stored with transaction ID | ESP32 firmware + `POST /api/vote/` |
| Thermal printer staff record, buzzer + LEDs | ESP32 firmware (`printStaffRecord`, `okFeedback`, `errFeedback`) |
| Admin dashboard: candidate-wise, constituency-wise, local vs other-place, requests, slots, complaints | `/control-room/` |
| Bilingual How-to-Vote page with video + step image | `/how-to-vote/` |

## 1. Run the web server (Windows 11)

```
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python manage.py makemigrations voting
python manage.py migrate
python manage.py seed_demo
python manage.py test voting          (optional: runs the automated tests)
python manage.py runserver 0.0.0.0:8000
```

* `0.0.0.0` lets the ESP32 reach the server over Wi-Fi. Allow port 8000 in Windows Firewall (Private network).
* Find the PC's IP with `ipconfig` (IPv4 address). The PC and the ESP32 must be on the same Wi-Fi.
* `seed_demo` prints **BOOTH_ID** and **BOOTH_KEY** for each district. Copy one pair into the ESP32 code.

Logins created by `seed_demo`:

| Who | Username | Password |
|---|---|---|
| Administrator | `admin` | `admin123` |
| Demo voters | `DEMO001` ... `DEMO004` | `demo123` |

Change these before showing the project to anyone outside the lab.

## 2. Add your video and step image

The video and the Tamil step image are already in `voting/static/guide/` (replace them there to change them; keep the exact names):

* `how_to_vote.mp4` - the animation video
* `vote_steps.png` - the Tamil + English step-by-step image
* `video_poster.jpg` - optional thumbnail

The `/how-to-vote/` page shows both, plus the steps as text in English and தமிழ் tabs.
To change the step text, edit `voting/guide.py`.

## 3. Map a real RFID card to a voter

1. Flash the ESP32 and open the Arduino Serial Monitor at 115200 baud.
2. Scan the card. The monitor prints `Card UID: 04A1B2C3`.
3. In `/admin/` > Voters > open the voter > paste the UID into **RFID uid** > Save.

## 4. ESP32 booth machine

The ESP32 code is in the separate `esp32_arduino` package (open it in Arduino IDE). It needs the BOOTH_ID and BOOTH_KEY printed by `seed_demo` and the PC's IP address.
Pin map and wiring: `esp32_arduino/docs/wiring_diagram.png`.

## 5. Demo script (about 5 minutes)

1. Log in as `DEMO003` (Chennai voter). Open **Outstation voting request**, choose Madurai, enter a hostel address, upload any PDF/photo. Submit.
2. Log in as `admin` > `/admin/` > Outstation voting requests > tick the request > **Approve**. Show the quota column (`1/10 approved`) and that a booth + time slot were allotted.
3. Log back in as `DEMO003`. The dashboard shows booth and slot, and a notification.
4. Madurai booth machine: scan DEMO003's card, press a candidate number, press `#`. Slip prints, phone notification text appears in the server window.
5. Scan the same card again: ACCESS DENIED (already voted). To show the district rule, scan a different Chennai card (DEMO004, no approval) at the Madurai booth: ACCESS DENIED (wrong district).
6. Open `/control-room/`: candidate-wise, constituency-wise and **other-place** counts update.

## 6. Design decisions

* **Secret ballot.** `Vote` (the ballot) has no link to the voter. `VoteLog` records only *that* a voter voted, with a transaction ID. The printed staff slip never shows the chosen candidate.
* **Server decides everything.** The ESP32 only displays and forwards; the server re-checks the card, double voting and booth/district on every request.
* **Quota.** `TEMP_ROUTE_QUOTA = 10` in `electoral/settings.py`: at most 10 voters from one home district approved for each destination district. Approval fails with a clear message when the route is full.
* **30-day rule.** `TEMP_REQUEST_MIN_DAYS = 30`. The seed election is 45 days ahead, so requests are accepted.
* **Demo date check.** `ENFORCE_ELECTION_DATE = False` lets the booth accept votes on any day. Set True to accept votes only on the election date.
* **SMS.** `SMS_BACKEND = "console"` prints the notification in the server window and saves it on the voter dashboard. For real SMS set `SMS_BACKEND=fast2sms` and `FAST2SMS_API_KEY` (check the provider's current API before use).

## 7. Limits (demo, not a real election system)

HTTP on a local network, a shared device key per booth, SQLite, Django `DEBUG` on. A real system would need HTTPS, hardware-secured devices, audit logs and legal approval.
The LCD shows English only (the character LCD cannot display Tamil).
