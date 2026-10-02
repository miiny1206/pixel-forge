#!/bin/sh
# One-step setup: builds pxf, installs the `pixel-forge` command, and connects it to Claude
# Code as an MCP server. Safe to run again after pulling changes.
set -e
cd "$(dirname "$0")"

command -v cargo >/dev/null || { echo "needs Rust: https://rustup.rs"; exit 1; }
command -v uv >/dev/null || { echo "needs uv: https://docs.astral.sh/uv/"; exit 1; }

cargo build --release --quiet
uv tool install --force --python '>=3.10' --editable './python[mcp]'

if [ ! -f .env ]; then
    cp .env.example .env
    echo "created .env: fill in PXF_API_BASE, PXF_API_KEY and PXF_IMAGE_MODEL"
fi

if command -v claude >/dev/null; then
    claude mcp remove -s user pixel-forge >/dev/null 2>&1 || true
    claude mcp add -s user pixel-forge -- pixel-forge mcp
fi

echo "done: try \`pixel-forge create out/cat --prompt \"a sleepy cat\" --size 32x32\`"
