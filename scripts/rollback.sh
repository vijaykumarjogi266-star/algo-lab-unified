#!/usr/bin/env bash
# ==============================================================================
# ALGO LAB UNIFIED: SAFE ROLLBACK SCRIPT
# Workspace: /opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified
# ==============================================================================
set -euo pipefail

WORKSPACE_ROOT="/opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified"
BACKUP_DIR="/opt/ai-builder/workspaces/algolab-unification-20261019-083200/evidence"
TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
BACKUP_ARCHIVE="${BACKUP_DIR}/unified_workspace_rollback_${TIMESTAMP}.tar.gz"

echo "[*] Initiating Safe Rollback for Algo Lab Unified..."

# Safety Check 1: Ensure workspace root matches expected isolated path
if [[ "${WORKSPACE_ROOT}" != "/opt/ai-builder/workspaces/algolab-unification-20261019-083200/algo-lab-unified" ]]; then
    echo "[!] FATAL: Workspace path mismatch. Aborting rollback to prevent accidental data loss." >&2
    exit 1
fi

# Safety Check 2: Verify canonical repositories are not touched
for CANONICAL in "/home/ubuntu/algopro" "/home/ubuntu/all-in-one" "/home/ubuntu/algolab"; do
    if [[ "${PWD}" == "${CANONICAL}"* ]]; then
        echo "[!] FATAL: Current directory is inside canonical repository ${CANONICAL}. Aborting immediately!" >&2
        exit 1
    fi
done

# Step 1: Create backup archive of current state
mkdir -p "${BACKUP_DIR}"
echo "[*] Archiving current workspace state to ${BACKUP_ARCHIVE}..."
tar --exclude=".venv-unified" -czf "${BACKUP_ARCHIVE}" -C "${WORKSPACE_ROOT}" .
echo "[✓] Backup archive created successfully (${BACKUP_ARCHIVE})."

# Step 2: Clean atomic teardown of generated artifacts
echo "[*] Cleaning workspace artifacts while preserving directory skeleton..."
rm -rf "${WORKSPACE_ROOT}/logs"/*
rm -rf "${WORKSPACE_ROOT}/data"/*
rm -rf "${WORKSPACE_ROOT}/.venv-unified"
find "${WORKSPACE_ROOT}" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${WORKSPACE_ROOT}" -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true

echo "[✓] Rollback complete. Workspace is reset to clean baseline."
