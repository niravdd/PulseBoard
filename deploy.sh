#!/bin/bash
set -euo pipefail

# PulseBoard — One-command deployment
# Prerequisites: AWS CLI, AWS SAM CLI, configured AWS credentials
#
# Usage:
#   ./deploy.sh                    # First deploy (interactive)
#   ./deploy.sh --guided           # Re-run guided deployment
#   ./deploy.sh --stage prod       # Deploy to specific stage
#   ./deploy.sh --dashboard-only   # Just upload dashboard files

STAGE="${STAGE:-prod}"
DASHBOARD_ONLY=false

# Parse args
while [[ $# -gt 0 ]]; do
    case $1 in
        --stage) STAGE="$2"; shift 2;;
        --dashboard-only) DASHBOARD_ONLY=true; shift;;
        --guided) GUIDED=true; shift;;
        *) echo "Unknown arg: $1"; exit 1;;
    esac
done

echo "=========================================="
echo "  PulseBoard Deployment (stage: $STAGE)"
echo "=========================================="

if [ "$DASHBOARD_ONLY" = true ]; then
    echo ""
    echo "Uploading dashboard only..."
else
    # Check prerequisites
    command -v sam >/dev/null 2>&1 || { echo "ERROR: AWS SAM CLI not found. Install: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"; exit 1; }
    command -v aws >/dev/null 2>&1 || { echo "ERROR: AWS CLI not found. Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"; exit 1; }

    # Build
    echo ""
    echo "Building Lambda functions..."
    sam build --template template.yaml

    # Deploy
    echo ""
    echo "Deploying stack..."
    if [ "${GUIDED:-false}" = true ] || [ ! -f samconfig.toml ]; then
        sam deploy --guided --stack-name "pulseboard-${STAGE}" --parameter-overrides "Stage=${STAGE}"
    else
        sam deploy --stack-name "pulseboard-${STAGE}" --parameter-overrides "Stage=${STAGE}" --no-confirm-changeset
    fi
fi

# Resolve region from samconfig.toml or AWS CLI default
REGION=$(grep -m1 'region' samconfig.toml 2>/dev/null | sed 's/.*= *"\(.*\)"/\1/' || aws configure get region 2>/dev/null || echo "us-east-1")
echo "Using region: ${REGION}"

# Get stack outputs
echo ""
echo "Fetching stack outputs..."
OUTPUTS=$(aws cloudformation describe-stacks --stack-name "pulseboard-${STAGE}" --region "${REGION}" --query "Stacks[0].Outputs" --output json 2>/dev/null || echo "[]")

CLOUDFRONT_URL=$(echo "$OUTPUTS" | python3 -c "import sys,json; [print(o['OutputValue']) for o in json.load(sys.stdin) if o['OutputKey']=='CloudFrontUrl']" 2>/dev/null || echo "")
DASHBOARD_BUCKET=$(echo "$OUTPUTS" | python3 -c "import sys,json; [print(o['OutputValue']) for o in json.load(sys.stdin) if o['OutputKey']=='DashboardBucket']" 2>/dev/null || echo "")
USER_POOL_ID=$(echo "$OUTPUTS" | python3 -c "import sys,json; [print(o['OutputValue']) for o in json.load(sys.stdin) if o['OutputKey']=='UserPoolId']" 2>/dev/null || echo "")
CLIENT_ID=$(echo "$OUTPUTS" | python3 -c "import sys,json; [print(o['OutputValue']) for o in json.load(sys.stdin) if o['OutputKey']=='UserPoolClientId']" 2>/dev/null || echo "")
API_URL=$(echo "$OUTPUTS" | python3 -c "import sys,json; [print(o['OutputValue']) for o in json.load(sys.stdin) if o['OutputKey']=='ApiUrl']" 2>/dev/null || echo "")
INGEST_URL=$(echo "$OUTPUTS" | python3 -c "import sys,json; [print(o['OutputValue']) for o in json.load(sys.stdin) if o['OutputKey']=='IngestEndpoint']" 2>/dev/null || echo "")

if [ -z "$DASHBOARD_BUCKET" ]; then
    echo "ERROR: Could not get stack outputs. Is the stack deployed?"
    exit 1
fi

# Inject config into dashboard
echo ""
echo "Injecting configuration into dashboard..."
CONFIG_JS="window.PB_CONFIG = {
    userPoolId: '${USER_POOL_ID}',
    clientId: '${CLIENT_ID}',
    region: '${REGION}',
    apiBase: '',
};"
echo "$CONFIG_JS" > dashboard/js/config.js

# Upload dashboard to S3
echo "Uploading dashboard to s3://${DASHBOARD_BUCKET}..."
aws s3 sync dashboard/ "s3://${DASHBOARD_BUCKET}/" \
    --delete \
    --cache-control "max-age=3600" \
    --exclude ".DS_Store"

# Set no-cache for HTML and JS (so updates are picked up immediately)
aws s3 cp "s3://${DASHBOARD_BUCKET}/index.html" "s3://${DASHBOARD_BUCKET}/index.html" \
    --content-type "text/html" --cache-control "no-cache, no-store, must-revalidate" --metadata-directive REPLACE
for jsfile in $(aws s3 ls "s3://${DASHBOARD_BUCKET}/js/" --recursive | awk '{print $4}'); do
    aws s3 cp "s3://${DASHBOARD_BUCKET}/${jsfile}" "s3://${DASHBOARD_BUCKET}/${jsfile}" \
        --content-type "application/javascript" --cache-control "no-cache, no-store, must-revalidate" --metadata-directive REPLACE
done

# Invalidate CloudFront cache
CF_DIST_ID=$(echo "$OUTPUTS" | python3 -c "
import sys,json
url = [o['OutputValue'] for o in json.load(sys.stdin) if o['OutputKey']=='CloudFrontUrl'][0]
# Extract distribution ID is not directly available — invalidate via AWS CLI
" 2>/dev/null || echo "")
# Try to get distribution ID from the domain
CF_DOMAIN=$(echo "$CLOUDFRONT_URL" | sed 's|https://||')
CF_DIST_ID=$(aws cloudfront list-distributions --query "DistributionList.Items[?DomainName=='${CF_DOMAIN}'].Id" --output text 2>/dev/null || echo "")
if [ -n "$CF_DIST_ID" ]; then
    echo "Invalidating CloudFront cache (${CF_DIST_ID})..."
    aws cloudfront create-invalidation --distribution-id "$CF_DIST_ID" --paths "/*" > /dev/null 2>&1 || true
fi

echo ""
echo "=========================================="
echo "  Deployment Complete!"
echo "=========================================="
echo ""
echo "  Dashboard:  ${CLOUDFRONT_URL}"
echo "  API:        ${API_URL}"
echo "  Ingest:     ${INGEST_URL}"
echo ""
echo "  Cognito:"
echo "    User Pool: ${USER_POOL_ID}"
echo "    Client ID: ${CLIENT_ID}"
echo ""
echo "  Check your email for the temporary password."
echo "  Sign in at: ${CLOUDFRONT_URL}"
echo ""
