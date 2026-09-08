"""Common Click decorators, option callbacks, and exit-code helpers for Strata CLI commands."""

from typing import Any

import click

OUTPUT_FORMATS = ["console", "text", "json", "ndjson"]

# Standard exit-code epilog applied to every leaf command that doesn't define its own.
# deploy run / deploy destroy / validate run carry command-specific epilogs and are skipped.
STANDARD_EPILOG = (
    "Exit codes:\n"
    "  0  success\n"
    "  1  system error (infrastructure unavailable, timeout, permissions) \u2014 alert\n"
    "  2  usage error (bad arguments, file not found) \u2014 fix script\n"
    "  3  validation error (schema, cross-ref) — fix config\n"
    "  4  lock conflict — another deploy is in progress\n"
    "  5  hand-off required — approval/gate pending; use --resume <id> after resolution\n"
)


def apply_standard_epilog(cmd: Any) -> None:
    """Recursively set STANDARD_EPILOG on all leaf commands that have no epilog defined."""
    if isinstance(cmd, click.Group):
        for sub in cmd.commands.values():
            apply_standard_epilog(sub)
    elif not getattr(cmd, "epilog", None):
        cmd.epilog = STANDARD_EPILOG


# Exit code handler for commands with validation
def handle_command_exit(command, success: bool) -> None:
    """
    Handle command exit codes based on success status and failure type.

    Exit codes:
        0 - Success
        1 - System/execution failure
        3 - Validation failure (file invalid)
        4 - Lock conflict (deploy run / deploy destroy only)
        5 - Hand-off required (work item created, deployment paused)

    Args:
        command: Command instance with optional has_validation_errors() / has_lock_conflict() / has_hand_off_required()
        success: Initial success status from command.execute()

    Raises:
        click.exceptions.Exit: With appropriate exit code
    """
    # Hand-off required: work item created, CI pipeline should pause and --resume after resolution.
    if not success and hasattr(command, "has_hand_off_required") and command.has_hand_off_required():
        raise click.exceptions.Exit(5)

    # Lock conflict takes priority over all other failure classifications.
    # Only reachable for deploy run / deploy destroy (has_lock_conflict lives on BaseDeployCommand).
    if not success and hasattr(command, "has_lock_conflict") and command.has_lock_conflict():
        raise click.exceptions.Exit(4)

    # Mark as failure if there are validation errors
    if success and hasattr(command, "has_validation_errors") and command.has_validation_errors():
        success = False

    if not success:
        # Check if failure was due to validation errors vs system errors
        if hasattr(command, "has_validation_errors") and command.has_validation_errors():
            # File didn't validate - exit code 3
            raise click.exceptions.Exit(3)
        else:
            # System/execution error - exit code 1
            raise click.exceptions.Exit(1)

    # Success - exit code 0 (implicit)


# Validation for mutually exclusive options
def validate_mutually_exclusive_options(ctx, param, value, exclusive_params):
    """
    Validate that mutually exclusive options are not used together.

    Args:
        ctx: Click context
        param: Current parameter being validated
        value: Value of the current parameter
        exclusive_params: List of parameter names that are mutually exclusive

    Raises:
        click.UsageError: If mutually exclusive options are used together

    Returns:
        The parameter value if validation passes
    """
    if value:
        # Check if any of the exclusive parameters are also set
        for exclusive_param in exclusive_params:
            if ctx.params.get(exclusive_param):
                raise click.UsageError(f"Illegal usage: --{param.name} and --{exclusive_param} are mutually exclusive.")
    return value


# Callbacks for specific mutually exclusive options
def validate_verbose_quiet_exclusive(ctx, param, value):
    """
    Callback to ensure --verbose and --quiet are not used together.

    Args:
        ctx: Click context
        param: Current parameter being validated
        value: Value of the current parameter

    Returns:
        The parameter value if validation passes

    Raises:
        click.UsageError: If both --verbose and --quiet are used
    """
    # Determine which parameter is exclusive based on current param name
    exclusive_param = "quiet" if param.name == "verbose" else "verbose"
    return validate_mutually_exclusive_options(ctx, param, value, [exclusive_param])


# Validation to ensure --output and --quiet are not used together
def validate_output_quiet_exclusive(ctx, param, value):
    """
    Callback to ensure --output and --quiet are not used together.

    Args:
        ctx: Click context
        param: Current parameter being validated
        value: Value of the current parameter

    Returns:
        The parameter value if validation passes

    Raises:
        click.UsageError: If --output (with non-empty value) and --quiet are used together
    """
    if param.name == "output":
        # For --output: check if quiet is set and output value is non-empty
        if value and ctx.params.get("quiet"):
            raise click.UsageError("Illegal usage: --output and --quiet are mutually exclusive.")
    elif param.name == "quiet":
        # For --quiet: check if output is set with non-empty value
        if value and ctx.params.get("output"):
            raise click.UsageError("Illegal usage: --quiet and --output are mutually exclusive.")
    return value


