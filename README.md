# PulseBoard

> Lightweight serverless telemetry for open-source projects.

![AWS](https://img.shields.io/badge/AWS-Serverless-orange?logo=amazonaws)
![License](https://img.shields.io/badge/License-MIT-yellow)

Track real deployments of your open-source projects — not just GitHub clones. Know how many people actually run your software, which versions they use, what OS they're on, and where in the world they are.

**Zero-dependency client SDK. Fully serverless backend. Beautiful dashboard.**

## How It Works

```
Your App                    PulseBoard (AWS)
  │                              │
  │  POST /ingest               │
  │  {api_key, event, props} ──▶│──▶ Lambda ──▶ DynamoDB
  │                              │       │
  │  CloudFront adds             │       ▼
  │  country headers ──────────▶│   Aggregates
  │                              │       │
  │                              │       ▼
  │                          Dashboard (S3+CF)
  │                          Charts, stats, breakdowns
```

1. Your app sends a single HTTP POST on startup (15 lines of code)
2. CloudFront adds geographic headers (country, city) for free
3. Lambda validates the API key, writes the event, updates real-time aggregates
4. Dashboard shows trends, version distribution, OS breakdown, country map

## Features

- **Multi-project** — Register unlimited projects, each with its own API key
- **Real-time aggregates** — Daily, weekly, monthly counters updated on every ping
- **Geographic data** — Country detection via CloudFront headers (no GeoIP database needed)
- **Version tracking** — See which versions are in the wild
- **OS/Architecture breakdown** — macOS vs Linux vs Windows, x86 vs ARM
- **Beautiful dashboard** — Dark theme, Chart.js visualizations, responsive
- **Cognito auth** — Dashboard protected by AWS Cognito User Pool
- **Zero-dependency SDK** — Drop-in Python file, uses only stdlib (no pip install)
- **Fully serverless** — Lambda + DynamoDB + API Gateway + CloudFront + S3
- **One-command deploy** — SAM template deploys everything
- **Privacy-first** — Anonymous machine fingerprint, no PII, 1-year TTL auto-cleanup

## Architecture

| Component | AWS Service | Purpose |
|-----------|-------------|---------|
| Ingest API | Lambda + API Gateway | Receives telemetry pings |
| Dashboard API | Lambda + API Gateway | Stats, projects CRUD |
| Data store | DynamoDB (3 tables) | Events, Aggregates, Projects |
| Auth | Cognito User Pool | Dashboard login |
| CDN + Geo | CloudFront | Country headers, dashboard hosting, SSL |
| Static site | S3 | Dashboard HTML/JS/CSS |

**Estimated cost**: $0/month at low volume (all services within free tier).

## Prerequisites

- AWS CLI configured with credentials
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
- Python 3.12+

## Deploy

```bash
git clone <repo-url> && cd PulseBoard

# First deployment (interactive — asks for stack name, region, admin email)
./deploy.sh --guided

# Subsequent deployments
./deploy.sh

# Dashboard-only update (skip Lambda rebuild)
./deploy.sh --dashboard-only
```

The deploy script:
1. Builds Lambda functions via SAM
2. Deploys CloudFormation stack (DynamoDB, Lambda, API Gateway, Cognito, CloudFront, S3)
3. Creates a Cognito admin user (check your email for the temporary password)
4. Injects Cognito config into the dashboard
5. Uploads dashboard to S3
6. Prints the CloudFront URL

## Client Integration

### Python (zero dependencies)

Copy `sdk/pulseboard.py` into your project:

```python
from pulseboard import PulseBoard

pb = PulseBoard(
    api_key="pb_your_key_here",
    endpoint="https://your-cloudfront-url.cloudfront.net/ingest",
)

# On app startup
pb.startup(version="1.2.0")

# Track custom events
pb.track("generation_complete", model="nova_canvas", duration=7.2)
```

### Any language (HTTP POST)

```bash
curl -X POST https://your-cloudfront-url.cloudfront.net/ingest \
  -H "Content-Type: application/json" \
  -H "X-Api-Key: pb_your_key_here" \
  -d '{"event": "app_started", "properties": {"version": "1.0", "os": "Linux"}}'
```

### Opt-out

Set an environment variable to disable telemetry:

```bash
export MYAPP_TELEMETRY=false
```

Your app checks this before calling `pb.startup()`.

## Dashboard

The dashboard at your CloudFront URL provides:

- **Overview cards** — Today, 7-day, 30-day event counts + unique deployments
- **Timeseries chart** — Daily/weekly/monthly trends with events + unique lines
- **OS doughnut** — Distribution of operating systems
- **Version bars** — Which versions are running, with relative bar chart
- **Country list** — Geographic distribution with flag emojis
- **Recent events** — Live feed of the latest pings
- **Project settings** — API key management, SDK snippet, delete

## Project Structure

```
PulseBoard/
├── src/
│   ├── handlers/
│   │   ├── ingest.py       # POST /ingest — telemetry receiver
│   │   ├── projects.py     # CRUD for project registration
│   │   └── stats.py        # Aggregated stats for dashboard
│   └── shared/
│       ├── db.py           # DynamoDB table references
│       └── response.py     # API response helpers
├── dashboard/
│   ├── index.html          # Dashboard SPA
│   └── js/
│       ├── app.js          # Main app logic
│       ├── auth.js         # Cognito authentication
│       └── charts.js       # Chart.js configuration
├── sdk/
│   └── pulseboard.py       # Drop-in Python client (zero deps)
├── template.yaml           # SAM: all AWS infrastructure
├── deploy.sh               # One-command deployment
└── README.md
```

## Data Retention

Events have a 1-year TTL by default (configurable per project). Aggregates are kept indefinitely for long-term trend analysis. All data is anonymous — only a machine fingerprint hash is stored, never any PII.

## License

MIT
