#!/usr/bin/env bash
set -euo pipefail

# Create a disposable Plone 6.2 development instance with this add-on
# installed and activated.  Override these for local development:
#   PLONE_SITE_ID=Demo PLONE_ADMIN=admin PLONE_PASSWORD=admin ./scripts/bootstrap-demo.sh

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTANCE_DIR="${ROOT_DIR}/${PLONE_INSTANCE_DIR:-instance}"
SITE_ID="${PLONE_SITE_ID:-Plone}"
ADMIN_USER="${PLONE_ADMIN:-admin}"
ADMIN_PASSWORD="${PLONE_PASSWORD:-admin}"

cd "${ROOT_DIR}"

command -v uv >/dev/null 2>&1 || {
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 1
}

uv python install 3.14
uv venv --python 3.14
uv sync --extra demo

if [[ ! -f "${INSTANCE_DIR}/etc/zope.conf" ]]; then
  uv run mkwsgiinstance \
    --dir "${INSTANCE_DIR}" \
    --user "${ADMIN_USER}:${ADMIN_PASSWORD}"
fi

PLONE_SITE_ID="${SITE_ID}" \
PLONE_ADMIN="${ADMIN_USER}" \
uv run zconsole run "${INSTANCE_DIR}/etc/zope.conf" scripts/create_demo_site.py

cat <<EOF

Demo site is ready:
  URL:      http://localhost:8080/${SITE_ID}
  user:     ${ADMIN_USER}
  password: ${ADMIN_PASSWORD}

Start it with:
  uv run runwsgi -v ${INSTANCE_DIR}/etc/zope.ini
EOF
