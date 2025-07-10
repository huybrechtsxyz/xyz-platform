#!/bin/bash

# Logging function
log() {
  local level="$1"; shift
  local msg="$*"
  local timestamp
  timestamp=$(date +"%Y-%m-%d %H:%M:%S")
  case "$level" in
    INFO)    echo -e "[\033[1;34mINFO\033[0m]  - $msg" ;;
    WARN)    echo -e "[\033[1;33mWARN\033[0m]  - $msg" ;;
    ERROR)   echo -e "[\033[1;31mERROR\033[0m] - $msg" >&2 ;;
    *)       echo -e "[UNKNOWN] - $msg" ;;
  esac
}

# Generate an environment file only taking env vars with specific prefix
generate_env_file() {
  local prefix="$1"
  local output_file="$2"

  if [[ -z "$prefix" || -z "$output_file" ]]; then
    echo "[!] Usage: generate_env_file <PREFIX> <OUTPUT_FILE>" >&2
    return 1
  fi

  log INFO "[*] Generating environment file for variables with prefix '$prefix'..."

  # Get all variables starting with the prefix
  mapfile -t vars < <(compgen -v | grep "^${prefix}")

  if [[ "${#vars[@]}" -eq 0 ]]; then
    echo "[!] Error: No environment variables found with prefix '$prefix'" >&2
    return 1
  fi

  # Validate all are non-empty
  for var in "${vars[@]}"; do
    [[ -z "${!var}" ]] && { echo "[!] Error: Missing required variable '$var'" >&2; return 1; }
  done

  # Ensure output directory exists
  mkdir -p "$(dirname "$output_file")"

  # Generate the env file with the prefix stripped
  {
    echo "# Auto-generated environment file (prefix '$prefix' stripped)"
    for var in "${vars[@]}"; do
      short_var="${var#$prefix}"
      printf '%s=%q\n' "$short_var" "${!var}"
    done
  } > "$output_file"

  log INFO "[+] Environment file generated at '$output_file'"
}

load_secret_identifiers() {
  # Workspace is required
  FILE="$1"
  ENVIRONMENT="$2"

  if [ -z "$FILE" ] || [ -z "$ENVIRONMENT" ]; then
    echo "Usage: load_secret_identifiers <path-to-ws.json> <environment>"
    return 1
  fi

  echo "[*] ... Loading secrets for environment '$ENVIRONMENT' from '$FILE'..."

  if ! command -v jq >/dev/null 2>&1; then
    echo "Error: 'jq' is required but not installed."
    exit 1
  fi

  if [ ! -f "$FILE" ]; then
    echo "Error: JSON file '$FILE' not found."
    exit 1
  fi

  # Extract secrets for the specified environment
  secrets=$(jq -r --arg env "$ENVIRONMENT" '.secrets.environment[$env]' "$FILE")
  if [ "$secrets" == "null" ]; then
    echo "[!] ERROR No secrets found for environment '$ENVIRONMENT'."
    return 1
  fi

  # Iterate and export
  echo "$secrets" | jq -r 'to_entries[] | "\(.key)=\(.value)"' | while IFS="=" read -r key value; do
    UPPER_KEY=$(echo "$key" | tr '[:lower:]' '[:upper:]')
    CLEAN_VALUE=$(echo "$value" | sed 's/^"//;s/"$//')
    export "UUID_${UPPER_KEY}"="$CLEAN_VALUE"
    echo "Exported UUID_${UPPER_KEY}"
  done

  echo "[*] ... Loading secrets for environment '$ENVIRONMENT' from '$FILE'...DONE"
}
