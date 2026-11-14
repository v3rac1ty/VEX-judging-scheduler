# VEX-judging-scheduler
Suggests judging interview times for VEX teams, tracks check-in, and tells the emcee who still needs an interview.

## Windows: just double-click (no setup)
1. Download `VEX-Judging-Scheduler.exe` from the [latest release](https://github.com/v3rac1ty/VEX-judging-scheduler/releases/latest).
2. Make a new folder for it (for example `Documents\Judging`) and move the `.exe` there.
   Your event data is saved in a `data` folder next to it.
3. Double-click the `.exe`. A window opens with the web address and the admin PIN, and your
   browser opens the app.
4. The first time, Windows may ask:
   - **"Windows protected your PC"**: click **More info**, then **Run anyway**.
   - **Windows Firewall**: click **Allow access** so other devices on the network can connect.
5. Other devices (phones, laptops) on the same network open the address shown in the window,
   for example `http://192.168.1.230:8000`.

Keep the window open while you use the app. Closing it stops the server.

### Building the .exe yourself
Needs Python 3.10 or newer. In the project folder, double-click `build_exe.bat` (or run it from a
terminal). The result is `dist\VEX-Judging-Scheduler.exe`.

## Quick start (from source)
1. Install dependencies:
	```bash
	python -m venv .venv
	source .venv/bin/activate
	pip install -r requirements.txt
	```
2. Run the app:
	```bash
	uvicorn app:app --reload
	```
3. Open the UI at http://127.0.0.1:8000

## How it works
The page has four tabs:

- **Setup** (admin only): load the Tournament Manager match file, check the judging window, then make and print the suggested times.
  - Interviews open at the first qualification match. The close time is filled in so every team fits (judge pairs × minutes per interview), and never later than the start of eliminations.
  - A lunch break is detected from the match schedule. By default no interviews are suggested during lunch; turn on **Hold interviews during lunch** to judge through it.
- **Check-in**: every team in team-number order, with its suggested time and judge pair. Mark teams **Interviewed** or **No-show**. Times are suggestions: interviews are first come, first served.
- **Announce**: teams that still need an interview, in large type for the emcee, plus a ready-to-read announcement. One hour before interviews close, every device shows a reminder.
- **No-shows**: suggests new times for no-show teams in the gaps between their matches, using the match schedule loaded in Setup. There is no separate final-schedule upload: interviews end before eliminations start.

## Admin PIN
Setup, printing, Start over and no-show times are locked behind an admin PIN. Anyone can open
the page and check teams in; click **Admin sign-in** in the top right to unlock the rest.
The PIN is 4 to 12 digits (numbers only, so phones show a number pad).

### Find the current PIN
- It's printed in the server window when the server starts, for example `Admin PIN: 482913`.
- Or open `data/admin_pin.txt` in the project folder.

If you never set one, a random 6-digit PIN is created the first time the server runs.

### Change the PIN
Pick one of these, then **restart the server** (stop it with Ctrl+C and start it again).
Changing the PIN signs every device out of admin.

**Option 1: edit the PIN file (simplest)**
1. Open `data/admin_pin.txt` in a text editor.
2. Replace the number with your new PIN, for example `2468`, and save.
3. Restart the server.

If the file doesn't hold 4 to 12 digits, the server replaces it with a new random PIN and
prints that one instead.

**Option 2: set it in a `.env` file**
1. In the project folder, copy `.env.example` to a new file named `.env`
   (Windows: `copy .env.example .env`; Mac/Linux: `cp .env.example .env`).
2. Find the line `# JUDGING_ADMIN_PIN=482913`, remove the `#` and put in your PIN:
   `JUDGING_ADMIN_PIN=2468`
3. Restart the server.

When `JUDGING_ADMIN_PIN` is set, it wins and `data/admin_pin.txt` is ignored. The server then
won't print the PIN at startup, so keep a note of it.

### Forgot the PIN?
Open `data/admin_pin.txt`, or check `.env` if you set it there. To start fresh, delete
`data/admin_pin.txt` and restart the server: a new random PIN is printed in the server window.

Neither `.env` nor `data/admin_pin.txt` is committed to git.
