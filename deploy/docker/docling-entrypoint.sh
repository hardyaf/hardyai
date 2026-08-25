#!/bin/sh
set -eu

key_path=/run/secrets/docling_api_key
if [ ! -f "$key_path" ] || [ -L "$key_path" ]; then
  echo "Docling API key file is unavailable or unsafe" >&2
  exit 1
fi
DOCLING_SERVE_API_KEY=$(tr -d '\r\n' < "$key_path")
if [ -z "$DOCLING_SERVE_API_KEY" ]; then
  echo "Docling API key file is empty" >&2
  exit 1
fi
export DOCLING_SERVE_API_KEY
exec docling-serve run
