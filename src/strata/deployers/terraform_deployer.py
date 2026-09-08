"""Terraform deployer — step-based provisioner style.

Supported steps (in execution order):
  setup    — terraform init
  check    — terraform validate
  plan     — terraform plan  -out=<stage>.tfplan
  apply    — terraform apply <stage>.tfplan
  destroy  — terraform destroy  (requires force=True for -auto-approve)
  output   — terraform output -json → {name: value} dict

Typical caller sequences:
  dry-run  : setup → check → plan
  deploy   : setup → check → plan → apply
  destroy  : setup → destroy  (force=True required)
  output   : output

``line_callback`` parameter (all step methods):
  Optional ``Callable[[str, str], None]`` — called for each subprocess output
  line as it arrives.  First arg is the stream name (``"stdout"`` / ``"stderr"``),
  second is the raw text line.  Use this to stream live output to the caller
  (e.g. NDJSON events or verbose console output).
"""

import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_DRIFT,
    STEP_OUTPUT,
    STEP_PLAN,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
    STEP_SHOW_PLAN,
    BaseDeployer,
)
from strata.integrations.terraform import TerraformIntegration
from strata.models.deployment_model import DeploymentStageModel
from strata.models.workspace_model import OutputProfileModel, WorkspaceIacModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService
from strata.utils.resolved_values import EXPR_PATTERN, ResolvedValues, inject_tf_vars, resolve_expr_string

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController


