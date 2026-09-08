"""Click CLI wiring for the deploy command group."""

from typing import Optional

import click

from strata.commands.cli_common import (
    click_change_reference,
    click_file,
    click_no_cache,
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_refresh_cache,
    click_work_path,
    handle_command_exit,
)
from strata.commands.deploy.acknowledge_drift_deploy_command import AcknowledgeDriftDeployCommand
from strata.commands.deploy.destroy_deploy_command import DestroyDeployCommand
from strata.commands.deploy.drift_deploy_command import DriftDeployCommand
from strata.commands.deploy.drift_history_deploy_command import DriftHistoryDeployCommand
from strata.commands.deploy.health_deploy_command import HealthDeployCommand
from strata.commands.deploy.history_deploy_command import HistoryDeployCommand
from strata.commands.deploy.list_deploy_command import ListDeployCommand
from strata.commands.deploy.lock_deploy_command import LockHistoryCommand, LockReleaseCommand, LockStatusCommand
from strata.commands.deploy.output_deploy_command import OutputDeployCommand
from strata.commands.deploy.plan_deploy_command import PlanDeployCommand
from strata.commands.deploy.run_deploy_command import RunDeployCommand
from strata.commands.deploy.show_deploy_command import ShowDeployCommand
from strata.commands.deploy.status_deploy_command import StatusDeployCommand


@click.group(name="deploy", help="Deploy platform using provisioners.")
def deploy():
    """Deploy command group."""
    pass


