# Deploy TradeWiser Bot on Ubuntu

This guide shows how to deploy the TradeWiser trading bot on Ubuntu (tested on 22.04/24.04).

The bot is a FastAPI application + background trading loop. On Linux we use **systemd** instead of the Windows Service.

---

## 1. System Preparation

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git curl
```

Optional (if you want a newer Python):

```bash
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev
```

## 2. Create Dedicated User + Directories

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin tradewiser
sudo mkdir -p /opt/tradewiser /etc/tradewiser
sudo chown tradewiser:tradewiser /opt/tradewiser
sudo chmod 750 /opt/tradewiser
sudo chmod 700 /etc/tradewiser
```

## 3. Install the Application

```bash
cd /opt
sudo git clone https://your-repo/tradewiser_bot.git tradewiser   # or scp/rsync your code
cd /opt/tradewiser

sudo chown -R tradewiser:tradewiser .

# Create virtual environment
sudo -u tradewiser python3 -m venv venv

# Install dependencies
sudo -u tradewiser ./venv/bin/pip install --upgrade pip
sudo -u tradewiser ./venv/bin/pip install -r requirements.txt
```

> **Note**: You can ignore `windows_service.py`, `*.ps1`, and `build-*.ps1` — they are Windows-only.

## 4. Configuration (`.env`)

The bot now looks for the env file in this order (first match wins):

1. `/etc/tradewiser/.env`   ← **Recommended for production**
2. `/opt/tradewiser/.env`
3. Repository `.env` (dev)
4. Current directory

Create the secure system file:

```bash
sudo cp sample.env /etc/tradewiser/.env
sudo chown tradewiser:tradewiser /etc/tradewiser/.env
sudo chmod 600 /etc/tradewiser/.env

# Edit with your real keys
sudo -u tradewiser nano /etc/tradewiser/.env
```

**Minimum required**:

```env
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
BOT_API_KEY=super-long-random-string-here

# At least one LLM provider
GROQ_API_KEY=gsk_...
# or OPENAI_API_KEY=... or ANTHROPIC_API_KEY=...
```

## 5. Install systemd Service

```bash
sudo cp systemd/tradewiser.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Enable and start:

```bash
sudo systemctl enable tradewiser
sudo systemctl start tradewiser
sudo systemctl status tradewiser
```

View logs:

```bash
sudo journalctl -u tradewiser -f
```

## 6. Verify

```bash
# Health check (no auth needed)
curl http://localhost:8000/health/

# Full status (needs BOT_API_KEY)
curl -H "X-API-Key: $YOUR_BOT_API_KEY" http://localhost:8000/trades/news-strategy
curl -H "X-API-Key: $YOUR_BOT_API_KEY" http://localhost:8000/trades/strategy/status
```

## 7. Production Hardening (Recommended)

### Use the provided nginx config

```bash
sudo cp nginx/tradewiser.conf /etc/nginx/sites-available/tradewiser
sudo ln -sf /etc/nginx/sites-available/tradewiser /etc/nginx/sites-enabled/tradewiser
sudo rm -f /etc/nginx/sites-enabled/default || true
sudo nginx -t && sudo systemctl reload nginx
```

Edit `/etc/nginx/sites-available/tradewiser` and set your `server_name` and (optionally) enable HTTPS.

### Use the automated deploy script (recommended)

```bash
# One-time full deploy (run as root)
REPO_URL=https://github.com/your-org/tradewiser_bot.git sudo ./scripts/deploy-ubuntu.sh

# Later updates
sudo ./scripts/deploy-ubuntu.sh --update
```

The script handles:
- System packages
- User + directories
- Code clone/update
- venv + requirements
- .env placement
- systemd + nginx
## 8. Updates

### Option A — Automated CI/CD (recommended, one-time setup)

Push to `main` on GitHub → CI runs the tests → the Pi polls every ~2 min and
deploys automatically once CI is green. Rolls back on health-check failure.

**One-time activation, run on the Pi:**

```bash
cd /opt/tradewiser
sudo -u tradewiser git pull
sudo ./scripts/pi-cd-install.sh
```

What that installs:
- `tradewiser-deploy.service` (oneshot) + `tradewiser-deploy.timer` (every 2 min)
- `/etc/sudoers.d/tradewiser-deploy` — allows the `tradewiser` user to
  restart *only* the `tradewiser` service (nothing else)
- Runs one deploy pass immediately so you see whether it works

Verify:
```bash
systemctl status tradewiser-deploy.timer      # should show "active (waiting)"
journalctl -u tradewiser-deploy -f            # live deploy log
systemctl start tradewiser-deploy.service     # force a deploy now
```

Behavior on failure:
- CI red for a commit → Pi refuses to deploy it, logs why, waits for the next.
- Health check fails after restart → Pi auto-rolls back to the previous
  known-good commit, restarts the service, logs `ROLLED BACK to <sha>`.

Optional overrides — write to `/etc/tradewiser/deploy.env` (all optional):
```env
REQUIRE_GREEN_CI=0          # deploy without waiting for CI (not recommended)
HEALTH_TIMEOUT_SEC=60       # give restart more time on a slow Pi
BRANCH=feature-x            # deploy from a branch other than main
```

Uninstall:
```bash
sudo systemctl disable --now tradewiser-deploy.timer
sudo rm /etc/systemd/system/tradewiser-deploy.{service,timer}
sudo rm /etc/sudoers.d/tradewiser-deploy
sudo systemctl daemon-reload
```

### Option B — Manual pull (fallback)

```bash
cd /opt/tradewiser
sudo -u tradewiser git pull
sudo -u tradewiser ./venv/bin/pip install -r requirements.txt
sudo systemctl restart tradewiser
```

## 9. Useful Commands

| Action                    | Command |
|---------------------------|---------|
| Start / Stop / Restart    | `sudo systemctl {start,stop,restart} tradewiser` |
| Status                    | `sudo systemctl status tradewiser` |
| Logs (follow)             | `sudo journalctl -u tradewiser -f` |
| Check config loaded       | `sudo -u tradewiser /opt/tradewiser/venv/bin/python -c "from app.core.config import settings; print(settings)"` |
| Run manually (dev)        | `source venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` |

## Notes

- The trading loop runs inside the FastAPI lifespan — only **one worker** is allowed.
- All trading logic (RSI short-term options + NewsEvent multi-day) works the same on Linux.
- Short-term options and News strategies are both active if their flags are enabled in `.env`.

You now have a clean Ubuntu deployment using native systemd. 

For Windows → Linux migration you only need to change the service layer — the Python code is portable.

## 10. Using Gunicorn (Production Recommendation)

For better process management in production:

```bash
cd /opt/tradewiser
sudo -u tradewiser ./venv/bin/pip install gunicorn
```

Then edit the service file:

```bash
sudo nano /etc/systemd/system/tradewiser.service
```

Uncomment the gunicorn `ExecStart` line and comment out the uvicorn one.

```bash
sudo systemctl daemon-reload
sudo systemctl restart tradewiser
```

Gunicorn command used:
`gunicorn -k uvicorn.workers.UvicornWorker -w 1 -b 0.0.0.0:8000 app.main:app`
(Use only 1 worker because of the lifespan trading loop.)