class TerraformDeployer(BaseDeployer):
    """Runs a deployment stage using Terraform (init → validate → plan → apply).

    Context is passed once to the constructor; step methods carry no arguments.
    Call validate_workspace() then validate_environment() before running steps.
    """

    def __init__(
        self,
        stage: DeploymentStageModel,
        deployment_service: DeploymentService,
        configuration_service: ConfigurationService,
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        resolved_values: Optional[ResolvedValues] = None,
        solution_controller: Optional["SolutionController"] = None,
    ) -> None:
        super().__init__(
            stage=stage,
            deployment_service=deployment_service,
            configuration_service=configuration_service,
            build_path=build_path,
            work_path=work_path,
            verbose=verbose,
            force=force,
            solution_controller=solution_controller,
            resolved_values=resolved_values,
        )
        self._iac_model: Optional[WorkspaceIacModel] = None
        self._working_dir: Optional[Path] = None
        self._plan_file: Optional[Path] = None
        self._tf: Optional[TerraformIntegration] = None
        # Set by plan(); None = plan not yet run, False = no changes, True = changes present
        self._plan_has_changes: Optional[bool] = None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:
        return "terraform"

    def get_supported_steps(self) -> List[str]:
        return [
            STEP_SETUP,
            STEP_CHECK,
            STEP_PLAN,
            STEP_APPLY,
            STEP_DESTROY,
            STEP_PLAN_DESTROY,
            STEP_SHOW_PLAN,
            STEP_OUTPUT,
            STEP_DRIFT,
        ]

    # ------------------------------------------------------------------
    # Validation (pre-step guards)
    # ------------------------------------------------------------------

    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify the terraform working directory and .tf files exist."""
        messages: List[str] = []

        workspace_service = self.deployment_service.get_workspace_service()
        if not workspace_service:
            messages.append("Workspace service is not available")
            return False, messages

        self._iac_model = self._resolve_iac_model(self.stage, workspace_service)
        if not self._iac_model:
            messages.append(
                f"Stage '{self.stage.name}': cannot resolve a terraform provisioner. "
                "Set stage.provisioner (by name) or stage.topology with a workspace "
                "topology that has provisioner='terraform'."
            )
            return False, messages

        if self.solution_controller is not None:
            self._working_dir = self.solution_controller.get_provisioner_path(
                self.deployment_service, self.build_path, self._iac_model
            )
        else:
            self._working_dir = self._get_working_dir(self.deployment_service, self.build_path, self._iac_model)
        self._plan_file = self._working_dir / f"{self.stage.name}.tfplan"

        if not self._working_dir.exists():
            messages.append(
                f"Terraform working directory does not exist: {self._working_dir}\n"
                "  Run 'strata build run' first to copy IaC artefacts to the build folder."
            )
            return False, messages

        tf_files = list(self._working_dir.glob("*.tf"))
        if not tf_files:
            messages.append(
                f"No *.tf files found in: {self._working_dir}\n"
                "  The build step should have copied Terraform source code here."
            )
            return False, messages

        if self.verbose:
            messages.append(f"Terraform working directory OK: {self._working_dir} ({len(tf_files)} .tf file(s))")

        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Verify Terraform binary is on PATH and cloud CLI auth if detectable.

        After confirming the Terraform/OpenTofu binary, inspects the backend
        type to infer which cloud is targeted and runs a quick CLI auth check:

        - ``azurerm`` backend → ``AzureCLIIntegration.ensure_available()``
        - ``s3`` backend      → ``AWSCLIIntegration.ensure_available()``
        - ``gcs`` backend     → ``GCloudCLIIntegration.ensure_available()``

        Auth check behaviour:
        - CLI installed but **not** authenticated → **error** (fail fast, clear fix hint)
        - CLI not installed → **warning only** (operators may use env-var credentials
          without the cloud CLI — do not block)
        """
        messages: List[str] = []

        if self._iac_model is None:
            messages.append("validate_workspace() must succeed before validate_environment()")
            return False, messages

        try:
            self._tf = self._get_terraform_integration(self._iac_model.name)
        except RuntimeError as exc:
            messages.append(str(exc))
            return False, messages

        if not self._tf.is_available():
            tool = self._tf.command if self._tf else "terraform"
            messages.append(f"{tool} binary not found on PATH. Install it and ensure it is accessible.")
            return False, messages

        # Cloud CLI pre-flight — infer cloud from backend type
        backend_type = ""
        if self._iac_model.backend and self._iac_model.backend.type:
            backend_type = str(self._iac_model.backend.type).lower()

        cloud_ok, cloud_msgs = self._check_cloud_cli(backend_type)
        messages.extend(cloud_msgs)
        if not cloud_ok:
            return False, messages

        return True, messages

    def _check_cloud_cli(self, backend_type: str) -> Tuple[bool, List[str]]:
        """Run a non-blocking cloud CLI auth check based on the backend type.

        Returns (False, [error]) only when the CLI *is* installed but auth fails.
        Returns (True, [warning]) when the CLI is absent — env-var auth may still work.
        Returns (True, []) when the CLI is available and authenticated.
        """
        from strata.models.integration_model import IntegrationModel

        cloud_map = {
            "azurerm": ("azure_cli", "AzureCLIIntegration", "az login"),
            "s3": ("aws_cli", "AWSCLIIntegration", "aws configure  or  aws sso login"),
            "gcs": ("gcloud_cli", "GCloudCLIIntegration", "gcloud auth login"),
        }

        entry = cloud_map.get(backend_type)
        if not entry:
            return True, []

        integration_type, class_name, fix_cmd = entry

        try:
            if integration_type == "azure_cli":
                from strata.integrations.azure_cli import AzureCLIIntegration as Cls
            elif integration_type == "aws_cli":
                from strata.integrations.aws_cli import AWSCLIIntegration as Cls  # type: ignore[assignment]
            else:
                from strata.integrations.gcloud_cli import GCloudCLIIntegration as Cls  # type: ignore[assignment]

            cli = Cls(IntegrationModel(name=integration_type, type=integration_type))

            if not cli.is_available():
                # CLI not installed — warn but don't block (env-var credentials may work)
                if self.verbose:
                    return True, [
                        f"Note: {cli.command} CLI not found — assuming env-var credentials for {backend_type} backend."
                    ]
                return True, []

            ok, reason = cli.ensure_available()
            if not ok:
                return False, [
                    f"Cloud CLI auth check failed for '{backend_type}' backend: {reason}",
                    f"Run: {fix_cmd}",
                ]

            if self.verbose:
                return True, [f"Cloud CLI check passed: {cli._info}"]
            return True, []

        except Exception:
            return True, []  # never block deploy on unexpected errors here

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    def setup(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform init"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._iac_model is not None
        assert self._tf is not None

        backend_config, backend_errors = self._build_backend_config(self._iac_model)
        if backend_errors:
            messages.extend(f"backend config error: {err}" for err in backend_errors)
            return False, messages
        messages.append(f"terraform init  ({self._working_dir})")

        # Phase 1B: write deploy-time store-backed tfvars before terraform init
        if self.resolved_values and self._working_dir:
            profile = self._get_output_profile_for_provisioner()
            if profile is not None:
                self._write_deploy_time_vars(self.resolved_values, profile, self._working_dir)

        try:
            result = self._tf.init(
                str(self._working_dir),
                backend_config=backend_config or None,
                reconfigure=bool(backend_config),
                line_callback=line_callback,
                timeout=self._get_timeout("setup", 300),
            )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform init failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform init error: {exc}")
            return False, messages

        return True, messages

    def check(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform validate"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._tf is not None

        messages.append("terraform validate")

        try:
            result = self._tf.validate(
                str(self._working_dir),
                timeout=self._get_timeout("check", 60),
            )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform validate failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform validate error: {exc}")
            return False, messages

        return True, messages

    def plan(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform plan -detailed-exitcode -out=<stage>.tfplan

        Uses ``-detailed-exitcode`` so the exit code carries change-detection:
          0 = success, no changes  → sets _plan_has_changes = False
          1 = error
          2 = success, changes present → sets _plan_has_changes = True
        """
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._plan_file is not None
        assert self._tf is not None

        messages.append(f"terraform plan  \u2192 {self._plan_file.name}")

        try:
            ctx = inject_tf_vars(self.resolved_values) if self.resolved_values else nullcontext()
            with ctx:
                result = self._tf.plan(
                    str(self._working_dir),
                    out_file=str(self._plan_file),
                    detailed_exitcode=True,
                    line_callback=line_callback,
                    timeout=self._get_timeout("plan", 600),
                )
            # -detailed-exitcode contract: 0=no changes, 1=error, 2=changes present
            if result.returncode == 0:
                self._plan_has_changes = False
                messages.append("\u21b3 No changes \u2014 infrastructure is already up to date.")
            elif result.returncode == 2:
                self._plan_has_changes = True
            else:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform plan failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform plan error: {exc}")
            return False, messages

        return True, messages

    def apply(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform apply <stage>.tfplan

        Short-circuits when ``plan()`` determined there are no changes
        (_plan_has_changes is False).  Downstream stages still run.
        """
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._plan_file is not None
        assert self._tf is not None

        if self._plan_has_changes is False:
            messages.append("\u21b3 No changes \u2014 apply skipped.")
            return True, messages

        messages.append(f"terraform apply  {self._plan_file.name}")

        try:
            ctx = inject_tf_vars(self.resolved_values) if self.resolved_values else nullcontext()
            with ctx:
                result = self._tf.apply(
                    str(self._working_dir),
                    plan_file=str(self._plan_file),
                    line_callback=line_callback,
                    timeout=self._get_timeout("apply", 1800),
                )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform apply failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform apply error: {exc}")
            return False, messages

        messages.append(f"\u2713 Stage '{self.stage.name}' applied successfully.")
        return True, messages

    def destroy(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform destroy (-auto-approve when force=True)"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._tf is not None

        messages.append("terraform destroy")

        try:
            ctx = inject_tf_vars(self.resolved_values) if self.resolved_values else nullcontext()
            with ctx:
                result = self._tf.destroy(
                    str(self._working_dir),
                    auto_approve=self.force,
                    line_callback=line_callback,
                    timeout=self._get_timeout("destroy", 1800),
                )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform destroy failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform destroy error: {exc}")
            return False, messages

        messages.append(f"\u2713 Stage '{self.stage.name}' destroyed successfully.")
        return True, messages

    def plan_destroy(self) -> Tuple[bool, List[str]]:
        """terraform plan -destroy  (preview what destroy would remove)"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._plan_file is not None
        assert self._tf is not None

        messages.append(f"terraform plan -destroy  \u2192 {self._plan_file.name}")

        try:
            ctx = inject_tf_vars(self.resolved_values) if self.resolved_values else nullcontext()
            with ctx:
                result = self._tf.plan(
                    str(self._working_dir),
                    out_file=str(self._plan_file),
                    destroy=True,
                )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform plan -destroy failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip():
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform plan -destroy error: {exc}")
            return False, messages

        return True, messages

    def collect_outputs(self) -> Tuple[bool, Dict[str, Any], Dict[str, Any], List[str]]:
        """Collect Terraform outputs after a successful apply, split by sensitivity.

        Runs ``terraform output -json`` and reads the ``sensitive`` flag on each
        output descriptor.  Non-sensitive outputs are returned in the first dict
        and will be injected as ``TF_VAR_<key>`` env vars for downstream stages.
        Sensitive outputs (``sensitive = true`` in Terraform) are returned in the
        second dict — the pipeline holds them internally but never injects them
        into subprocess environments.

        Returns:
            (success, non_sensitive_outputs, sensitive_outputs, messages)
        """
        messages: List[str] = []
        non_sensitive: Dict[str, Any] = {}
        sensitive: Dict[str, Any] = {}

        if not self._ready(messages):
            return False, non_sensitive, sensitive, messages
        assert self._working_dir is not None
        assert self._tf is not None

        try:
            result = self._tf.output(str(self._working_dir))
            if result.returncode != 0:
                messages.append(f"terraform output failed:\n{result.stderr}")
                return False, non_sensitive, sensitive, messages
            raw: Dict[str, Any] = json.loads(result.stdout or "{}")
            for key, descriptor in raw.items():
                val = descriptor.get("value")
                if descriptor.get("sensitive", False):
                    sensitive[key] = val
                else:
                    non_sensitive[key] = val
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            messages.append(f"terraform output error: {exc}")
            return False, non_sensitive, sensitive, messages

        self._write_outputs_cache(non_sensitive)
        return True, non_sensitive, sensitive, messages

    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """terraform output -json -> {name: value} dict"""
        messages: List[str] = []
        outputs: Dict[str, Any] = {}

        if not self._ready(messages):
            return False, outputs, messages
        assert self._working_dir is not None
        assert self._tf is not None

        messages.append("terraform output -json")

        try:
            result = self._tf.output(str(self._working_dir))
            if result.returncode != 0:
                messages.append(f"terraform output failed:\n{result.stderr}")
                return False, outputs, messages
            raw: Dict[str, Any] = json.loads(result.stdout or "{}")
            # terraform output -json returns {name: {value: ..., type: ...}}
            outputs = {k: v.get("value") for k, v in raw.items()}
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            messages.append(f"terraform output error: {exc}")
            return False, outputs, messages

        self._write_outputs_cache(outputs)
        return True, outputs, messages

    def _write_outputs_cache(self, outputs: Dict[str, Any]) -> None:
        """Write outputs to build/<stage>.tf-outputs.json for fast offline access."""
        cache_file = self.build_path / f"{self.stage.name}.tf-outputs.json"
        try:
            data = {
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "outputs": outputs,
            }
            with open(cache_file, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=str)
        except OSError:
            pass  # non-fatal: cache write failures do not affect the deploy

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """terraform show -json <stage>.tfplan  → raw plan data dict"""
        messages: List[str] = []
        data: Dict[str, Any] = {}

        if not self._ready(messages):
            return False, data, messages
        assert self._working_dir is not None
        assert self._plan_file is not None
        assert self._tf is not None

        if not self._plan_file.exists():
            messages.append(f"No saved plan found at {self._plan_file}. Run 'strata deploy run --dry-run' first.")
            return False, data, messages

        messages.append(f"terraform show -json  {self._plan_file.name}")

        try:
            result = self._tf.show(
                str(self._working_dir),
                plan_file=str(self._plan_file),
                json_format=True,
            )
            if result.returncode != 0:
                messages.append(f"terraform show failed:\n{result.stderr}")
                return False, data, messages
            data = json.loads(result.stdout or "{}")
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            messages.append(f"terraform show error: {exc}")
            return False, data, messages

        return True, data, messages

    def save_plan_json(self) -> Tuple[bool, Optional[Path], List[str]]:
        """Save the plan as JSON alongside the binary: {stage}.tfplan → {stage}.tfplan.json.

        Calls show_plan() internally; the plan step must have run first.
        """
        ok, data, msgs = self.show_plan()
        if not ok or not data:
            return False, None, msgs
        assert self._plan_file is not None  # guaranteed by show_plan() / _ready()
        out_path = self._plan_file.parent / f"{self.stage.name}.tfplan.json"
        try:
            out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            msgs.append(f"Could not write plan JSON: {exc}")
            return False, None, msgs
        return True, out_path, msgs

    def drift(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Detect infrastructure drift via a non-destructive terraform plan.

        Runs ``terraform plan -detailed-exitcode`` against a temporary plan file
        so it does not overwrite any existing run-plan.  When changes are found
        (exit code 2), ``terraform show -json`` decodes the plan and sensitive
        values are redacted before returning.

        Returns:
            (success, data, messages)

            ``data["resource_changes"]`` is a list of resource-change dicts
            compatible with the ``terraform show -json`` schema.  Empty list
            means no drift was found.
        """
        messages: List[str] = []
        data: Dict[str, Any] = {"resource_changes": []}
        if not self._ready(messages):
            return False, data, messages
        assert self._working_dir is not None
        assert self._plan_file is not None
        assert self._tf is not None

        # Use a dedicated drift plan file so we never overwrite an existing plan
        drift_plan_file = self._plan_file.parent / f"{self.stage.name}.drift.tfplan"
        messages.append(f"terraform plan (drift check)  \u2192 {drift_plan_file.name}")

        try:
            ctx = inject_tf_vars(self.resolved_values) if self.resolved_values else nullcontext()
            with ctx:
                result = self._tf.plan(
                    str(self._working_dir),
                    out_file=str(drift_plan_file),
                    detailed_exitcode=True,
                    timeout=self._get_timeout("plan", 600),
                )
            if result.returncode == 0:
                messages.append("\u21b3 No drift detected \u2014 infrastructure matches configuration.")
                return True, data, messages
            elif result.returncode == 2:
                pass  # changes present — decode below
            else:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform plan (drift check) failed:\n{output}")
                return False, data, messages
        except RuntimeError as exc:
            messages.append(f"terraform plan (drift check) error: {exc}")
            return False, data, messages

        # Decode the plan to structured JSON
        try:
            show_result = self._tf.show(
                str(self._working_dir),
                plan_file=str(drift_plan_file),
                json_format=True,
            )
            if show_result.returncode != 0:
                messages.append(f"terraform show (drift check) failed:\n{show_result.stderr}")
                return False, data, messages
            plan_data = json.loads(show_result.stdout or "{}")
            raw_changes = plan_data.get("resource_changes", [])
            data["resource_changes"] = self._redact_sensitive_changes(raw_changes)
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            messages.append(f"terraform show (drift check) error: {exc}")
            return False, data, messages
        finally:
            try:
                if drift_plan_file.exists():
                    drift_plan_file.unlink()
            except OSError:
                pass

        n = len(data.get("resource_changes", []))
        messages.append(f"\u21b3 Drift detected: {n} resource(s) have changes.")
        return True, data, messages

    @staticmethod
    def _redact_sensitive_changes(resource_changes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove sensitive values from terraform show -json resource_changes.

        Terraform marks sensitive values via ``before_sensitive`` /
        ``after_sensitive`` in each change block.  When a key maps to ``True``
        (or the entire block is ``True``), the corresponding value is replaced
        with ``"(sensitive)"`` so it is never stored in drift history.

        Resources with action ``no-op`` or ``read`` are excluded (they do not
        represent real infrastructure drift).
        """
        redacted: List[Dict[str, Any]] = []
        for rc in resource_changes:
            change = rc.get("change", {})
            actions = change.get("actions", [])
            if not actions or actions == ["no-op"] or actions == ["read"]:
                continue

            before = change.get("before") or {}
            after = change.get("after") or {}
            before_sensitive = change.get("before_sensitive") or {}
            after_sensitive = change.get("after_sensitive") or {}

            redacted_rc = dict(rc)
            redacted_change = dict(change)
            if before:
                redacted_change["before"] = TerraformDeployer._redact_values(before, before_sensitive)
            if after:
                redacted_change["after"] = TerraformDeployer._redact_values(after, after_sensitive)
            redacted_rc["change"] = redacted_change
            redacted.append(redacted_rc)
        return redacted

    @staticmethod
    def _redact_values(values: Dict[str, Any], sensitive_mask: Any) -> Dict[str, Any]:
        """Apply a sensitive mask to an attribute dict, replacing flagged values.

        ``sensitive_mask`` can be:
        - ``True``  → entire dict is sensitive; replace all values
        - ``dict``  → per-key flags; replace only those marked ``True``
        - anything else → return values unchanged
        """
        if sensitive_mask is True:
            return {k: "(sensitive)" for k in values}
        if isinstance(sensitive_mask, dict):
            result = dict(values)
            for key, is_sensitive in sensitive_mask.items():
                if is_sensitive and key in result:
                    result[key] = "(sensitive)"
            return result
        return values

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ready(self, messages: List[str]) -> bool:
        """Guard: ensure validate_workspace + validate_environment have been called."""
        if self._working_dir is None or self._plan_file is None:
            messages.append("validate_workspace() must be called before running steps")
            return False
        if self._tf is None:
            messages.append("validate_environment() must be called before running steps")
            return False
        return True

    def _get_working_dir(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        iac_model: WorkspaceIacModel,
    ) -> Path:
        """Return the filesystem path where terraform commands should run.

        Fallback used only when no ``solution_controller`` is available (e.g. some
        tests, direct/library use). Mirrors
        ``SolutionController.get_provisioner_path()``'s resolution order exactly —
        ``target_path`` when set, else ``source_path`` — so the deployer's working
        directory always agrees with where the builder copies IaC source, whichever
        path is taken (ADR-0071).
        """
        deployment_build_path = deployment_service.get_build_path(build_path)
        assert iac_model.source is not None  # model validator guarantees source for non-sync provisioners
        target = iac_model.source.target_path or iac_model.source.source_path
        if target is None:
            raise ValueError(f"Provisioner '{iac_model.name}' source has no source_path or target_path defined.")
        return deployment_build_path / target

    def _build_backend_config(self, iac_model: WorkspaceIacModel) -> Tuple[Optional[Dict[str, str]], List[str]]:
        """Extract backend configuration key-value pairs from the IaC model.

        Resolves ``${var:KEY}`` / ``${secret:KEY}`` / ``${feature:KEY}`` expressions
        (ADR-0075) using ``self.resolved_values`` when available. Fails loud — an
        unresolved reference is always an error, never a silent pass-through.

        Returns:
            ``(backend_config, errors)``. When ``errors`` is non-empty the caller
            must not proceed to ``terraform init`` with the (incomplete) config.
        """
        if not iac_model.backend:
            return None, []
        config = iac_model.backend.configuration or {}
        if not config:
            return None, []

        result: Dict[str, str] = {}
        errors: List[str] = []
        for k, v in config.items():
            resolved, resolve_errors = self._resolve_backend_expr(str(v))
            if resolve_errors:
                errors.extend(f"backend config key '{k}': {err}" for err in resolve_errors)
                continue
            result[k] = resolved
        return result, errors

    def _resolve_backend_expr(self, value: str) -> Tuple[str, List[str]]:
        """Resolve ``${var:KEY}`` / ``${secret:KEY}`` / ``${feature:KEY}`` expressions
        in a string value (ADR-0075's shared expression syntax)."""
        if self.resolved_values is None:
            if EXPR_PATTERN.search(value):
                return value, [f"cannot resolve '{value}': no resolved values available"]
            return value, []
        return resolve_expr_string(value, self.resolved_values)

    def _write_deploy_time_vars(
        self,
        resolved: ResolvedValues,
        profile: OutputProfileModel,
        output_path: Path,
    ) -> None:
        """Write (or overwrite) store-backed tfvars files from fully-resolved values.

        Called before ``terraform init`` so integration-backed features and variables
        (Flagsmith, Azure App Config, Vault, …) are available to Terraform.

        Secrets are never written to disk — they are always injected as
        ``TF_VAR_*`` environment variables via ``inject_tf_vars``.
        """
        if profile.should_emit("features") and resolved.features:
            flags: Dict[str, Any] = {k: bool(v) if v is not None else False for k, v in resolved.features.items()}
            output_path.mkdir(parents=True, exist_ok=True)
            (output_path / "flags.auto.tfvars.json").write_text(json.dumps(flags, indent=2), encoding="utf-8")

        if profile.should_emit("variables") and resolved.variables:
            output_path.mkdir(parents=True, exist_ok=True)
            (output_path / "variables.auto.tfvars.json").write_text(
                json.dumps(resolved.variables, indent=2), encoding="utf-8"
            )

    def _get_output_profile_for_provisioner(self) -> Optional[OutputProfileModel]:
        """Return the OutputProfileModel for the current provisioner, if any."""
        if self._iac_model is None:
            return None
        return self._iac_model.output

    @staticmethod
    def _get_terraform_integration(name: str) -> TerraformIntegration:
        """Return the registered TerraformIntegration instance by name."""
        from strata.services.integration_service import IntegrationService

        svc = IntegrationService.get_instance()
        integration = svc.get_integration(name)
        if integration is None:
            raise RuntimeError(
                f"Terraform integration '{name}' is not registered. Ensure integrations are initialized."
            )
        if not isinstance(integration, TerraformIntegration):
            raise RuntimeError(
                f"Integration '{name}' is not a TerraformIntegration (got {type(integration).__name__})."
            )
        return integration
