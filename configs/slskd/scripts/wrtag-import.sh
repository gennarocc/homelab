#!/bin/bash
# Called by slskd on DownloadDirectoryComplete.
# Submits the finished release directory to wrtagweb for tagging + import.
set -euo pipefail

: "${WRTAG_WEB_API_KEY:?WRTAG_WEB_API_KEY not set in slskd container}"

WRTAG_URL="${WRTAG_URL:-http://wrtag.homelab.svc.cluster.local:7373}"

# Paths as slskd sees them vs as wrtag sees them
SLSKD_DOWNLOADS="/var/slskd/downloads"
WRTAG_DOWNLOADS="/hdd/media/music/.tmp/complete"

DIR=$(jq -r '.localDirectory // .LocalDirectory // empty' <<<"$SLSKD_SCRIPT_DATA")

if [[ -z "$DIR" ]]; then
  echo "wrtag-import: no localDirectory in event payload" >&2
  echo "payload: $SLSKD_SCRIPT_DATA" >&2
  exit 0
fi

# Translate the mount prefix
WRTAG_PATH="${DIR/#$SLSKD_DOWNLOADS/$WRTAG_DOWNLOADS}"

echo "wrtag-import: submitting $WRTAG_PATH"

wget -qO- \
  --method=POST \
  --http-user="" \
  --http-password="$WRTAG_WEB_API_KEY" \
  --header="Content-Type: application/x-www-form-urlencoded" \
  --body-data="path=$WRTAG_PATH" \
  "$WRTAG_URL/op/move" \
  && echo "wrtag-import: queued ok" \
  || echo "wrtag-import: submit failed for $WRTAG_PATH" >&2
