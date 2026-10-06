#!/bin/bash
# Called by slskd on DownloadDirectoryComplete.
# Submits the finished release directory to wrtagweb for tagging + import.
set -euo pipefail

: "${WRTAG_WEB_API_KEY:?WRTAG_WEB_API_KEY not set in slskd container}"

WRTAG_URL="${WRTAG_URL:-http://wrtag.homelab.svc.cluster.local:7373}"

# Paths as slskd sees them vs as wrtag sees them
SLSKD_DOWNLOADS="/var/slskd/downloads"
WRTAG_DOWNLOADS="/hdd/media/music/.tmp/complete"

DIR=$(jq -r '.localDirectoryName // .LocalDirectoryName // empty' <<<"$SLSKD_SCRIPT_DATA")

if [[ -z "$DIR" ]]; then
  echo "wrtag-import: no localDirectoryName in event payload" >&2
  echo "payload: $SLSKD_SCRIPT_DATA" >&2
  exit 0
fi

# Translate the mount prefix
WRTAG_PATH="${DIR/#$SLSKD_DOWNLOADS/$WRTAG_DOWNLOADS}"

# Release dirs contain spaces and other reserved characters, so the form body
# must be percent-encoded. jq's @uri does this without needing curl.
ENCODED=$(jq -rn --arg v "$WRTAG_PATH" '$v|@uri')

# wget's --http-password only answers a 401 challenge, and it will not replay a
# POST body after that challenge. Send the Basic header preemptively instead.
AUTH=$(printf ':%s' "$WRTAG_WEB_API_KEY" | base64 -w0)

echo "wrtag-import: submitting $WRTAG_PATH"

if wget -qO- \
     --method=POST \
     --header="Authorization: Basic $AUTH" \
     --header="Content-Type: application/x-www-form-urlencoded" \
     --body-data="path=$ENCODED" \
     "$WRTAG_URL/op/move"; then
  echo "wrtag-import: queued ok"
else
  echo "wrtag-import: submit failed for $WRTAG_PATH" >&2
  exit 1
fi
