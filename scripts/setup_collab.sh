#!/usr/bin/env sh
# One-time setup per clone so Claude Code and Cursor can work the same repo at
# the same time. Registers the merge driver that unions Meedo-Me's memory
# (episodes, ledger) instead of conflicting on every concurrent append.
set -e
cd "$(git rev-parse --show-toplevel)"
git config merge.meedo.name "Meedo-Me memory union"
git config merge.meedo.driver "python3 -m tools.logo_vectorizer.meedo_merge %O %A %B"
echo "merge.meedo registered; see COORDINATION.md for the protocol"
