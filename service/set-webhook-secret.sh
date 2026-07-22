#!/usr/bin/env bash
# Usage: ./set-webhook-secret.sh whsec_xxx   — wires the Stripe webhook signing
# secret into .env and restarts the service so hosted purchases auto-upgrade to Pro.
set -e
SEC="$1"
[ -z "$SEC" ] && { echo "usage: $0 whsec_..."; exit 1; }
cd /opt/capturd-service
sed -i "s|^BILLING_WEBHOOK_SECRET=.*|BILLING_WEBHOOK_SECRET=$SEC|" .env
systemctl restart capturd-service
sleep 2
curl -s http://127.0.0.1:8099/api/me | python3 -c "import sys,json;print(\"webhook_configured:\", json.load(sys.stdin)[\"config\"][\"webhook_configured\"])"