@deploy.command(
    name="run",
    help="Run the deploy pipeline for a deployment definition.",
    epilog=(
        "Exit codes:\n"
        "  0  success\n"
        "  1  system error (infrastructure unavailable, timeout, permissions) — alert\n"
        "  2  usage error (bad arguments, file not found) — fix script\n"
        "  3  validation error (schema, cross-ref) — fix config\n"
        "  4  lock conflict (another deployment in progress) — retry after delay"
    ),
)
@click_file
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit execution to a specific deployment stage by name.",
)
@click.option(
    "--scope",
    default=None,
    metavar="LABEL",
    help="Run only deployment stages whose scope field matches this label.",
)
@click.option(
    "--namespace",
    "namespaces",
    multiple=True,
    default=None,
    metavar="NAME",
    help=(
        "Limit the helm provisioner to specific namespace(s) for this run (repeatable). "
        "Overrides any stage-level 'helm_namespaces' allowlist. Omit to deploy all namespaces."
    ),
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Skip interactive confirmation prompts and approval gates.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Validate and plan the deploy without running any provisioners.",
)
@click.option(
    "--force-lock",
    is_flag=True,
    default=False,
    help="Force-release any held lock before acquiring. Use to recover from a crashed pipeline.",
)
@click.option(
    "--require-lock",
    "require_lock",
    is_flag=True,
    default=False,
    help=(
        "Fail (exit 3) if the target ring has no lock file (versions/<ring>.yaml). "
        "Also enforced when the ring declares require_lock: true in configuration."
    ),
)
@click.option(
    "--version-file",
    "-v",
    "version_file",
    default=None,
    metavar="PATH",
    help=(
        "Explicit version file (kind: version) to apply for this deployment (Layer 3). "
        "Mutually exclusive with spec.promotion — use 'strata promote' for managed promotions."
    ),
)
@click.option(
    "--ring",
    "ring_override",
    default=None,
    metavar="NAME",
    help=(
        "Override the ring name used for version lock resolution. "
        "Defaults to the ring declared in the deployment's spec.promotion.ring."
    ),
)
@click.option(
    "--wave",
    "wave",
    default=None,
    type=int,
    metavar="N",
    help=(
        "Layer the wave-N lock file ({ring}.wave.N.lock.yaml) on top of the ring lock "
        "during version resolution. Wave pins win over ring pins for the same target."
    ),
)
@click.option(
    "--promotion",
    "promotion_override",
    default=None,
    metavar="NAME",
    help=(
        "Override the promotion strategy name used for version lock resolution. "
        "Defaults to the strategy declared in the deployment's spec.promotion.strategy."
    ),
)
@click.option(
    "--timeout",
    type=int,
    default=0,
    metavar="SECONDS",
    help="Abort if the command does not complete within N seconds (0 = no timeout).",
)
@click.option(
    "--resume",
    "resume_id",
    default=None,
    metavar="ID",
    help="Resume a paused deployment after gate resolution. Provide the work-item ID printed when the deploy was paused.",
)
@click_change_reference
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_run(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    scope: Optional[str] = None,
    namespaces: Optional[tuple] = None,
    force: bool = False,
    dry_run: bool = False,
    force_lock: bool = False,
    require_lock: bool = False,
    version_file: Optional[str] = None,
    ring_override: Optional[str] = None,
    wave: Optional[int] = None,
    promotion_override: Optional[str] = None,
    timeout: int = 0,
    ai: bool = False,
    strict_ai_review: Optional[str] = None,
    resume_id: Optional[str] = None,
    change_id: Optional[str] = None,
    change_system: Optional[str] = None,
    change_title: Optional[str] = None,
    change_url: Optional[str] = None,
    change_classification: Optional[str] = None,
    change_reason: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Execute the deploy pipeline."""
    command = RunDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        scope=scope,
        namespaces=list(namespaces) if namespaces else None,
        force=force,
        dry_run=dry_run,
        force_lock=force_lock,
        require_lock=require_lock,
        version_file=version_file,
        ring_override=ring_override,
        wave=wave,
        promotion_override=promotion_override,
        timeout=timeout,
        ai=ai,
        strict_ai_review=strict_ai_review,
        resume_id=resume_id,
        change_id=change_id,
        change_system=change_system,
        change_title=change_title,
        change_url=change_url,
        change_classification=change_classification,
        change_reason=change_reason,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(
    name="destroy",
    help="Tear down provisioned infrastructure for a deployment definition.",
    epilog=(
        "Exit codes:\n"
        "  0  success\n"
        "  1  system error (infrastructure unavailable, timeout, permissions) — alert\n"
        "  2  usage error (bad arguments, file not found) — fix script\n"
        "  3  validation error (schema, cross-ref) — fix config\n"
        "  4  lock conflict (another deployment in progress) — retry after delay\n\n"
        "Note: exactly one of --dry-run or --force must be provided."
    ),
)
@click_file
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit destruction to a specific deployment stage by name.",
)
@click.option(
    "--scope",
    default=None,
    metavar="LABEL",
    help="Destroy only deployment stages whose scope field matches this label.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Auto-approve: run terraform destroy non-interactively. (cannot use with: --dry-run)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Plan what would be destroyed (terraform plan -destroy) without removing anything. (cannot use with: --force)",
)
@click.option(
    "--force-lock",
    is_flag=True,
    default=False,
    help="Force-release any held lock before acquiring. Use to recover from a crashed pipeline.",
)
@click.option(
    "--timeout",
    type=int,
    default=0,
    metavar="SECONDS",
    help="Abort if the command does not complete within N seconds (0 = no timeout).",
)
@click_change_reference
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_destroy(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    scope: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
    force_lock: bool = False,
    timeout: int = 0,
    change_id: Optional[str] = None,
    change_system: Optional[str] = None,
    change_title: Optional[str] = None,
    change_url: Optional[str] = None,
    change_classification: Optional[str] = None,
    change_reason: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Tear down provisioned infrastructure."""
    if force and dry_run:
        raise click.UsageError("--force and --dry-run are mutually exclusive.")
    if not force and not dry_run:
        raise click.UsageError("One of --force or --dry-run must be provided.")
    command = DestroyDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        scope=scope,
        force=force,
        dry_run=dry_run,
        force_lock=force_lock,
        timeout=timeout,
        change_id=change_id,
        change_system=change_system,
        change_title=change_title,
        change_url=change_url,
        change_classification=change_classification,
        change_reason=change_reason,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(
    name="show", help="Show resolved deployment configuration: remote versions, workspace, and environment."
)
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Filter secrets visibility to a specific stage's allowlist.",
)
@click_no_cache
@click_refresh_cache
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_show(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    no_cache: bool = False,
    refresh_cache: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show resolved deployment configuration."""
    command = ShowDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        no_cache=no_cache,
        refresh_cache=refresh_cache,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="plan", help="Show the resource change summary from the last saved .tfplan file.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit display to a specific deployment stage.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_plan(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show resource change summary from the last saved .tfplan (terraform show -json). No backend calls."""
    command = PlanDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="list", help="List deployment manifests with metadata for CI matrix generation.")