# --output json -> Returns the output in JSON format
def click_output_format(func):
    """
    Standard output format option for CLI commands.

    Available formats: json, text
    Default: text (human-readable, command-specific formatting)
    """
    formats_list = [f for f in OUTPUT_FORMATS if f]  # Exclude blank
    func = click.option(
        "--output",
        type=click.Choice(OUTPUT_FORMATS, case_sensitive=False),
        default=None,
        callback=validate_output_quiet_exclusive,
        help=f"Output format: {', '.join(formats_list)}",
    )(func)
    return func


# --verbose -> Enable verbose output (show logs in console)
def click_output_verbose(func):
    func = click.option(
        "--verbose",
        is_flag=True,
        callback=validate_verbose_quiet_exclusive,
        help="Enable verbose output (show logs in console)",
    )(func)
    return func


# --quiet -> Disable all output (show nothing in console)
def click_output_quiet(func):
    """
    Quiet mode disables all console output.
    Mutually exclusive with --verbose and --output.
    """

    def combined_callback(ctx, param, value):
        # Check both --verbose and --output exclusivity
        value = validate_verbose_quiet_exclusive(ctx, param, value)
        value = validate_output_quiet_exclusive(ctx, param, value)
        return value

    func = click.option(
        "--quiet",
        is_flag=True,
        callback=combined_callback,
        help="Disable any console output",
    )(func)
    return func


# --work-path -> The path to the root of the workspace if not in pwd
def click_work_path(func):
    func = click.option(
        "--work-path",
        default=None,
        metavar="PATH",
        type=click.Path(exists=False, file_okay=False, dir_okay=True, path_type=str),
        help="Workspace root directory (default: current directory).",
    )(func)
    return func


# --file / -f -> Path to the deployment YAML file (supports STRATA_FILE env var)
def click_file(func):
    func = click.option(
        "--file",
        "-f",
        default=None,
        envvar="STRATA_FILE",
        metavar="FILE",
        help="Path to the deployment YAML file. [env: STRATA_FILE]",
    )(func)
    return func


# --profile -> The active profile to use (defaults to the currently active profile)
def click_profile(func):
    func = click.option(
        "--profile",
        default=None,
        help="Profile name. Defaults to the currently active profile.",
    )(func)
    return func


# --no-cache -> Bypass the resolved-model cache for this invocation (ADR-0026)
def click_no_cache(func):
    func = click.option(
        "--no-cache",
        "no_cache",
        is_flag=True,
        envvar="STRATA_NO_CACHE",
        help="Bypass the resolved-model cache: no read, no write, no warm. [env: STRATA_NO_CACHE]",
    )(func)
    return func


# --refresh-cache -> Force re-warm even if the cache entry is fresh (ADR-0026)
def click_refresh_cache(func):
    func = click.option(
        "--refresh-cache",
        "refresh_cache",
        is_flag=True,
        help="Force re-warm the resolved-model cache even if it is still fresh.",
    )(func)
    return func


# --change-id / --change-system / --change-title / --change-url / --change-classification / --reason
# -> External change/ticket reference justifying this deploy (ADR-0074 Phase 1: capture only)
def click_change_reference(func):
    func = click.option(
        "--reason",
        "change_reason",
        default=None,
        envvar="STRATA_CHANGE_REASON",
        metavar="TEXT",
        help="Justification for this deployment. Required if --change-id is supplied. [env: STRATA_CHANGE_REASON]",
    )(func)
    func = click.option(
        "--change-classification",
        "change_classification",
        default=None,
        envvar="STRATA_CHANGE_CLASSIFICATION",
        metavar="VALUE",
        help=(
            "Change classification, e.g. 'emergency'/'normal'/'standard'. Validated against configuration's "
            "change_tracking.classifications when that allowlist is set. [env: STRATA_CHANGE_CLASSIFICATION]"
        ),
    )(func)
    func = click.option(
        "--change-url",
        "change_url",
        default=None,
        envvar="STRATA_CHANGE_URL",
        metavar="URL",
        help="Link to the change record. Resolved from configuration's change_tracking.url_template if omitted. [env: STRATA_CHANGE_URL]",
    )(func)
    func = click.option(
        "--change-title",
        "change_title",
        default=None,
        envvar="STRATA_CHANGE_TITLE",
        metavar="TEXT",
        help="Snapshot of the change record's title, for offline audit review. [env: STRATA_CHANGE_TITLE]",
    )(func)
    func = click.option(
        "--change-system",
        "change_system",
        default=None,
        envvar="STRATA_CHANGE_SYSTEM",
        metavar="NAME",
        help=(
            "Tracker identifier, e.g. 'jira', 'azure_devops', 'servicenow'. "
            "Defaults to configuration's change_tracking.system. [env: STRATA_CHANGE_SYSTEM]"
        ),
    )(func)
    func = click.option(
        "--change-id",
        "change_id",
        default=None,
        envvar="STRATA_CHANGE_ID",
        metavar="ID",
        help="External change/ticket identifier, e.g. 'OPS-1234'. [env: STRATA_CHANGE_ID]",
    )(func)
    return func
