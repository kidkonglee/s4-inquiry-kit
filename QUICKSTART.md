# Run the S4 Inquiry Lab on your Mac

Use a supported, security-updated Python. This version requires Flask and Waitress from requirements.txt; no npm or frontend build is needed.

## 1. Open Terminal in the project folder

If using the existing project on this Mac:

```sh
cd "$HOME/Desktop/全世界/codex/s4-inquiry-kit"
```

If using the ZIP, extract it first and change to the extracted `s4-inquiry-kit` folder instead. The archive excludes your teacher secret, student records and database; a new extraction starts fresh.

## 2. Initialize once, then start

```sh
python3 --version
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python tools/init.py
python3 tools/start_server.py
```

If `.env` already exists, skip `tools/init.py`; the initializer will preserve the existing file. Keep Terminal running while using the app. Press Control+C to stop the server.

Read [the security guide](guides/06-安全加固與使用.md) for the updated setup.

The deployed `docs/config.js` points to this Mac's ngrok HTTPS URL. Keep ngrok running when testing the published worksheet. Port 8000 serves only student APIs; the teacher dashboard uses the separate local port 8001.

The deployed student site is <https://kidkonglee.github.io/s4-inquiry-kit/>. It still requires the MacBook backend and ngrok to be running to receive student records.

## 3. Open both pages

- Student worksheet: <https://kidkonglee.github.io/s4-inquiry-kit/> (port 8000 now serves only student APIs)
- Teacher dashboard: <http://127.0.0.1:8001/teacher>

Open the teacher secret file locally:

```sh
open -a TextEdit .env
```

Copy the value after `ADMIN_TOKEN=` into the teacher login form. Keep this value private.

## 4. Try a real classroom session

1. In the teacher dashboard, create a class and enter student aliases, one per line.
2. Download the individual login codes immediately.
3. In the student page, expand “加入正式課堂” and enter the class ID and that student's code.
4. Write a prediction and plan, change the sliders, and run at least three trials.
5. Check the teacher dashboard for progress and experiment records. It refreshes about every 10 seconds.
6. Complete the worksheet, submit it, and grade it in the teacher dashboard.

Without joining a class, the student page is a local demonstration: its records are not sent to the teacher.

## 5. Let tablets connect through the internet

Keep the Python server running. In a second Terminal window:

```sh
python3 tools/start_tunnel.py
```

Then:

1. Set `apiBase` in `docs/config.js` to the actual ngrok HTTPS URL, without `/api`.
2. Publish the `docs/` folder through GitHub Pages: repository Settings → Pages → Deploy from a branch → main → /docs.
3. Add your Pages origin to `.env`, for example:

```text
ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000,https://YOUR_USERNAME.github.io
```

4. Restart the Python server after editing `.env`.
5. Give students the GitHub Pages worksheet URL and their individual classroom login details.

Only publish source files. Do not upload `.env`, `private/`, student records, or login-code lists. Keep the Mac awake and both Terminal processes running.

For the full deployment procedure, see [the Chinese deployment guide](guides/02-部署操作手冊.md).

## Verify or back up

Run backend tests:

```sh
python3 -m unittest discover -s tests -v
```

Back up the classroom database:

```sh
python3 tools/backup.py
```

Backups are stored in `private/backups/`.
