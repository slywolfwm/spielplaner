#!/usr/bin/env bash
set -e

python -m omoc_refresh &
python -m omoc_refresh --requests &
exec python -m streamlit run app.py \
  --server.address 0.0.0.0 \
  --server.port 8000 \
  --server.headless true
