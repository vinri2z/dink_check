# 🏐 Dink Auto-Reservation Script

This is a simple Python script to **automatically book a field** on [Dink](https://dink.club). Ideal for recurring beach-volley games or quick grabs of popular time slots.

---

## ⚙️ Requirements

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (`brew install uv`)
- `./install.sh` runs `uv sync` to install dependencies and the package

---

## 🚀 Getting Started

1. **Clone the repo**
   ```bash
   git clone https://github.com/your-username/dink-reservation-bot.git
   cd dink-reservation-bot
   ```

2. **Run the installer**
   ```bash
   ./install.sh
   ```

3. **Run the script**
   ```bash
   uv run dink-check
   ```

   Or via module:

   ```bash
   uv run python -m dink_check
   ```
   
---

## 🔐 Environment Variables

Before running the script, make sure to set up your environment variables:

1. **Copy the example file**  
```bash
   cp .env.example .env
```

## 🛠️ Getting the Bearer Token with `mitmweb`

To authenticate your requests, you'll need to extract the Bearer token from the Dink app. Here's how:

### 1. Install `mitmproxy` with Homebrew

If you are using macOS, you can install `mitmproxy` (which includes `mitmweb`) via Homebrew:

```bash
    brew install mitmproxy
```

### 2. Start `mitmweb`

```bash
    mitmweb
```

This will start a proxy server and open the web interface at `http://localhost:8081`.

### 3. Configure Your Device

On your phone:

- Connect to the same Wi-Fi network.
- Set the HTTP proxy to your computer’s IP address and port `8080`.

### 4. Install the MITM Certificate (on your iPhone)

1. Open Safari on your iPhone and visit `http://mitm.it`.
2. Select `iOS` as your platform.
3. Download the certificate. This will prompt you to install the certificate on your device.
4. After the certificate is downloaded, go to **Settings** > **Profile Downloaded** and tap **Install**.
5. You'll be prompted to enter your device’s passcode. After entering it, tap **Install** again and then **Done**.

### 5. Trust the Certificate

To ensure the MITM proxy works correctly, you need to trust the certificate on your iPhone:

1. Go to **Settings** > **General** > **About** > **Certificate Trust Settings**.
2. Under **Enable full trust for root certificates**, find the **mitmproxy** certificate and toggle it on to trust it.

### 6. Open the Dink App and Login

Once the proxy is active and the certificate is installed, open the Dink app and log in. In the `mitmweb` interface, look for an API call with an `Authorization: Bearer` header.

### 7. Copy the Bearer Token and Fingerprint

- Find a request to an authenticated endpoint (e.g., `/api/reservations/availabilities`).
- Copy the `Authorization` header value (the part after `Bearer `).
- Copy the `X-Fingerprint` header value from the same request.
- Paste them into your `.env` file:

```env
BEARER_TOKEN=your_token_here
FINGERPRINT=your_x_fingerprint_here
```

Both values must come from the **same** request in mitmweb. Access tokens expire after about 30 minutes.

When booking, the bot sends `availabilities[].id` from each per-court entry (e.g. `PISTA 17`) as `field` on `POST /v2/reservations`. The API may occasionally return a venue-only row (`location` + `slots`); that `location.id` is not bookable—the bot skips it and stops if no court rows are present.

### 8. Automatic token refresh (optional)

See [docs/auth-refresh.md](docs/auth-refresh.md) for full details.

**Option A — email/password (recommended):** the bot signs in to Dink's
identity API and keeps the session alive on its own. Just set:

```env
DINK_EMAIL=you@example.com
DINK_PASSWORD=...
```

It signs in via `POST /api/identity/auth/sign-in`, then rotates the refresh
token via `POST /api/identity/auth/refresh`. `BEARER_TOKEN`/`FINGERPRINT` are
optional bootstrap. Verify without the booking loop:

```bash
uv run dink-check-refresh
```

**Option B — mitmweb session capture (fallback / recon):** keep the Dink app
open on your phone while running:

```bash
mitmweb -s scripts/capture_dink_session.py
```

The addon writes `.dink_session.json` (token + fingerprint, reloaded by the bot
on expiry / `401 INVALID_TOKEN`) and `.dink_auth_capture.jsonl` (full auth-flow
recon across `dink.social` + Firebase hosts).