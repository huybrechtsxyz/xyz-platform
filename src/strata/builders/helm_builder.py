"""Build Helm artifacts from namespace module definitions.

For each namespace that contains at least one module with ``spec.type: helm``,
this builder writes per-module output files to::

    {build_path}/{namespace}/{module}/values.yaml
    {build_path}/{namespace}/{module}/meta.yaml

Security: this builder never writes resolved secret or variable values.
Variable/secret/feature references are emitted as typed ``${var:KEY}`` /
``${secret:KEY}`` / ``${feature:KEY}`` substitution expressions (ADR-0075).
The deployer resolves them at deploy time — secret-shaped values via
``--set-string`` (never written to disk), var/feature-only values via a
rewritten values file.
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

import yaml

from strata.builders.base_builder import BaseBuilder
from strata.models.common_models import ServiceDeployerType
from strata.models.module_model import ModuleServiceModel
from strata.services.deployment_service import DeploymentService
from strata.services.module_service import ModuleService
from strata.utils.resolved_values import collect_expr_refs
from strata.utils.system import resolve_path

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController


class HelmBuilder(BaseBuilder):
    """Builder that generates ``values.yaml`` and ``meta.yaml`` files for each
    Helm module.

    One values file and one meta file are created per Helm module.  Service
    names are prefixed with the module name (``{module}-{service}``); the
    prefix is omitted when the module name equals the service name.
    """

    # ------------------------------------------------------------------
    # BaseBuilder interface
    # ------------------------------------------------------------------

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Generate values.yaml and meta.yaml for each namespace with helm modules.

        Args:
            deployment_service: Fully-loaded deployment service.
            work_path: Workspace root directory used to resolve module file refs.
            build_path: Root build output directory.
            dry_run: When True, skip writing output files.
            solution_controller: Optional controller for canonical path helpers.

        Returns:
            bool: True on success, False on failure.
        """
        try:
            namespace_services = deployment_service.get_namespace_services() or {}

            if not namespace_services:
                if self.verbose:
                    self._messages.append("No namespaces found — skipping helm build")
                return True

            deployment_build_path = deployment_service.get_build_path(build_path)
            template_context = self._build_template_context(deployment_service)

            for ns_name, ns_service in namespace_services.items():
                if not ns_service.is_validated() or not ns_service.model:
                    continue

                ok = self._build_namespace(
                    namespace_name=str(ns_name),
                    ns_service=ns_service,
                    work_path=work_path,
                    deployment_service=deployment_service,
                    build_path=build_path,
                    deployment_build_path=deployment_build_path,
                    dry_run=dry_run,
                    repo_map=repo_map or {},
                    template_context=template_context,
                    solution_controller=solution_controller,
                )
                if not ok:
                    return False

                ok = self._copy_namespace_module_files(
                    namespace_name=str(ns_name),
                    ns_service=ns_service,
                    work_path=work_path,
                    deployment_service=deployment_service,
                    build_path=build_path,
                    deployment_build_path=deployment_build_path,
                    template_context=template_context,
                    dry_run=dry_run,
                    solution_controller=solution_controller,
                )
                if not ok:
                    return False

            # _validate_expr_refs() (ADR-0075) appends to self._errors without
            # returning False per-module, so it must be checked here — every
            # other error path in this loop returns False immediately instead.
            return not self._errors

        except Exception as exc:
            msg = f"Helm build failed: {exc}"
            self.logger.exception("Helm build failed", error=str(exc))
            self._errors.append(msg)
            return False

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Pre-build validation: verify that the deployment service is ready.

        Args:
            deployment_service: Deployment service to validate.
            work_path: Working directory path.
            build_path: Build output directory path.
            solution_controller: Optional solution controller.

        Returns:
            bool: True on success, False on failure.
        """
        if not deployment_service.is_validated():
            self._errors.append("Deployment service is not validated")
            return False

        workspace_service = deployment_service.get_workspace_service()
        if not workspace_service:
            self._errors.append("Workspace service is not available")
            return False

        if self.verbose:
            self._messages.append("Helm pre-build validation passed")

        return True

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Post-build hook.  A workspace with no helm modules is valid.

        Args:
            deployment_service: Deployment service.
            work_path: Working directory path.
            build_path: Build output directory path.
            dry_run: When True, skip output file existence checks.
            solution_controller: Optional solution controller.

        Returns:
            bool: Always True — absence of helm files is not an error.
        """
        if dry_run and self.verbose:
            self._messages.append("[DRY-RUN] Skipping helm output file check")
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_namespace(
        self,
        namespace_name: str,
        ns_service: Any,
        work_path: Path,
        deployment_service: DeploymentService,
        build_path: Path,
        deployment_build_path: Path,
        dry_run: bool,
        repo_map: Optional[Dict[str, str]] = None,
        template_context: Optional[Dict[str, Any]] = None,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Build values.yaml and meta.yaml for all helm modules in one namespace.

        Iterates all modules referenced by the namespace, loads each one, and
        writes per-module output for modules whose ``spec.type`` is ``helm``.

        Args:
            namespace_name: Name of the namespace.
            ns_service: NamespaceService instance.
            work_path: Workspace root for resolving module file references.
            deployment_build_path: Resolved build output directory.
            dry_run: When True, skip file I/O.

        Returns:
            bool: True on success, False on failure.
        """
        modules = ns_service.model.spec.modules
        if not modules:
            return True

        for module_ref in modules:
            try:
                module_path = resolve_path(str(work_path), module_ref.file)
            except (ValueError, Exception) as exc:
                self._errors.append(
                    f"Namespace '{namespace_name}', module '{module_ref.name}': "
                    f"cannot resolve file '{module_ref.file}': {exc}"
                )
                return False

            if not module_path.exists():
                self._errors.append(
                    f"Namespace '{namespace_name}', module '{module_ref.name}': file not found: '{module_path}'"
                )
                return False

            mod_service = ModuleService.load(str(module_path), validate=True)
            if not mod_service.is_validated() or not mod_service.model:
                errs = mod_service.get_validation_errors()
                self._errors.append(
                    f"Namespace '{namespace_name}', module '{module_ref.name}': validation failed: {'; '.join(errs)}"
                )
                return False

            module = mod_service.model
            if module.spec.type != ServiceDeployerType.HELM:
                # Not a helm module — skip silently
                continue

            module_name = str(module.meta.name)
            module_dir = (
                solution_controller.get_module_build_path(deployment_service, build_path, namespace_name, module_name)
                if solution_controller is not None
                else deployment_build_path / namespace_name / module_name
            )
            values_path = module_dir / "values.yaml"
            meta_path = module_dir / "meta.yaml"

            # For local charts (no chart_repository), copy the chart source directory
            # into module_dir so the deployer can reference it as chart_ref.
            source = module.spec.source
            if not source.chart_repository and source.source_path:
                repo_name = str(source.repository) if source.repository else ""
                if repo_map and repo_name and repo_name in repo_map:
                    repo_root = Path(repo_map[repo_name])
                else:
                    repo_root = work_path
                src_dir = repo_root / source.source_path

                # When source.reference is set, extract from the pinned ref using git
                # archive instead of copying from the (potentially different) working
                # tree checkout. SourceModel.reference is generic (valid for any
                # git-based source), not Terraform-specific — helm's local chart copy
                # must honor it too (ADR-0071).
                if source.reference:
                    if dry_run:
                        self._messages.append(
                            f"[DRY-RUN] Would extract helm chart source at ref '{source.reference}': "
                            f"{src_dir} -> {module_dir}"
                        )
                    else:
                        ok, msg = self._extract_source_at_ref(
                            repo_root=repo_root,
                            source_path=source.source_path,
                            ref=source.reference,
                            dest_dir=module_dir,
                            provisioner_name=f"{namespace_name}/{module_name}",
                        )
                        if not ok:
                            self._errors.append(msg)
                            return False
                        if template_context:
                            self._apply_templates_to_dir(module_dir, template_context, exclude_dirs={"templates"})
                        self._messages.append(
                            f"Extracted helm chart source at ref '{source.reference}': {src_dir} -> {module_dir}"
                        )
                elif dry_run:
                    self._messages.append(f"[DRY-RUN] Would copy helm chart source: {src_dir} -> {module_dir}")
                    if not src_dir.exists():
                        self._errors.append(
                            f"Helm chart source not found: {src_dir} "
                            f"(namespace '{namespace_name}', module '{module_name}')"
                        )
                        return False
                else:
                    if not src_dir.exists():
                        self._errors.append(
                            f"Helm chart source not found: {src_dir} "
                            f"(namespace '{namespace_name}', module '{module_name}')"
                        )
                        return False
                    module_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(src_dir, module_dir, dirs_exist_ok=True)
                    if template_context:
                        # Skip templates/ — that subtree is Helm's own Go-template
                        # syntax (e.g. `{{ .Release.Name }}`), rendered by Helm itself
                        # at deploy time with its own .Values/.Release context, not
                        # strata's Jinja2 pass. Applying Jinja2 there would either
                        # crash on unparseable Go-template syntax or silently mangle
                        # it (see the TemplateError handling in _apply_templates_to_dir).
                        self._apply_templates_to_dir(module_dir, template_context, exclude_dirs={"templates"})
                    if self.verbose:
                        self._messages.append(f"Copied helm chart source: {src_dir} -> {module_dir}")

            # Render values.yaml from spec.services when present; omit the file
            # when there are no services (e.g. registry chart + values file via
            # spec.files — the deployer uses the copied values.yaml directly).
            if module.spec.services:
                values_doc, meta_doc = self._render_module_artifacts(
                    namespace_name=namespace_name,
                    module_name=module_name,
                    services=module.spec.services,
                    release_name=module.spec.release_name,
                    kubernetes_namespace=module.spec.kubernetes_namespace,
                )
            else:
                values_doc = None
                meta_doc = {
                    "releaseName": module.spec.release_name if module.spec.release_name else module_name,
                    "namespace": module.spec.kubernetes_namespace
                    if module.spec.kubernetes_namespace
                    else namespace_name,
                }

            # Enrich meta with chart coordinates so the build artifact is
            # self-contained — deployers and GitOps tools (ArgoCD, Flux) can
            # drive the install entirely from meta.yaml without re-reading the
            # module spec.
            if source.chart_repository:
                meta_doc["chartName"] = source.chart_name
                meta_doc["chartVersion"] = source.chart_version
                meta_doc["chartRepository"] = source.chart_repository

            # Merge module-level configuration into values.yaml.  This is the
            # open bag for deployer-specific overrides at the module level
            # (top-level helm values).  For service-less modules this creates
            # the values doc; for modules with services it merges on top.
            if module.spec.configuration:
                if values_doc is None:
                    values_doc = {}
                values_doc.update(module.spec.configuration)

            # Validate configuration keys against chart's default values.yaml
            # (local charts only — registry charts aren't available at build time).
            if module.spec.configuration and not source.chart_repository and source.source_path:
                self._validate_helm_values(
                    configuration=module.spec.configuration,
                    chart_source_dir=src_dir,
                    namespace_name=namespace_name,
                    module_name=module_name,
                )

            # ADR-0075: every ${var:}/${secret:}/${feature:} reference in the
            # rendered values document must resolve against a declared
            # variable/secret/feature — the only build-time signal for a typo'd
            # or undeclared name (the deployer also fails loud at deploy time,
            # but that's much later in the pipeline than a build error).
            if values_doc:
                self._validate_expr_refs(
                    values_doc=values_doc,
                    deployment_service=deployment_service,
                    namespace_name=namespace_name,
                    module_name=module_name,
                )

            if dry_run:
                if self.verbose:
                    if values_doc is not None:
                        self._messages.append(f"[DRY-RUN] Would write: {values_path}")
                    self._messages.append(f"[DRY-RUN] Would write: {meta_path}")
                continue

            module_dir.mkdir(parents=True, exist_ok=True)

            if values_doc is not None:
                try:
                    with values_path.open("w", encoding="utf-8") as fh:
                        yaml.dump(
                            values_doc,
                            fh,
                            default_flow_style=False,
                            sort_keys=False,
                            allow_unicode=True,
                        )
                except OSError as exc:
                    self._errors.append(f"Failed to write '{values_path}': {exc}")
                    return False
                if self.verbose:
                    self._messages.append(f"Wrote helm values: {values_path}")

            try:
                with meta_path.open("w", encoding="utf-8") as fh:
                    yaml.dump(
                        meta_doc,
                        fh,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
            except OSError as exc:
                self._errors.append(f"Failed to write '{meta_path}': {exc}")
                return False

            if self.verbose:
                self._messages.append(f"Wrote helm meta: {meta_path}")

        return True

    def _copy_namespace_module_files(
        self,
        namespace_name: str,
        ns_service: Any,
        work_path: Path,
        deployment_service: DeploymentService,
        build_path: Path,
        deployment_build_path: Path,
        template_context: Dict[str, Any],
        dry_run: bool,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Copy ``spec.files`` entries for all helm modules in one namespace.

        Output lands in ``{build}/{namespace}/{module}/`` alongside values.yaml.

        Args:
            namespace_name: Name of the namespace.
            ns_service: NamespaceService instance.
            work_path: Workspace root for resolving file references.
            deployment_build_path: Resolved build output directory.
            template_context: STRATA_* substitution variables.
            dry_run: When True, log what would happen but skip file I/O.

        Returns:
            bool: True on success, False on failure.
        """
        modules = ns_service.model.spec.modules
        if not modules:
            return True

        for module_ref in modules:
            try:
                module_path = resolve_path(str(work_path), module_ref.file)
            except (ValueError, Exception):
                continue  # already reported by _build_namespace
            if not module_path.exists():
                continue

            mod_service = ModuleService.load(str(module_path), validate=True)
            if not mod_service.is_validated() or not mod_service.model:
                continue

            module = mod_service.model
            if module.spec.type != ServiceDeployerType.HELM:
                continue
            if not module.spec.files:
                continue

            module_name = str(module.meta.name)
            dest_dir = (
                solution_controller.get_module_build_path(deployment_service, build_path, namespace_name, module_name)
                if solution_controller is not None
                else deployment_build_path / namespace_name / module_name
            )
            label = f"Namespace '{namespace_name}', module '{module_name}'"

            if not self._copy_module_files(
                files=module.spec.files,
                work_path=work_path,
                dest_dir=dest_dir,
                template_context=template_context,
                module_label=label,
                dry_run=dry_run,
            ):
                return False

        return True

    def _render_module_artifacts(
        self,
        namespace_name: str,
        module_name: str,
        services: List[ModuleServiceModel],
        release_name: Optional[str],
        kubernetes_namespace: Optional[str],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Render values.yaml and meta.yaml dicts for a helm module.

        Args:
            namespace_name: Namespace name (used as default Kubernetes namespace).
            module_name: Module name (used for service key prefix and release name default).
            services: List of ``ModuleServiceModel`` instances.
            release_name: Optional override for the Helm release name.
            kubernetes_namespace: Optional override for the Kubernetes namespace.

        Returns:
            Tuple of (values_doc dict, meta_doc dict).
        """

        def prefixed(svc_name: str) -> str:
            """Prefix service name with module name unless they are the same."""
            return svc_name if svc_name == module_name else f"{module_name}-{svc_name}"

        values_doc: Dict[str, Any] = {}

        for svc in services:
            svc_name = str(svc.name)
            entry_name = prefixed(svc_name)
            entry: Dict[str, Any] = {}

            # Environment variables — never write resolved secret/variable values
            if svc.environment:
                env_dict: Dict[str, str] = {}
                for env in svc.environment:
                    if env.value is not None:
                        env_dict[env.key] = env.value
                    elif env.var is not None:
                        env_dict[env.key] = f"${{{env.var}}}"
                    elif env.secret is not None:
                        env_dict[env.key] = f"${{{env.secret}}}"
                    elif env.feature is not None:
                        env_dict[env.key] = f"${{{env.feature}}}"
                if env_dict:
                    entry["env"] = env_dict

            # PersistentVolumeClaim entries — only for mounts with storage_class set
            if svc.mounts:
                persistence: Dict[str, Any] = {}
                for mount in svc.mounts:
                    if mount.storage_class is not None:
                        pvc_key = str(mount.name) if mount.name is not None else "data"
                        pvc_entry: Dict[str, Any] = {"storageClass": mount.storage_class}
                        pvc_entry["accessMode"] = mount.access_mode or "ReadWriteOnce"
                        if mount.storage_size:
                            pvc_entry["size"] = mount.storage_size
                        persistence[pvc_key] = pvc_entry
                if persistence:
                    entry["persistence"] = persistence

            # Merge service-level configuration overrides verbatim (last wins)
            if svc.configuration:
                entry.update(svc.configuration)

            values_doc[entry_name] = entry

        meta_doc: Dict[str, Any] = {
            "releaseName": release_name if release_name else module_name,
            "namespace": kubernetes_namespace if kubernetes_namespace else namespace_name,
        }

        return values_doc, meta_doc

    def _validate_helm_values(
        self,
        configuration: Dict[str, Any],
        chart_source_dir: Path,
        namespace_name: str,
        module_name: str,
    ) -> None:
        """Validate configuration keys against the chart's default values.yaml.

        Compares top-level and one-level-deep keys from module.spec.configuration
        against the chart's default values. Undeclared keys are reported as errors
        with fuzzy-match suggestions. Does not block the build — warnings only.

        Args:
            configuration: The module.spec.configuration dict to validate.
            chart_source_dir: Path to the chart source directory.
            namespace_name: Namespace name for error messages.
            module_name: Module name for error messages.
        """
        from strata.validators.helm_input_validator import (
            check_helm_values,
            parse_chart_values,
        )

        chart_values = parse_chart_values(chart_source_dir)
        if not chart_values:
            return  # No default values.yaml — nothing to validate against

        module_label = f"{namespace_name}/{module_name}"
        errors, warnings = check_helm_values(configuration, chart_values, module_label)

        for error in errors:
            self._messages.append(f"⚠ {error}")
        for warning in warnings:
            self._messages.append(f"⚠ {warning}")

    def _validate_expr_refs(
        self,
        values_doc: Dict[str, Any],
        deployment_service: DeploymentService,
        namespace_name: str,
        module_name: str,
    ) -> None:
        """Cross-check ``${var:}``/``${secret:}``/``${feature:}`` references in the
        rendered values document against declared variables/secrets/features
        (ADR-0075). An undeclared name blocks the build.

        Unlike ``TerraformBuilder``'s equivalent check, this is not scoped to a
        stage's ``secrets:`` allowlist — Helm modules aren't associated with a
        deployment stage at build time the way Terraform provisioners are, so
        this checks against everything declared anywhere in the environment.
        """
        refs = collect_expr_refs(values_doc)
        if not refs:
            return

        env_service = deployment_service.get_environment_service()
        if env_service is None or env_service.model is None:
            return

        declared: Set[str] = set()
        for var in env_service.get_variables():
            declared.add(var.key)
        for feat in env_service.get_features():
            declared.add(feat.key)
        if env_service.model.spec and env_service.model.spec.secrets:
            declared.update(secret.key for secret in env_service.model.spec.secrets)

        for kind, key in sorted(refs):
            if key not in declared:
                self._errors.append(
                    f"Namespace '{namespace_name}', module '{module_name}': values reference "
                    f"'${{{kind}:{key}}}', but '{key}' is not declared as a variable, secret, or feature."
                )
