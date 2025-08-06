#!/bin/bash
#===============================================================================
# Script Name   : initialize_server.sh
# Description   : Server initialization script
# Usage         : ./initialize_server.sh <PATH_DEPLOY>"
# Author        : Vincent Huybrechts
# Created       : 2025-07-23
# Last Modified : 2025-07-23
#===============================================================================
# Available directories and files in $PATH_DEPLOY (/tmp/app/.deploy)
# |- ./deploy/scripts/variables.env
# |- ./deploy/scripts/*
# |- ./deploy/workspaces/*
#===============================================================================
set -euo pipefail
trap 'echo "ERROR Script failed at line $LINENO: \`$BASH_COMMAND\`"' ERR

PATH_DEPLOY="$1"
: "${PATH_DEPLOY:?Missing PATH_DEPLOY}"
if [[ ! -d "$PATH_DEPLOY" ]]; then
  echo "Temporary deployment path $PATH_DEPLOY does not exist."
  exit 1
fi

