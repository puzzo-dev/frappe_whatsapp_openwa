### WhatsApp Dual Gateway

I-Varse Technologies NG

### Architecture

This app adds a second provider (`OpenWA`) to `frappe_whatsapp` so messages can be routed through either the **Meta Cloud API** or a **self-hosted OpenWA** gateway.

```
WhatsApp Message / Notification
        │
        ▼
┌─────────────────────────────┐
│  DualGateway override class  │
└─────────────────────────────┘
        │
        ▼
┌─────────────────────────────┐
│  Routing resolver (per      │
│  WhatsApp Account)          │
└─────────────────────────────┘
        │
   ┌────┴────┐
   ▼         ▼
OpenWA    Meta Cloud
session   API
  │
  ▼
Queue / Fallback
```

#### Routing modes

- **Account-level**: a `WhatsApp Account` is configured to use `meta` or `openwa` via `WhatsApp Account Provider Extension`.
- **OpenWA path**: the message is sent to the OpenWA gateway session. If the session is unhealthy, it is optionally queued; if the daily cap is reached, it falls back to Meta.
- **Meta path**: the upstream `frappe_whatsapp` Meta Cloud API flow is used. Per-account rate limiting is enforced before the call.

#### Fallback cascade

1. Healthy OpenWA session → send via OpenWA.
2. Unhealthy session + `queue_on_unhealthy` enabled → enqueue to `WhatsApp Outbound Queue`.
3. Daily cap reached → route to Meta.
4. Resolver error → route to Meta.
5. Meta rate limit reached → throw `TooManyRequestsError` (or the caller catches and retries).

#### Outbound queue and dead letters

- `WhatsApp Outbound Queue` stores messages with exponential backoff (`process_outbound_queue` runs every minute).
- After the maximum number of retries, the message is moved to `WhatsApp Fallback Log` (dead-letter) and logged to Error Log.
- Old terminal queue rows and fallback logs are purged weekly by scheduled data-retention tasks.

#### Monitoring

- `monitoring/health.py` checks all OpenWA sessions periodically.
- `monitoring/metrics.py` exposes a whitelisted API for gateway/session/queue metrics.
- Session status changes are logged to Error Log — no email notifications are sent.

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app frappe_whatsapp_openwa
```

### Configuration

- Add `sentry_dsn` to `site_config.json` and install `sentry-sdk` to enable structured error reporting.
- Configure `OpenWA Gateway Settings` (base URL, API key, rate limits).
- Configure `WhatsApp Account Provider Extension` per account (provider, session, queue, cap).

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/frappe_whatsapp_openwa
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### License

agpl-3.0
