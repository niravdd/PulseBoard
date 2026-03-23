# PulseBoard

> Lightweight serverless telemetry platform for open-source projects.

![AWS](https://img.shields.io/badge/AWS-Serverless-orange?logo=amazonaws)
![Python](https://img.shields.io/badge/Python-3.12+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-Apache%202.0-blue)

Track real deployments of your open-source projects — not just GitHub clones. Know how many people actually run your software, which versions they use, what OS they're on, where in the world they are, and how much they spend on cloud services.

**Zero-dependency client SDK. Fully serverless backend. Beautiful dashboard. GitHub traffic integration.**

---

## 1. What It Does

| Capability | How |
|---|---|
| **Deployment tracking** | Each running instance sends a startup ping with an anonymous machine fingerprint |
| **Version distribution** | See which versions are in the wild (v1.0 vs v1.2 vs v2.0) |
| **OS / Architecture** | macOS vs Linux vs Windows, x86 vs ARM |
| **Geographic distribution** | Country + city detection via CloudFront headers — no GeoIP database needed |
| **Feature usage** | Track which features are popular (e.g. "image_generation" vs "video_studio") |
| **Cost tracking** | Aggregate estimated cloud spend per deployment, per period |
| **Error tracking** | Log errors with type + message for failure analysis |
| **Performance timing** | Track operation durations (generation time, API latency) |
| **GitHub traffic** | Daily fetch of clones, views, stars, forks, referrers, popular pages — stored permanently (GitHub only keeps 14 days) |
| **Multi-project** | Register unlimited projects, each with its own API key |
| **Admin / Viewer roles** | Admins manage projects + invite users; Viewers see stats only |

## 2. How It Works

```
Your App                          PulseBoard (AWS)
  │                                    │
  │  POST /ingest                      │
  │  {api_key, event, props}  ────────▶│──▶ Lambda ──▶ DynamoDB
  │                                    │       │         (Events + Aggregates)
  │  CloudFront adds country  ────────▶│       │
  │  headers automatically             │       ▼
  │                                    │   Real-time counters
  │                                    │   (day / week / month)
  │                                    │
  │                                Dashboard (S3 + CloudFront)
  │                                Charts, breakdowns, GitHub traffic
  │
  │  GitHub API ◀──── Scheduled Lambda (daily)
  │  (clones, views, stars)    stores permanently in DynamoDB
```

1. Your app sends a single HTTP POST on startup (or any event)
2. CloudFront adds geographic headers (country, city) for free
3. Lambda validates the API key, writes the raw event, updates real-time aggregates
4. Dashboard reads from pre-computed aggregates — fast, no table scans
5. A daily scheduled Lambda fetches GitHub traffic and stores it permanently

## 3. Architecture

```
PulseBoard/
├── src/
│   ├── handlers/
│   │   ├── ingest.py            # POST /ingest — receives telemetry pings
│   │   ├── projects.py          # CRUD for projects + admin user management
│   │   ├── stats.py             # Aggregated stats for the dashboard
│   │   └── github_traffic.py    # Daily GitHub traffic fetcher (scheduled + manual)
│   └── shared/
│       ├── db.py                # DynamoDB table references
│       └── response.py          # API response helpers
├── dashboard/
│   ├── index.html               # Dashboard SPA (Tailwind CSS + Chart.js)
│   └── js/
│       ├── app.js               # Dashboard logic, routing, data loading
│       ├── auth.js              # Cognito authentication + token refresh
│       ├── charts.js            # Chart.js configuration
│       └── config.js            # Auto-generated: Cognito IDs (created by deploy.sh)
├── sdk/
│   └── pulseboard.py            # Drop-in Python client (zero dependencies)
├── template.yaml                # SAM: all AWS infrastructure
├── deploy.sh                    # One-command deployment
└── README.md
```

| AWS Service | Resource | Purpose |
|---|---|---|
| **DynamoDB** | `pulseboard-events-*` | Raw event storage (permanent) |
| **DynamoDB** | `pulseboard-aggregates-*` | Pre-computed counters (day/week/month + GitHub) |
| **DynamoDB** | `pulseboard-projects-*` | Project registry + API keys |
| **Lambda** | `pulseboard-ingest-*` | Receives telemetry pings (public, API key auth) |
| **Lambda** | `pulseboard-projects-*` | Project CRUD + Cognito user management |
| **Lambda** | `pulseboard-stats-*` | Dashboard data API |
| **Lambda** | `pulseboard-github-traffic-*` | Daily GitHub fetcher (EventBridge schedule) |
| **API Gateway** | REST API | Routes all endpoints |
| **Cognito** | User Pool + Groups | Admin/Viewer authentication |
| **CloudFront** | Distribution | CDN for dashboard + geographic headers for telemetry |
| **S3** | Dashboard bucket | Static site hosting |

**Estimated cost**: $0/month at low volume (all services within AWS free tier).

## 4. Prerequisites

- **AWS CLI** configured with valid credentials
- **[AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)** (v1.100+)
- **Python 3.12+**

## 5. Deployment

### 5.1 First-time deployment

```bash
git clone <repo-url> && cd PulseBoard

# Interactive deployment — asks for region, admin email, etc.
./deploy.sh --guided
```

The deploy script will:
1. Build Lambda functions via SAM
2. Deploy the CloudFormation stack (DynamoDB, Lambda, API Gateway, Cognito, CloudFront, S3)
3. Create a Cognito admin user — **check your email** for the temporary password
4. Inject Cognito config into the dashboard
5. Upload dashboard files to S3
6. Print the CloudFront URL, API URL, and Cognito details

### 5.2 Subsequent deployments

```bash
# Full redeploy (Lambda code + infrastructure changes)
./deploy.sh

# Dashboard-only (HTML/JS/CSS changes — skips Lambda build)
./deploy.sh --dashboard-only
```

### 5.3 First login

1. Go to the CloudFront URL printed by the deploy script
2. Sign in with your email and the temporary password from Cognito
3. Set a new permanent password on first login
4. Create your first project — you'll get an API key (`pb_...`)

## 6. Client Integration

### 6.1 Python (zero dependencies)

Copy `sdk/pulseboard.py` into your project — no `pip install` needed:

```python
from pulseboard import PulseBoard

pb = PulseBoard(
    api_key="pb_your_key_here",
    endpoint="https://your-cloudfront-url.cloudfront.net/ingest",
)

# Track app startup (auto-captures OS, arch, Python version, CPU count)
pb.startup(version="1.2.0")

# Track a generation with estimated cost
pb.generation(model="nova_canvas", cost_usd=0.06)

# Track feature usage
pb.feature("video_studio")

# Track errors
pb.error("moderation_blocked", "Nova Canvas rejected prompt")

# Track performance timing
pb.performance("image_generation", duration_ms=7200)

# Track any custom event
pb.track("custom_event", key1="value1", key2="value2")
```

**Auto-captured properties** (sent with every event):
- `os` — Operating system (Darwin, Linux, Windows)
- `os_version` — OS release version
- `arch` — CPU architecture (arm64, x86_64)
- `python` — Python version
- `cpu_count` — Number of CPU cores
- `hostname_hash` — 8-char hash of hostname (anonymous)

**Opt-out**: Check an environment variable before initializing:

```python
import os
if os.environ.get("MYAPP_TELEMETRY", "true").lower() != "false":
    pb = PulseBoard(api_key="...", endpoint="...")
    pb.startup(version="1.0")
```

### 6.2 Any language (HTTP POST)

```bash
curl -X POST https://your-cloudfront-url.cloudfront.net/ingest \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: pb_your_key_here" \
  -d '{
    "event": "app_started",
    "distinct_id": "optional-unique-machine-id",
    "properties": {
      "version": "1.0",
      "os": "Linux",
      "cost_usd": 0.06,
      "model": "gpt-4"
    }
  }'
```

The `X-Api-Key` header or `api_key` in the body are both accepted.

### 6.3 Unique deployment tracking

The SDK generates a stable anonymous machine fingerprint from `SHA-256(hostname + OS + machine + processor)`, truncated to 16 hex characters. This:
- Uniquely identifies each deployment (18.4 quintillion possible values)
- Is **not reversible** — you cannot recover the hostname or any input from the hash
- Persists across app restarts on the same machine
- Differs on every other machine

## 7. Dashboard

### 7.1 Overview

The dashboard at your CloudFront URL provides:

- **Stat cards** — Today, 7-day, 30-day, Lifetime event counts + unique deployments + cost
- **Period filter** — Today, 7 Days, 30 Days, 90 Days, 1 Year, Lifetime, Custom date range
- **Timeseries chart** — Daily/weekly/monthly events + unique deployments (Chart.js)
- **OS doughnut** — Distribution of operating systems (unique deployments)
- **Version bars** — Which versions are running
- **Country list** — Geographic distribution with flag emojis (unique deployments)
- **Models used** — Which AI/ML models are popular + Bedrock usage cost
- **Event types** — Breakdown by event name (startup, generation, error, etc.)
- **Recent events** — Scrollable feed with deployment ID, timestamp + timezone
- **Auto-refresh** — Every 5 minutes when idle (30s of no clicks/keyboard)
- **Manual refresh** — Click button with countdown timer

### 7.2 GitHub Traffic

When a GitHub repo is configured for a project:

- **Stars, forks, issues, contributors** — Always available
- **Clone + view counts (14-day)** — Requires PAT with traffic access
- **Daily clone/view chart** — Bar chart with historical data (stored permanently)
- **Top referrers** — Where traffic comes from
- **Popular pages** — Which pages get the most views
- **Daily scheduled fetch** — Lambda runs automatically, stores data in DynamoDB

GitHub only retains 14 days of traffic data. PulseBoard fetches daily and stores permanently.

### 7.3 GitHub PAT Setup

| PAT Type | Required Permissions | Traffic Access |
|---|---|---|
| **Classic PAT** | `repo` scope | Yes |
| **Fine-Grained PAT** | `Metadata: read` + `Administration: read` | Yes |
| **Fine-Grained PAT** | `Metadata: read` only | No (stars/forks work, clones/views don't) |

Create a PAT at:
- Fine-Grained: https://github.com/settings/tokens?type=beta
- Classic: https://github.com/settings/tokens/new

## 8. Security

### 8.1 Data privacy

- **No PII collected** — Only anonymous machine fingerprint (SHA-256 hash, irreversible)
- **No IP addresses stored** — CloudFront provides country/city, not IPs
- **No cookies or browser fingerprinting**
- **Data retained forever** by default — manual purge available per project

### 8.2 Access control

| Role | Can view stats | Can create/edit/delete projects | Can see API key | Can invite users |
|---|---|---|---|---|
| **Admin** | Yes | Yes | Yes (click to reveal) | Yes |
| **Viewer** | Yes | No | No (masked in API response) | No |

- Cognito User Pool with `Admins` and `Viewers` groups
- JWT `cognito:groups` claim checked on every write operation
- **API key**: Never returned to Viewers (masked at the backend, not just the UI)
- **GitHub PAT**: Never returned to anyone — only consumed by the backend Lambda
- Token auto-refresh via Cognito REFRESH_TOKEN_AUTH — sessions last 30 days

### 8.3 Ingest endpoint

The `/ingest` endpoint uses API key authentication (not Cognito) — lightweight for telemetry pings. The API key is validated against the Projects table via a DynamoDB GSI lookup.

## 9. API Reference

### 9.1 Ingest (public, API key auth)

| Method | Path | Description |
|---|---|---|
| POST | `/ingest` | Receive a telemetry event. Requires `X-Api-Key` header or `api_key` in body. |

### 9.2 Projects (Cognito auth)

| Method | Path | Description |
|---|---|---|
| GET | `/projects` | List all projects (API keys masked for Viewers) |
| POST | `/projects` | Create a new project (Admin only) |
| GET | `/projects/{id}` | Get project details (API key masked for Viewers) |
| PATCH | `/projects/{id}` | Update project — name, description, GitHub repo/token (Admin only) |
| DELETE | `/projects/{id}` | Delete project (Admin only) |
| POST | `/projects/{id}/regen-key` | Regenerate API key (Admin only) |

### 9.3 Stats (Cognito auth)

| Method | Path | Description |
|---|---|---|
| GET | `/stats/{id}/overview` | Summary: today, 7d, 30d, lifetime events + unique + cost |
| GET | `/stats/{id}/timeseries?period=daily&days=30` | Time series data for charts. `days=0` for lifetime. Supports `from=`/`to=` for custom range. |
| GET | `/stats/{id}/breakdown?days=30` | Breakdown by version, OS, country, event type, model. Unique counts for countries/OS/versions. |
| GET | `/stats/{id}/events?limit=100` | Recent raw events (paginated, newest first) |
| GET | `/stats/{id}/github` | GitHub traffic: stars, forks, clones, views, referrers, paths |
| DELETE | `/stats/{id}/purge?confirm=yes` | Delete all events + aggregates (Admin only, irreversible) |

### 9.4 Admin (Cognito auth)

| Method | Path | Description |
|---|---|---|
| GET | `/admin/users` | List all Cognito users with roles |
| POST | `/admin/invite` | Invite a new user (Admin or Viewer role) |

### 9.5 GitHub (Cognito auth)

| Method | Path | Description |
|---|---|---|
| POST | `/github/fetch` | Manually trigger GitHub traffic fetch for all projects |

## 10. Data Model

### Events Table

| Field | Type | Description |
|---|---|---|
| `project_id` | String (PK) | Project identifier |
| `timestamp_id` | String (SK) | ISO timestamp + unique suffix |
| `event_date` | String (GSI) | YYYY-MM-DD for date-range queries |
| `event_type` | String | Event name (app_started, generation, error, etc.) |
| `distinct_id` | String | Anonymous machine fingerprint (16 hex chars) |
| `country` / `city` | String | From CloudFront headers |
| `version` / `os` / `arch` | String | System info |
| `cost_usd` | Number | Estimated cloud cost for this event |
| `model` / `feature` / `error_type` | String | Indexed properties |
| `properties` | String (JSON) | Full properties blob |

### Aggregates Table

| Field | Type | Description |
|---|---|---|
| `pk` | String (PK) | Project ID |
| `sk` | String (SK) | Period key: `day#2026-03-23`, `week#2026-W12`, `month#2026-03`, `github#summary`, `ghday#2026-03-23` |
| `total_events` | Number | Atomic counter |
| `total_cost_usd` | Number | Accumulated cost |
| `unique_ids` | String Set | Set of distinct_ids for unique count |
| `versions` / `os_breakdown` / `countries` / `event_types` / `models` | Map | Breakdown counters per dimension |

## 11. Cost

At low-to-moderate volume (< 10,000 events/month):

| Service | Free Tier | Estimated Cost |
|---|---|---|
| Lambda | 1M requests/month | $0.00 |
| DynamoDB | 25 GB + 25 WCU/RCU | $0.00 |
| API Gateway | 1M calls/month | $0.00 |
| CloudFront | 1 TB transfer/month | $0.00 |
| S3 | 5 GB storage | $0.00 |
| Cognito | 50,000 MAU | $0.00 |

**Total: $0.00/month** within free tier.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
