#!/usr/bin/env bash
# Copies the web app (earth/, as it is on disk now) to the EC2 server and (re)starts it.
#   deploy.sh IP [HOSTNAME]
# HOSTNAME is the address the site is served at (default: IP-with-dashes.sslip.io, which needs no DNS setup). The
# first deploy for a hostname gets its HTTPS certificate, which takes a few seconds. Run it again after any change.
set -euo pipefail

IP=${1:?usage: deploy.sh IP [HOSTNAME]}
SITE=${2:-${IP//./-}.sslip.io}
KEY_FILE=${KEY_FILE:-$HOME/.ssh/${NAME:-ignopticon}-ec2.pem}
EARTH=$(cd "$(dirname "$0")/../.." && pwd)
SSH=(ssh -i "$KEY_FILE" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 "ubuntu@$IP")

echo "== Waiting for the server to finish first-boot setup"
for i in $(seq 1 60); do
    status=0; err=$("${SSH[@]}" test -f /var/lib/ignopticon-ready 2>&1) || status=$?
    [[ $status == 0 ]] && break
    if [[ $status == 255 ]]; then  # ssh itself failed: can't connect or log in
        echo "   can't log in yet: ${err##*$'\n'}"
        if [[ $i -ge 6 && "$err" == *"Permission denied"* ]]; then
            echo "Wrong key or user: needs $KEY_FILE (the key pair chosen at launch) and an Ubuntu image (user 'ubuntu')." >&2; exit 1
        fi
    elif (( i % 6 == 0 )); then
        echo "   logged in; Docker setup not finished ($("${SSH[@]}" cloud-init status 2>/dev/null | tr -d '\n'))"
    fi
    if [[ $i == 60 ]]; then
        echo "Server not ready after 5 min. If cloud-init says 'done', the user data was not set at launch; run it now:" >&2
        echo "  ${SSH[*]} 'sudo bash -s' < $EARTH/deploy/ec2/user-data.sh" >&2
        exit 1
    fi
    sleep 5
done

echo "== Copying $EARTH"
# Same exclusions as .gitignore/.dockerignore; .env on the server is kept.
"${SSH[@]}" mkdir -p ignopticon/earth
rsync -az --delete -e "ssh -i $KEY_FILE" \
    --exclude node_modules --exclude tile-cache --exclude public/test --exclude deploy/ec2/.env \
    --exclude 'public/data/thermal/i4/*/*/8' --exclude 'public/data/thermal/i4/*/*/9' \
    "$EARTH/" "ubuntu@$IP:ignopticon/earth/"

echo "== Building and starting on the server, served at $SITE"
"${SSH[@]}" "cd ignopticon/earth/deploy/ec2 && echo SITE_ADDRESS=$SITE > .env && \
    docker compose up -d --build --remove-orphans && docker image prune -f >/dev/null"

echo "== Checking https://$SITE/"
for i in $(seq 1 24); do
    if curl -fsS -o /dev/null "https://$SITE/api/fires"; then
        echo "Live: https://$SITE/  (globe: https://$SITE/globe)"
        exit 0
    fi
    sleep 5
done
echo "Not answering over HTTPS yet. Logs: ${SSH[*]} 'cd ignopticon/earth/deploy/ec2 && docker compose logs --tail 50'" >&2
exit 1
