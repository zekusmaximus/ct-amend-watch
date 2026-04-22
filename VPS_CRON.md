## Run Every 1 Minute on a VPS (Improved)

This setup is more robust than a standard cron job. It uses **Systemd**, which ensures the script starts on boot, logs errors to the system journal, and maintains a strict 1-minute interval without overlapping.

### 1. Provision and Base Setup
Provision an Ubuntu 24.04 VPS and run:
```bash
sudo apt-get update && sudo apt-get install -y git python3 python3-venv python3-pip logrotate

cd ~
git clone <your-repo-url> ct-amend-watch
cd ct-amend-watch

python3 -m venv .venv
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install -r requirements.txt
sudo ./.venv/bin/python -m playwright install-deps chromium
./.venv/bin/python -m playwright install chromium
```

### 2. Configure Environment
```bash
cp .env.example .env
nano .env # Set your Telegram and Year (2026) variables
chmod 600 .env
```

### 3. Create the Systemd Service
This defines **what** to run. Replace `<your-user>` with your actual VPS username (usually `ubuntu` or `root`).

`sudo nano /etc/systemd/system/ct-watch.service`

```ini
[Unit]
Description=CT Amend Watcher Service
After=network.target

[Service]
Type=oneshot
User=<your-user>
WorkingDirectory=/home/<your-user>/ct-amend-watch
# Uses flock to prevent overlapping runs
ExecStart=/usr/bin/flock -n /tmp/ct-watch.lock /home/<your-user>/ct-amend-watch/.venv/bin/python /home/<your-user>/ct-amend-watch/watch_amend.py
```

### 4. Create the Systemd Timer
This defines **how often** to run.

`sudo nano /etc/systemd/system/ct-watch.timer`

```ini
[Unit]
Description=Run CT Amend Watcher every minute

[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
AccuracySec=1s
Persistent=true

[Install]
WantedBy=timers.target
```

### 5. Enable and Start
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now ct-watch.timer
```

### 6. Set Up Log Rotation
Since this runs 1,440 times a day, logs grow fast. Prevent disk-full errors:

`sudo nano /etc/logrotate.d/ct-watch`

```text
/home/<your-user>/ct-amend-watch/logs/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    notifempty
}
```

---

### Maintenance Commands

* **Check status:** `systemctl status ct-watch.timer`
* **View real-time logs:** `journalctl -u ct-watch.service -f`
* **Manually trigger a run:** `sudo systemctl start ct-watch.service`
* **Check next scheduled run:** `systemctl list-timers --all`

### Notes
* **Drift Prevention:** `OnUnitActiveSec=1min` triggers the script one minute after the *previous* run finishes, ensuring you never have two Playwright instances fighting for memory.
* **Persistence:** If the VPS reboots, the timer starts itself automatically.