@click.option(
    "--path",
    "-p",
    default=None,
    metavar="DIR",
    help="Directory to scan for deployment manifests (default: current directory).",
)
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_list(
    path: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """List deployment manifests with metadata."""
    command = ListDeployCommand(
        path=path,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="history", help="Show deployment execution history from workspace logs.")
@click_work_path
@click.option(
    "--lines",
    default=50,
    show_default=True,
    help="Maximum number of history entries to display.",
)
@click.option(
    "--operation",
    default=None,
    type=click.Choice(["run", "destroy"], case_sensitive=False),
    help="Filter to a specific operation type.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Run AI trend analysis on deployment history (requires an ai_agent integration; needs ≥2 entries).",
)
def deploy_history(
    work_path: Optional[str] = None,
    lines: int = 50,
    operation: Optional[str] = None,
    ai: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show deployment execution history from workspace logs."""
    command = HistoryDeployCommand(
        work_path=work_path,
        lines=lines,
        operation=operation,
        ai=ai,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="health", help="Run health checks against provisioned infrastructure stages.")
@click.option(
    "--file",
    "-f",
    required=True,
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Path to the deployment YAML file. [env: STRATA_FILE]",
)
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit checks to a specific deployment stage.",
)
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Run AI explanation of failed health probes (requires an ai_agent integration).",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_health(
    file: str,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    ai: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Run health checks against provisioned infrastructure stages."""
    command = HealthDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        ai=ai,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="status", help="Show the live infrastructure status for a single deployment.")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Query only a single stage (default: all).",
)
@click.option(
    "--offline",
    is_flag=True,
    default=False,
    help="Use cached data only — do not contact remote backends.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_status(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    offline: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show infrastructure status: per-stage resources, outputs, serial, and cache freshness."""
    command = StatusDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        offline=offline,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# deploy drift (subgroup)
# ---------------------------------------------------------------------------


@deploy.group(name="drift", help="Drift detection: run checks, acknowledge expected drift, view history.")
def deploy_drift_group():
    """Drift subcommand group."""
    pass


@deploy_drift_group.command(name="run", help="Detect configuration drift between Terraform state and code.")
@click.option(
    "--file",
    "-f",
    required=True,
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Path to the deployment YAML file. [env: STRATA_FILE]",
)
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit drift detection to a specific deployment stage.",
)
@click.option(
    "--severity",
    default="info",
    show_default=True,
    type=click.Choice(["critical", "high", "medium", "low", "info"], case_sensitive=False),
    help="Minimum severity threshold for exit-code 3. Changes below this level are reported but do not fail.",
)
@click.option(
    "--baseline",
    is_flag=True,
    default=False,
    help="Acknowledge all currently drifted resources as the accepted baseline and reset history. Always exits 0.",
)
@click.option(
    "--ai",
    "ai",
    is_flag=True,
    default=False,
    help="Run AI drift explanation when drift is detected (requires an ai_agent integration).",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_drift_run(
    file: str,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    severity: str = "info",
    baseline: bool = False,
    ai: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Detect infrastructure drift by running a non-destructive terraform plan for each stage."""
    command = DriftDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        severity=severity,
        baseline=baseline,
        ai=ai,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy_drift_group.command(name="acknowledge", help="Acknowledge expected drift for a resource address.")
@click.option(
    "--file",
    "-f",
    required=True,
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Path to the deployment YAML file. [env: STRATA_FILE]",
)
@click_work_path
@click.option(
    "--address",
    required=True,
    metavar="ADDRESS",
    help="Terraform resource address to acknowledge (e.g. azurerm_autoscale_setting.web).",
)
@click.option(
    "--reason",
    default="",
    metavar="TEXT",
    help="Human-readable explanation of why this drift is expected.",
)
@click.option(
    "--remove",
    is_flag=True,
    default=False,
    help="Remove a previously added acknowledgement so the address is reported again.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_drift_acknowledge(
    file: str,
    work_path: Optional[str] = None,
    address: str = "",
    reason: str = "",
    remove: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Acknowledge (or un-acknowledge) expected drift for a specific resource address."""
    command = AcknowledgeDriftDeployCommand(
        file=file,
        work_path=work_path,
        address=address,
        reason=reason,
        remove=remove,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy_drift_group.command(name="history", help="Show drift-check run history and acknowledged addresses.")
@click.option(
    "--file",
    "-f",
    required=True,
    envvar="STRATA_FILE",
    metavar="PATH",
    help="Path to the deployment YAML file. [env: STRATA_FILE]",
)
@click_work_path
@click.option(
    "--last",
    default=10,
    show_default=True,
    metavar="N",
    help="Number of most-recent drift-check runs to show.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_drift_history(
    file: str,
    work_path: Optional[str] = None,
    last: int = 10,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show per-run history and acknowledged (suppressed) addresses for a deployment."""
    command = DriftHistoryDeployCommand(
        file=file,
        work_path=work_path,
        last=last,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy.command(name="output", help="Show Terraform outputs for a deployment (cached, live, or stored artifacts.)")
@click_file
@click_work_path
@click.option(
    "--stage",
    default=None,
    metavar="NAME",
    help="Limit output to a specific deployment stage.",
)
@click.option(
    "--provisioner",
    default=None,
    metavar="NAME",
    help="Limit to stages that use a specific provisioner (default: all).",
)
@click.option(
    "--key",
    default=None,
    metavar="NAME",
    help="Show only a single output key (useful for scripting).",
)
@click.option(
    "--raw",
    is_flag=True,
    default=False,
    help="Print bare value with no formatting — requires --key.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit outputs as JSON — bypasses the strata envelope.",
)
@click.option(
    "--refresh",
    is_flag=True,
    default=False,
    help="Fetch outputs live from the backend and update the cache.",
)
@click.option(
    "--version",
    default=None,
    metavar="VERSION",
    help="Show stored output artifacts for a specific version tag.",
)
@click.option(
    "--all-versions",
    is_flag=True,
    default=False,
    help="Show stored output artifacts for every version found.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_output(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    stage: Optional[str] = None,
    provisioner: Optional[str] = None,
    key: Optional[str] = None,
    raw: bool = False,
    json_output: bool = False,
    refresh: bool = False,
    version: Optional[str] = None,
    all_versions: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show Terraform outputs from cache, live backend, or stored artifacts."""
    command = OutputDeployCommand(
        file=file,
        work_path=work_path,
        stage=stage,
        provisioner=provisioner,
        key=key,
        raw=raw,
        json_output=json_output,
        refresh=refresh,
        version=version,
        all_versions=all_versions,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


# ---------------------------------------------------------------------------
# deploy lock subgroup
# ---------------------------------------------------------------------------


@deploy.group(name="lock", help="Manage deployment state locks.")
def deploy_lock():
    """Lock management subgroup."""
    pass


@deploy_lock.command(name="status", help="Show the current lock state for a deployment.")
@click_file
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_lock_status(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show current lock state."""
    command = LockStatusCommand(
        file=file,
        work_path=work_path,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy_lock.command(name="release", help="Release the state lock for a deployment.")
@click_file
@click_work_path
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Release the lock even if held by a different user or host.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_lock_release(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    force: bool = False,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Release the current deployment lock."""
    command = LockReleaseCommand(
        file=file,
        work_path=work_path,
        force=force,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)


@deploy_lock.command(name="history", help="Show recent lock history for a deployment.")
@click_file
@click_work_path
@click.option(
    "--last",
    default=10,
    show_default=True,
    type=click.IntRange(1, 100),
    help="Number of recent lock events to show.",
)
@click_output_format
@click_output_verbose
@click_output_quiet
def deploy_lock_history(
    file: Optional[str] = None,
    work_path: Optional[str] = None,
    last: int = 10,
    output: Optional[str] = None,
    verbose: Optional[bool] = None,
    quiet: Optional[bool] = None,
):
    """Show recent lock history."""
    command = LockHistoryCommand(
        file=file,
        work_path=work_path,
        last=last,
        output=output,
        verbose=verbose,
        quiet=quiet,
    )
    success = command.execute()
    handle_command_exit(command, success)
