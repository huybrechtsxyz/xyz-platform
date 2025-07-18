#!/bin/bash
set -euo pipefail

# Usage: ./deploy.sh <service_id> <environment>
SERVICE_ID="$1"
ENVIRONMENT="$2"

: "${SERVICE_ID:?Missing SERVICE_ID env var}"
: "${ENVIRONMENT:?Missing ENVIRONMENT env var}"

source "$(dirname "${BASH_SOURCE[0]}")/../../scripts/utilities.sh"

create_variables_secrets() {
  SERVICE_PATH="/service/${SERVICE_ID}/src"
  SECRETS_FILE="${SERVICE_PATH}/${ENVIRONMENT}.secrets.env"
  OUTPUT_FILE="$VAR_PATH_TEMP/${SERVICE_ID}.secrets"

  echo "🔐 Fetching secrets for service: $SERVICE_ID ($ENVIRONMENT)"
  echo "📄 Secrets definition file: $SECRETS_FILE"
  mkdir -p "$VAR_PATH_TEMP"
  > "$OUTPUT_FILE"

  if [[ ! -f "$SECRETS_FILE" ]]; then
    echo "No secrets file found at $SECRETS_FILE"
    return 0
  fi

  while IFS='=' read -r VAR UUID; do
    [[ -z "$VAR" || -z "$UUID" ]] && continue
    VALUE=$(bw get password "$UUID")
    echo "$VAR=$VALUE" >> "$OUTPUT_FILE"
  done < "$SECRETS_FILE"
}

main() {
  create_variables_secrets
}

main