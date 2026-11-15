#!/usr/bin/env bash
# Configure the GPG-signed komawm APT repository served from Cloudflare R2.
set -euo pipefail

readonly CDN_APT="https://cdn.komawm.org/apt"
readonly KEYRING="/etc/apt/keyrings/komawm-archive-keyring.gpg"
readonly SOURCE_LIST="/etc/apt/sources.list.d/komawm.sources"

install -d -m 0755 /etc/apt/keyrings
curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  "${CDN_APT}/komawm-archive-keyring.gpg" -o "${KEYRING}.tmp"
chmod 0644 "${KEYRING}.tmp"
mv "${KEYRING}.tmp" "${KEYRING}"

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  "${CDN_APT}/komawm.sources" -o "${SOURCE_LIST}.tmp"
chmod 0644 "${SOURCE_LIST}.tmp"
mv "${SOURCE_LIST}.tmp" "${SOURCE_LIST}"

apt-get update
printf 'Configured komawm APT source (%s). Install with: sudo apt install koma\n' "${SOURCE_LIST}"
