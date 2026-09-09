"""Service for loading and validating workspace configurations."""

from typing import Any, Dict, List, Optional, Set, Tuple, Type, Union, cast

from strata.exceptions import InvalidReferenceError
from strata.models.configuration_model import ConfigurationModel
from strata.models.firewall_model import FirewallModel
from strata.models.workspace_model import WorkspaceModel
from strata.services.base_service import BaseService
from strata.services.configuration_service import ConfigurationService
from strata.services.dns_service import DnsService
from strata.services.firewall_service import FirewallService
from strata.services.module_service import ModuleService
from strata.services.namespace_service import NamespaceService
from strata.services.network_service import NetworkService
from strata.services.provider_service import ProviderService
from strata.services.resource_service import ResourceService


class WorkspaceService(BaseService["WorkspaceModel"]):
    """Service for handling workspace configurations."""

    # Initialization

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """
        Initialize the WorkspaceService.

        _related_services = {
            "providers": {"provider_name": ProviderService, ...},
        }
        """
        super().__init__(path=path, data=data)
        self.model = None
        self._related_services: Optional[Dict[str, Dict[str, BaseService]]] = None
        self._validation_errors: List[str] = []
        self._structured_errors: List[InvalidReferenceError] = []

    # Lifecycle hooks

    def on_init(self) -> None:
        """Lifecycle hook: called after __init__ completes."""
        pass

    def on_ready(self) -> None:
        """Lifecycle hook: called after validation succeeds."""
        pass

    def on_shutdown(self) -> None:
        """Lifecycle hook: called before cleanup/destruction."""
        pass

    # Abstract methods

    def _get_model_class(self):
        """Return the WorkspaceModel class for validation."""
        return WorkspaceModel

    def _validate_dynamic(
        self,
        configuration_model: Optional[ConfigurationModel] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        Validates:
        1. Provisioner repository references (workspace.spec.provisioners -> solution registered repos)
        2. Provisioner validity (validated via ProvisionerType enum)
        3. Module repository references (loaded modules -> solution registered repos)
        4. Topology provider references (workspace.spec.providers)
        5. Topology provisioner references (workspace.spec.provisioners)
        6. Component roles against configuration topology definitions

        Args:
            configuration_model: Optional ConfigurationModel for cross-validation
            work_path: Optional working path for file resolution

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        if not configuration_model or not self.model:
            return True, []

        errors = []
        # Note: Do not call _ensure_validated() here as this is called during validate()
        # before the model is marked as validated

        # Get workspace provider names and create provider map
        workspace_provider_names = {p.name for p in self.model.spec.providers}

        # Get workspace provisioner names (topology references provisioners by name)
        workspace_provisioner_names = {p.name for p in self.model.spec.provisioners}

        # Build solution repository names from the repo map registered via `strata repo add`
        # (stored in solution.json, set on self._repo_map before _validate_dynamic is called).
        # Note: configuration_model.spec.remotes is for named remote endpoints — a different concept.
        solution_repository_names: Set[str] = set(self._repo_map.keys()) if self._repo_map else set()

        # STEP 1: Validate provisioner repository references
        # Check that each provisioner's source.repository exists in the solution's registered repos
        for provisioner in self.model.spec.provisioners:
            if provisioner.source and provisioner.source.repository:
                if provisioner.source.repository not in solution_repository_names:
                    error = InvalidReferenceError(
                        source_type="Provisioner",
                        source_name=provisioner.name,
                        reference_type="repository",
                        reference_value=provisioner.source.repository,
                    )
                    self._structured_errors.append(error)
                    errors.append(str(error))

        # STEP 2: Provisioner name validity is validated by the WorkspaceSpecModel model validator

        # STEP 3: Validate module repository references (if modules are defined in resources)
        if self.model and self.model.spec.resources:
            for resource in self.model.spec.resources:
                if resource.modules:
                    # Note: Modules in workspace are references with file paths.
                    # The actual module source.repository is in the module file itself.
                    # For thorough validation, we would need to load each module file,
                    # but that's expensive. This check is documented for when module
                    # services are loaded via related_services.
                    pass

        # Validate module repository references from loaded module services (if available)
        if self._related_services and "modules" in self._related_services:
            for module_name, module_service in self._related_services["modules"].items():
                if (
                    hasattr(module_service, "model")
                    and module_service.model is not None
                    and hasattr(module_service.model, "spec")
                    and hasattr(module_service.model.spec, "source")
                    and module_service.model.spec.source
                ):
                    module_repo = module_service.model.spec.source.repository
                    if module_repo not in solution_repository_names:
                        error = InvalidReferenceError(
                            source_type="Module",
                            source_name=module_name,
                            reference_type="repository",
                            reference_value=module_repo,
                        )
                        self._structured_errors.append(error)
                        errors.append(str(error))

        # Build topology configuration map and roles map from configuration
        topology_config_map = {}
        topology_roles_map = {}
        if configuration_model.spec.topologies:
            for topo_config in configuration_model.spec.topologies:
                # Store the full config object
                topology_config_map[topo_config.type] = topo_config
                # Build roles map
                if topo_config.components:
                    topo_roles: Set[str] = {comp.role for comp in topo_config.components}
                    topology_roles_map[topo_config.type] = topo_roles

        # Validate topology provider, provisioner, and component role references
        for topology in self.model.spec.topology:
            # Check provider reference
            if topology.provider not in workspace_provider_names:
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="provider",
                    reference_value=topology.provider,
                )
                self._structured_errors.append(error)
                errors.append(str(error))

            # Check provisioner reference (by name)
            if topology.provisioner not in workspace_provisioner_names:
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="provisioner",
                    reference_value=topology.provisioner,
                )
                self._structured_errors.append(error)
                errors.append(str(error))

            # Validate component roles against configuration topology
            topology_type_normalized = topology.type.lower().replace("-", "_")

            # Find matching configuration topology (with normalization)
            valid_roles: Optional[Set[str]] = None
            matching_config = None
            for config_type, topo_config in topology_config_map.items():
                config_type_normalized = config_type.lower().replace("-", "_")
                if config_type_normalized == topology_type_normalized:
                    valid_roles = topology_roles_map.get(config_type)
                    matching_config = topo_config
                    break

            # Check if topology type is allowed (additional_topologies validation)
            if matching_config is None and not configuration_model.spec.additional_topologies:
                # available_topologies = sorted(topology_config_map.keys())
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="topology type",
                    reference_value=topology.type,
                )
                self._structured_errors.append(error)
                errors.append(str(error))
                continue  # Skip component validation for this topology

            # If topology type is defined in configuration, validate component roles
            if valid_roles:
                additional_components_allowed = matching_config.additional_components if matching_config else True

                for component in topology.components:
                    # Resolve role from component or resource reference
                    component_role = self._get_component_role(component)
                    if not component_role:
                        continue  # Skip components without role
                    component_role_normalized = component_role.lower().replace("-", "_")
                    valid_roles_normalized = {r.lower().replace("-", "_") for r in valid_roles}

                    if component_role_normalized not in valid_roles_normalized:
                        # Check if additional components are allowed
                        if not additional_components_allowed:
                            # Get component name for error message
                            component_name = component.resource
                            error = InvalidReferenceError(
                                source_type="Component",
                                source_name=component_name,
                                reference_type="role",
                                reference_value=component_role,
                            )
                            self._structured_errors.append(error)
                            errors.append(str(error))

            # Validate component constraints from configuration (min/max count, required, uses_module)
            if matching_config and matching_config.components:
                config_validation_errors = self._validate_component_constraints(topology, matching_config)
                errors.extend(config_validation_errors)

        # STEP 5: Validate that all file: references resolve to existing files on disk
        if work_path:
            config_repo_map = configuration_model.get_remote_map() if configuration_model else {}
            repo_map = {**config_repo_map, **(self._repo_map or {})}
            file_refs = []
            for p in self.model.spec.providers:
                file_refs.append((f"Provider '{p.name}'", p.file))
            if self.model.spec.resources:
                for r in self.model.spec.resources:
                    if r.file:
                        file_refs.append((f"Resource '{r.name}'", r.file))
                    if r.modules:
                        for m in r.modules:
                            file_refs.append((f"Resource '{r.name}' module '{m.name}'", m.file))
            if self.model.spec.namespaces:
                for ns in self.model.spec.namespaces:
                    file_refs.append((f"Namespace '{ns.name}'", ns.file))
            if self.model.spec.firewalls:
                for fw in self.model.spec.firewalls:
                    file_refs.append((f"Firewall '{fw.name}'", fw.file))
            if self.model.spec.dns_zones:
                for dz in self.model.spec.dns_zones:
                    file_refs.append((f"DNS zone '{dz.name}'", dz.file))
            if self.model.spec.networks:
                for net in self.model.spec.networks:
                    file_refs.append((f"Network '{net.name}'", net.file))
            errors.extend(self._validate_file_refs(work_path, repo_map, file_refs))

        return len(errors) == 0, errors

    def _validate_component_constraints(self, topology, matching_config) -> List[str]:
        """
        Validate component constraints from configuration.

        Validates:
        - uses_module: Component must have a module if required
        - required: Topology must include required components
        - min_count: Topology must have at least min_count instances
        - max_count: Topology must not exceed max_count instances (0 = unlimited)

        Args:
            topology: WorkspaceTopologyModel instance
            matching_config: ConfigurationTopologyModel instance

        Returns:
            List[str]: List of validation error messages
        """
        errors = []

        # Build a map of component roles to their instances in the workspace topology
        # Count both full definitions and simple resource references by resolving roles
        component_role_counts: Dict[str, int] = {}
        component_role_modules: Dict[str, List[str]] = {}
        for component in topology.components:
            # Resolve role from component or resource reference
            component_role = self._get_component_role(component)
            if not component_role:
                continue  # Skip components without role
            role_normalized = component_role.lower().replace("-", "_")
            component_role_counts[role_normalized] = component_role_counts.get(role_normalized, 0) + 1
            # Check if resource has modules (component is just a reference)
            resource = self._get_resource_by_name(component.resource)
            if resource and resource.modules:
                component_role_modules.setdefault(role_normalized, []).append(component.resource)

        # Validate each component configuration constraint
        for config_comp in matching_config.components:
            if not config_comp.role:
                continue  # Skip components without role
            role_normalized = config_comp.role.lower().replace("-", "_")
            actual_count = component_role_counts.get(role_normalized, 0)

            # Validate required components
            if config_comp.required and actual_count == 0:
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="required component role",
                    reference_value=config_comp.role,
                )
                self._structured_errors.append(error)
                errors.append(str(error))
                continue

            # Skip further validation if component is not present
            if actual_count == 0:
                continue

            # Validate min_count constraint
            if config_comp.min_count and actual_count < config_comp.min_count:
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="component role (min_count)",
                    reference_value=f"{config_comp.role} (has {actual_count}, needs {config_comp.min_count})",
                )
                self._structured_errors.append(error)
                errors.append(str(error))

            # Validate max_count constraint (0 means unlimited)
            if config_comp.max_count and config_comp.max_count > 0 and actual_count > config_comp.max_count:
                error = InvalidReferenceError(
                    source_type="Topology",
                    source_name=topology.name,
                    reference_type="component role (max_count)",
                    reference_value=f"{config_comp.role} (has {actual_count}, max {config_comp.max_count})",
                )
                self._structured_errors.append(error)
                errors.append(str(error))

            # Validate uses_module constraint
            if config_comp.uses_module:
                components_without_module = []
                for component in topology.components:
                    # Resolve role from component or resource reference
                    comp_role = self._get_component_role(component)
                    if not comp_role:
                        continue
                    comp_role_normalized = comp_role.lower().replace("-", "_")
                    # Check if resource has modules (component is just a reference)
                    resource = self._get_resource_by_name(component.resource)
                    if comp_role_normalized == role_normalized and (not resource or not resource.modules):
                        components_without_module.append(component.resource)

                if components_without_module:
                    error = InvalidReferenceError(
                        source_type="Component",
                        source_name=", ".join(components_without_module),
                        reference_type="module (required by role)",
                        reference_value=config_comp.role,
                    )
                    self._structured_errors.append(error)
                    errors.append(str(error))

        return errors

    def _get_resource_by_name(self, resource_name: str) -> Optional[Any]:
        """
        Get a resource definition by its name.

        Args:
            resource_name: Name of the resource to find

        Returns:
            WorkspaceResourceModel instance, or None if not found
        """
        if self.model and self.model.spec.resources:
            for resource_ref in self.model.spec.resources:
                if resource_ref.name == resource_name:
                    return resource_ref
        return None

    def _get_component_role(self, component) -> Optional[str]:
        """
        Get the role for a component by resolving its resource reference.

        Args:
            component: WorkspaceComponentModel instance (has only 'resource' field)

        Returns:
            The role from the referenced resource, or None if not found
        """
        # Component only has a resource name reference - look up role from resources
        if isinstance(component.resource, str):
            # Find the resource definition
            if self.model and self.model.spec.resources:
                for resource_ref in self.model.spec.resources:
                    if resource_ref.name == component.resource:
                        return resource_ref.role

        return None

    # Service Methods

    def get_validation_errors(self) -> List[str]:
        """Return all validation errors: Phase 1 (Pydantic) errors from _errors plus
        dynamic errors accumulated in _validation_errors."""
        return self._errors + self._validation_errors

    def load_workspace_services(
        self, objects_path: Optional[str] = None, repo_map: Optional[Dict[str, str]] = None
    ) -> Tuple[Dict[str, Dict[str, BaseService]], bool]:
        """
        Load all related services (providers, resources, namespaces, firewalls) into a dictionary.

        Uses service cache to avoid re-parsing the same files.

        Args:
            objects_path: Optional base directory for resolving relative file paths.
                          If None, uses the directory of the workspace file.

        Returns:
            Tuple containing:
            - Dict with structure:
              {
                  "providers": {"provider_name": ProviderService, ...},
                  "resources": {"resource_name": ResourceService, ...},
                  "namespaces": {"namespace_name": NamespaceService, ...},
                  "firewalls": {"firewall_name": FirewallService, ...}
              }
            - bool: Success status (True if all services loaded successfully)
        """
        # Return cached services if already loaded
        if self._related_services is not None:
            self.logger.debug("Returning cached related services")
            return self._related_services, True

        self._ensure_validated()
        self.logger.info(
            "Loading related services for workspace",
            workspace_name=self.get_name(),
        )
        success = True

        # Validate objects_path parameter
        if objects_path is None:
            error_msg = (
                "objects_path parameter is required for loading related services. "
                "Paths in workspace YAML are relative to project root."
            )
            self.logger.error(
                error_msg,
                workspace_name=self.get_name(),
            )
            raise ValueError(error_msg)

        services: Dict[str, Dict[str, BaseService]] = {
            "providers": {},
            "resources": {},
            "namespaces": {},
            "firewalls": {},
            "dns_zones": {},
            "networks": {},
            "modules": {},
        }
        if self.model is None:
            error_msg = "Workspace model is not loaded. Cannot load related services."
            self.logger.error(error_msg)
            raise ValueError(error_msg)
        workspace: WorkspaceModel = self.model

        # Build repo_map once for all @repo_name/... path resolutions in this call.
        # Merge solution-level map (caller-supplied) with config-service map.
        # Solution names take precedence so @haven/... refs resolve correctly.
        config_repo_map: Dict[str, str] = ConfigurationService.get_instance().get_remote_map()
        repo_map = {**config_repo_map, **(repo_map or {})}

        def _load_simple_services(
            refs: Optional[list],
            service_cls: Type[BaseService],
            bucket_key: str,
            label: str,
        ) -> None:
            """Load a list of file-referenced services into *services[bucket_key]*.

            Handles the common try / validate / log / error pattern used for
            DNS zones, firewalls, providers, and namespaces.
            """
            nonlocal success
            if not refs:
                return
            self.logger.debug(f"Loading {label}", count=len(refs))
            for ref in refs:
                ref_path = self._resolve_file_path(ref.file, objects_path, repo_map)
                try:
                    svc = service_cls.load(ref_path, validate=True)
                    if svc.is_validated():
                        services[bucket_key][ref.name] = svc
                        self.logger.debug(f"Loaded {label.rstrip('s')}", name=ref.name, path=ref_path)
                    else:
                        success = False
                        errs = svc.get_validation_errors()
                        self._validation_errors.append(f"{label.rstrip('s').title()} '{ref.name}' validation failed")
                        self._validation_errors.extend(errs)
                        self.logger.warning(
                            f"{label.rstrip('s').title()} validation failed",
                            name=ref.name,
                            path=ref_path,
                            error_count=len(errs),
                        )
                except Exception as e:
                    success = False
                    self._validation_errors.append(
                        f"Failed to load {label.rstrip('s')} '{ref.name}' from {ref_path}: {str(e)}"
                    )
                    self.logger.error(
                        f"Failed to load {label.rstrip('s')}",
                        name=ref.name,
                        path=ref_path,
                        exc_info=True,
                    )

        # Load simple file-referenced services
        _load_simple_services(workspace.spec.dns_zones, DnsService, "dns_zones", "DNS zones")
        _load_simple_services(workspace.spec.firewalls, FirewallService, "firewalls", "firewalls")
        _load_simple_services(workspace.spec.providers, ProviderService, "providers", "providers")
        _load_simple_services(workspace.spec.namespaces, NamespaceService, "namespaces", "namespaces")
        _load_simple_services(workspace.spec.networks, NetworkService, "networks", "networks")

        # Load resource services from workspace resources section
        # Resources are defined in workspace.spec.resources with file references
        # Components in topology reference these resources by name
        if workspace.spec.resources:
            self.logger.debug("Loading resources", count=len(workspace.spec.resources))
            for resource_ref in workspace.spec.resources:
                # Skip externally-managed resources (no file to load)
                if resource_ref.managed_by:
                    resource_key = resource_ref.name
                    # Managed resources have no backing service (no file to load) — the
                    # dict is declared as Dict[str, BaseService] but this sentinel case
                    # is handled by callers via .get()/len() only, never by iterating
                    # and calling methods on the value.
                    services["resources"][resource_key] = None  # type: ignore[assignment]
                    self.logger.debug(
                        "Skipping managed resource", name=resource_key, managed_by=resource_ref.managed_by
                    )
                    continue
                if resource_ref.file is None:
                    # Unreachable in practice: WorkspaceResourceModel validation enforces
                    # that exactly one of `file` or `managed_by` is set.
                    continue
                resource_path = self._resolve_file_path(resource_ref.file, objects_path, repo_map)
                resource_key = resource_ref.name
                if resource_key not in services["resources"]:
                    try:
                        # Use service cache to avoid re-parsing same files
                        rx_service: ResourceService = ResourceService.load(resource_path, validate=True)
                        if rx_service.is_validated():
                            services["resources"][resource_key] = rx_service
                            self.logger.debug("Loaded resource", name=resource_key, path=resource_path)

                            # Merge multiple firewalls if resource references more than one
                            if resource_ref.firewalls and len(resource_ref.firewalls) > 0:
                                self.logger.debug(
                                    "Merging firewalls for resource",
                                    resource=resource_key,
                                    count=len(resource_ref.firewalls),
                                )
                                firewall_services: List[Union[FirewallModel, FirewallService]] = []
                                for fw_name in resource_ref.firewalls:
                                    fw_service_raw = services["firewalls"].get(fw_name)
                                    if fw_service_raw:
                                        firewall_services.append(cast(FirewallService, fw_service_raw))
                                    else:
                                        self.logger.warning(
                                            "Firewall not found for resource", firewall=fw_name, resource=resource_key
                                        )

                                if firewall_services:
                                    try:
                                        merged_firewall = FirewallService.merge_firewalls(firewall_services)
                                        rx_service.set_merged_firewall(merged_firewall)
                                        self.logger.debug(
                                            "Merged firewalls for resource",
                                            resource=resource_key,
                                            count=len(firewall_services),
                                        )
                                    except Exception as e:
                                        success = False
                                        self._validation_errors.append(
                                            f"Failed to merge firewalls for resource '{resource_key}': {str(e)}"
                                        )
                                        self.logger.error(
                                            "Failed to merge firewalls for resource",
                                            resource=resource_key,
                                            firewall_count=len(firewall_services),
                                            exc_info=True,
                                        )

                        else:
                            success = False
                            errors = rx_service.get_validation_errors()
                            self._validation_errors.append(f"Resource '{resource_ref.name}' validation failed")
                            self._validation_errors.extend(errors)
                            self.logger.warning(
                                "Resource validation failed",
                                name=resource_ref.name,
                                path=resource_path,
                                error_count=len(errors),
                            )

                    except Exception as e:
                        success = False
                        self._validation_errors.append(
                            f"Failed to load resource '{resource_ref.name}' from {resource_path}: {str(e)}"
                        )
                        self.logger.error(
                            "Failed to load resource",
                            name=resource_ref.name,
                            path=resource_path,
                            exc_info=True,
                        )

        # Load module services referenced by resources
        if workspace.spec.resources:
            for resource_ref in workspace.spec.resources:
                if resource_ref.modules:
                    self.logger.debug(
                        "Loading modules for resource", resource=resource_ref.name, count=len(resource_ref.modules)
                    )
                    for module_ref in resource_ref.modules:
                        module_path = self._resolve_file_path(module_ref.file, objects_path, repo_map)
                        # Use resource_name:module_name as key to avoid conflicts
                        module_key = f"{resource_ref.name}:{module_ref.name}"

                        try:
                            mod_service: ModuleService = ModuleService.load(module_path, validate=True)
                            if mod_service.is_validated():
                                services["modules"][module_key] = mod_service
                                self.logger.debug(
                                    "Loaded module for resource",
                                    module=module_ref.name,
                                    resource=resource_ref.name,
                                    path=module_path,
                                )
                            else:
                                success = False
                                errors = mod_service.get_validation_errors()
                                self._validation_errors.append(
                                    f"Module '{module_ref.name}' for resource '{resource_ref.name}' validation failed"
                                )
                                self._validation_errors.extend(errors)
                                self.logger.warning(
                                    "Module validation failed",
                                    name=module_ref.name,
                                    resource=resource_ref.name,
                                    path=module_path,
                                    error_count=len(errors),
                                )
                        except Exception as e:
                            success = False
                            self._validation_errors.append(
                                f"Failed to load module '{module_ref.name}' for resource '{resource_ref.name}' from {module_path}: {str(e)}"
                            )
                            self.logger.error(
                                "Failed to load module for resource",
                                module=module_ref.name,
                                resource=resource_ref.name,
                                path=module_path,
                                exc_info=True,
                            )

        # Load module services referenced by namespaces
        if workspace.spec.namespaces:
            for namespace_ref in workspace.spec.namespaces:
                # First ensure the namespace service is loaded
                namespace_service = services["namespaces"].get(namespace_ref.name)
                ns_model = getattr(namespace_service, "model", None) if namespace_service else None
                ns_spec = getattr(ns_model, "spec", None) if ns_model else None
                if namespace_service and ns_spec and hasattr(ns_spec, "modules") and ns_spec.modules:
                    self.logger.debug(
                        "Loading modules for namespace",
                        namespace=namespace_ref.name,
                        count=len(ns_spec.modules),
                    )
                    for module_ref in ns_spec.modules:
                        module_path = self._resolve_file_path(module_ref.file, objects_path, repo_map)
                        # Use namespace_name:module_name as key to avoid conflicts
                        module_key = f"{namespace_ref.name}:{module_ref.name}"

                        try:
                            mod_service = ModuleService.load(module_path, validate=True)
                            if mod_service.is_validated():
                                services["modules"][module_key] = mod_service
                                self.logger.debug(
                                    "Loaded module for namespace",
                                    module=module_ref.name,
                                    namespace=namespace_ref.name,
                                    path=module_path,
                                )
                            else:
                                success = False
                                errors = mod_service.get_validation_errors()
                                self._validation_errors.append(
                                    f"Module '{module_ref.name}' for namespace '{namespace_ref.name}' validation failed"
                                )
                                self._validation_errors.extend(errors)
                                self.logger.warning(
                                    "Module validation failed",
                                    name=module_ref.name,
                                    namespace=namespace_ref.name,
                                    path=module_path,
                                    error_count=len(errors),
                                )
                        except Exception as e:
                            success = False
                            self._validation_errors.append(
                                f"Failed to load module '{module_ref.name}' for namespace '{namespace_ref.name}' from {module_path}: {str(e)}"
                            )
                            self.logger.error(
                                "Failed to load module for namespace",
                                module=module_ref.name,
                                namespace=namespace_ref.name,
                                path=module_path,
                                exc_info=True,
                            )

        # Cache services if successful
        if success:
            self._related_services = services
            self.logger.info(
                "All related services loaded successfully",
                workspace_name=self.get_name(),
                providers=len(services["providers"]),
                resources=len(services["resources"]),
                modules=len(services["modules"]),
                namespaces=len(services["namespaces"]),
                firewalls=len(services["firewalls"]),
                dns_zones=len(services["dns_zones"]),
                networks=len(services["networks"]),
            )
        else:
            self.logger.warning(
                "Some related services failed to load",
                workspace_name=self.get_name(),
                error_count=len(self._validation_errors),
            )

        return services, success

    def get_dns_services(self) -> Optional[Dict[str, DnsService]]:
        """Get all DNS zone services keyed by name."""
        value = self._get_workspace_related_services("dns_zones", None)
        if value is not None and isinstance(value, dict):
            casted = {k: cast(DnsService, v) for k, v in value.items() if isinstance(v, DnsService)}
            return casted
        return None

    def get_dns_service(self, dns_name: str) -> Optional[DnsService]:
        """Get a specific DNS zone service by name."""
        value = self._get_workspace_related_services("dns_zones", dns_name)
        if value is not None and isinstance(value, DnsService):
            return cast(DnsService, value)
        return None

    def get_firewall_services(self) -> Optional[Dict[str, FirewallService]]:
        """Get all firewall services keyed by name."""
        value = self._get_workspace_related_services("firewalls", None)
        if value is not None and isinstance(value, dict):
            casted = {k: cast(FirewallService, v) for k, v in value.items() if isinstance(v, FirewallService)}
            return casted
        return None

    def get_firewall_service(self, firewall_name: str) -> Optional[FirewallService]:
        """Get a specific firewall service by name."""
        value = self._get_workspace_related_services("firewalls", firewall_name)
        if value is not None and isinstance(value, FirewallService):
            return cast(FirewallService, value)
        return None

    def get_network_services(self) -> Optional[Dict[str, NetworkService]]:
        """Get all network topology services keyed by name."""
        value = self._get_workspace_related_services("networks", None)
        if value is not None and isinstance(value, dict):
            casted = {k: cast(NetworkService, v) for k, v in value.items() if isinstance(v, NetworkService)}
            return casted
        return None

    def get_network_service(self, network_name: str) -> Optional[NetworkService]:
        """Get a specific network topology service by name."""
        value = self._get_workspace_related_services("networks", network_name)
        if value is not None and isinstance(value, NetworkService):
            return cast(NetworkService, value)
        return None

    def get_module_services(self) -> Optional[Dict[str, ModuleService]]:
        """Get all module services keyed by resource:module name."""
        value = self._get_workspace_related_services("modules", None)
        if value is not None and isinstance(value, dict):
            casted = {k: cast(ModuleService, v) for k, v in value.items() if isinstance(v, ModuleService)}
            return casted
        return None

    def get_module_service(self, resource_name: str, module_name: str) -> Optional[ModuleService]:
        """Get a specific module service by resource and module name."""
        module_key = f"{resource_name}:{module_name}"
        value = self._get_workspace_related_services("modules", module_key)
        if value is not None and isinstance(value, ModuleService):
            return cast(ModuleService, value)
        return None

    def get_namespace_services(
        self,
    ) -> Optional[Dict[str, NamespaceService]]:
        """Get all namespace services keyed by name."""
        value = self._get_workspace_related_services("namespaces", None)
        if value is not None and isinstance(value, dict):
            casted = {k: cast(NamespaceService, v) for k, v in value.items() if isinstance(v, NamespaceService)}
            return casted
        return None

    def get_namespace_service(self, namespace_name: str) -> Optional[NamespaceService]:
        """Get a specific namespace service by name."""
        value = self._get_workspace_related_services("namespaces", namespace_name)
        if value is not None and isinstance(value, NamespaceService):
            return cast(NamespaceService, value)
        return None

    def get_provider_services(self) -> Optional[Dict[str, ProviderService]]:
        """Get all provider services keyed by name."""
        value = self._get_workspace_related_services("providers", None)
        if value is not None and isinstance(value, dict):
            casted = {k: cast(ProviderService, v) for k, v in value.items() if isinstance(v, ProviderService)}
            return casted
        return None

    def get_provider_service(self, provider_name: str) -> Optional[ProviderService]:
        """Get a specific provider service by name."""
        value = self._get_workspace_related_services("providers", provider_name)
        if value is not None and isinstance(value, ProviderService):
            return cast(ProviderService, value)
        return None

    def replace_provider_service(self, provider_name: str, service: ProviderService) -> None:
        """Replace a loaded provider service.

        Called by DeploymentService during environment override application when an
        environment declares ``overrides.providers[].file`` to swap the provider binding.
        """
        if self._related_services is not None:
            if "providers" not in self._related_services:
                self._related_services["providers"] = {}
            self._related_services["providers"][provider_name] = service

    def get_resource_services(
        self,
    ) -> Optional[Dict[str, ResourceService]]:
        """Get all resource services keyed by name."""
        value = self._get_workspace_related_services("resources", None)
        if value is not None and isinstance(value, dict):
            casted = {k: cast(ResourceService, v) for k, v in value.items() if isinstance(v, ResourceService)}
            return casted
        return None

    def get_resource_service(self, resource_name: str) -> Optional[ResourceService]:
        """Get a specific resource service by name."""
        value = self._get_workspace_related_services("resources", resource_name)
        if value is not None and isinstance(value, ResourceService):
            return cast(ResourceService, value)
        return None

    # Helper method to get related services

    def _get_workspace_related_services(
        self, service_type: str, service_name: Optional[str] = None
    ) -> Optional[Union[BaseService, Dict[str, BaseService]]]:
        """
        Get a specific related service by type and optionally by name.

        This is a helper method for subclasses that manage related services.

        Args:
            service_type: Type of service (e.g., 'providers', 'resources', etc.)
            service_name: Optional name for dict-based services

        Returns:
            The requested service or None if not found
        """
        # Check if subclass has _related_services attribute
        if not hasattr(self, "_related_services") or self._related_services is None:
            return None

        # Check if requested service type is available
        if service_type not in self._related_services:
            return None

        # Get the service object for the requested type
        service_obj = self._related_services.get(service_type)

        # If service is a dict and name provided, get specific item
        if isinstance(service_obj, dict):
            if service_name:
                return service_obj.get(service_name)
            # No service_name requested → return the full dict
            return service_obj

        return None
