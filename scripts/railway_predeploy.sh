#!/bin/sh
set -eu

alembic upgrade head

if [ "${SEED_DEMO_DATA:-false}" = "true" ]; then
  python -m scripts.seed
fi
