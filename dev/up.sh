#!/usr/bin/env bash
# Start a dev Home Assistant with the integration mounted from this checkout.
# Edits under custom_components/wattnet apply after restarting the container
# (docker compose restart homeassistant). Credentials are entered in the UI.
set -euo pipefail
cd "$(dirname "$0")"

CLIENT_DIR="${WATTNET_CLIENT_DIR:-../../wattnet-client-python}"

# Ship the local checkout of wattnet-client (including uncommitted changes)
# into the image build context, to try changes of the client before they are
# published on PyPI.
rm -rf .client-src && mkdir .client-src
git -C "$CLIENT_DIR" ls-files -co --exclude-standard -z \
  | tar --null -C "$CLIENT_DIR" -T - -cf - | tar -xf - -C .client-src

docker compose up -d --build
python3 bootstrap.py
