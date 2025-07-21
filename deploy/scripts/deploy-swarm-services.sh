#!/bin/bash
set -euo pipefail

: "${VAR_WORKSPACE:?Missing WORKSPACE env var}"
: "${VAR_ENVIRONMENT:?Missing ENVIRONMENT env var}"
: "${VAR_SERVICEDATA:?Missing SERVICEDATA env var}"
: "${VAR_SERVERINFO:?Missing SERVERINFO env var}"

source "$(dirname "${BASH_SOURCE[0]}")/utilities.sh"

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
  echo "Reading service and information"
  SERVICE_ID=$(jq -r '.service.id' <<< "$VAR_SERVICEDATA")
  SERVER_IP=$(jq -r '.ip' <<< "$VAR_SERVERINFO")

  log INFO "[*] Deploying service $SERVICE_ID on $SERVER_IP ..."

  log INFO "[*] Deploying service $SERVICE_ID on $SERVER_IP ... DONE"
}

main