"""
EasyBuild GitLab CI Hook

This hook automatically generates GitLab CI pipeline YAML files from EasyBuild easyconfigs.
It reads configuration from .gitlab-ci.yml and creates a child pipeline with proper dependencies.

Usage:
    eb --hooks=gitlab_ci_hook.py <easyconfig.eb>

The hook will:
1. Intercept EasyBuild execution before builds start
2. Analyze all easyconfigs and their dependencies
3. Generate a GitLab CI pipeline YAML with proper job dependencies
4. Inject default configuration from .gitlab-ci.yml
5. Exit before actually building anything

Configuration is read from .gitlab-ci.yml in the current directory, specifically:
- default: section (tags, before_script, id_tokens, retry, etc.)
- execute_builds.variables: section (for child pipeline variables)

Author: Varun Sudharshnam, HZDR
"""

import copy
import os
import posixpath
import sys
import yaml
from pathlib import Path
from easybuild.base import fancylogger
from easybuild.tools.build_log import print_msg
from easybuild.tools.filetools import write_file, mkdir
from easybuild.framework.easyconfig.easyconfig import ActiveMNS


log = fancylogger.getLogger('gitlab_ci_hook', fname=False)


def _status_msg(message):
    """Emit a concise user-facing status line through EasyBuild output handling."""
    print_msg(message, log=log)


_status_msg("*** GITLAB CI HOOK LOADED ***")
log.info("GitLab CI Hook module loaded successfully")

# Global variables to track pipeline state
PIPELINE_JOBS = {}
JOB_DEPENDENCIES = {}
GITLAB_CONFIG = {}
JOB_NAME_MAP = {}
PARSED_ECS = []
READY_ECS = []

PIPELINE_FILE_NAME = 'easybuild-child-pipeline.yml'
PASSTHROUGH_ENV_KEYS = ('SCHEDULER_PARAMETERS', 'patheb', 'DRYRUN')
HOOK_CONTROL_OPTIONS = ('--hooks', '--job', '--easystack')
TRUTHY_VALUES = ('1', 'true', 'yes')
DEFAULT_RETRY = {
    'max': 2,
    'when': ['runner_system_failure', 'stuck_or_timeout_failure', 'job_execution_timeout'],
}
DEFAULT_CONFIG_KEYS = ('before_script', 'after_script', 'tags', 'id_tokens', 'timeout', 'image')


def _extract_easyconfig_details(easyconfig, index):
    """Normalize the different easyconfig payload shapes used by EasyBuild hooks."""
    if isinstance(easyconfig, dict):
        if 'ec' in easyconfig:
            ec = easyconfig['ec']
            spec = easyconfig.get('spec', 'unknown')
            easyconfig_name = f"{ec.name}-{ec.version}.eb"
        elif 'name' in easyconfig and 'version' in easyconfig:
            ec = easyconfig.get('ec')
            if ec is None:
                log.warning("Skipping easyconfig %d: no 'ec' object found", index)
                return None
            easyconfig_name = f"{easyconfig['name']}-{easyconfig['version']}.eb"
            spec = easyconfig.get('spec', easyconfig_name)
        else:
            ec = easyconfig.get('ec')
            if ec is None:
                log.warning("Skipping easyconfig %d: no 'ec' key found", index)
                return None
            easyconfig_name = f"{ec.name}-{ec.version}.eb"
            spec = easyconfig.get('spec', easyconfig_name)
    else:
        ec = easyconfig
        easyconfig_name = f"{ec.name}-{ec.version}.eb"
        spec = getattr(ec, 'path', easyconfig_name)

    return {
        'ec': ec,
        'spec': spec,
        'easyconfig_name': easyconfig_name,
        'name': getattr(ec, 'name', None),
        'version': getattr(ec, 'version', None),
        'versionsuffix': getattr(ec, 'versionsuffix', ''),
        'toolchain': getattr(ec, 'toolchain', None),
    }


def _toolchain_tuple(toolchain):
    """Return a comparable (name, version) tuple for a toolchain-like object."""
    if isinstance(toolchain, dict):
        return (toolchain.get('name'), toolchain.get('version'))
    return (getattr(toolchain, 'name', None), getattr(toolchain, 'version', None))


def _record_identity(item):
    """Return the lookup key used to match dependencies to collected easyconfigs."""
    if isinstance(item, dict):
        return (item.get('name'), item.get('version'), item.get('versionsuffix', ''))
    return (getattr(item, 'name', None), getattr(item, 'version', None), getattr(item, 'versionsuffix', ''))


def _build_easyconfig_record_index(easyconfig_records):
    """Index collected easyconfigs by name/version/versionsuffix for fast dependency fallback lookup."""
    record_index = {}
    for record in easyconfig_records:
        if not record.get('module_name'):
            continue
        key = _record_identity(record)
        if key[0] and key[1]:
            record_index.setdefault(key, []).append(record)
    return record_index


def _resolve_dependency_module_name(dep, easyconfig_records=None, record_index=None):
    """Best-effort dependency module name lookup without requiring the dep easyconfig file."""
    if not isinstance(dep, dict):
        return None

    for key in ('full_mod_name', 'module_name', 'short_mod_name'):
        if dep.get(key):
            return dep[key]

    if not easyconfig_records:
        return None

    dep_name = dep.get('name')
    dep_version = dep.get('version')
    dep_versionsuffix = dep.get('versionsuffix', '')
    if not dep_name or not dep_version:
        return None

    lookup_key = (dep_name, dep_version, dep_versionsuffix)
    if record_index is not None:
        matches = record_index.get(lookup_key, [])
    else:
        matches = [
            record for record in easyconfig_records
            if record.get('name') == dep_name
            and record.get('version') == dep_version
            and record.get('versionsuffix', '') == dep_versionsuffix
            and record.get('module_name') is not None
        ]

    if len(matches) == 1:
        return matches[0]['module_name']

    dep_toolchain = _toolchain_tuple(dep.get('toolchain'))
    toolchain_matches = [
        record for record in matches
        if _toolchain_tuple(record.get('toolchain')) == dep_toolchain
    ]
    if len(toolchain_matches) == 1:
        return toolchain_matches[0]['module_name']

    if len(matches) > 1 and dep.get('toolchain_inherited'):
        match_names = [record['module_name'] for record in matches]
        log.warning(
            "Ambiguous inherited dependency %s/%s matches multiple pipeline jobs: %s",
            dep_name,
            dep_version,
            ', '.join(match_names),
        )

    return None


def _det_full_module_name(item, easyconfig_records=None, record_index=None, mns=None):
    """Resolve a module name with a fallback for inherited/toolchain-shifted dependencies."""
    if mns is None:
        mns = ActiveMNS()

    try:
        return mns.det_full_module_name(item)
    except Exception as err:
        fallback_module_name = _resolve_dependency_module_name(item, easyconfig_records, record_index)
        if fallback_module_name:
            log.debug(
                "ActiveMNS failed for %s with %s, resolved via pipeline fallback to %s",
                item,
                err,
                fallback_module_name,
            )
            return fallback_module_name
        log.debug("ActiveMNS and pipeline fallback both failed for %s after %s", item, err)
        raise


def start_hook(*args, **kwargs):
    """Initialize GitLab CI pipeline generation."""
    global PIPELINE_JOBS, JOB_DEPENDENCIES, GITLAB_CONFIG, JOB_NAME_MAP, PARSED_ECS, READY_ECS
    
    _status_msg("*** START_HOOK CALLED ***")
    log.info("[GitLab CI Hook] Initializing GitLab CI pipeline generation")
    
    # Reset global state
    PIPELINE_JOBS = {}
    JOB_DEPENDENCIES = {}
    GITLAB_CONFIG = {}
    JOB_NAME_MAP = {}
    PARSED_ECS = []
    READY_ECS = []
    
    # Load GitLab configuration from environment
    GITLAB_CONFIG = {
        'project_url': os.environ.get('CI_PROJECT_URL', ''),
        'project_path': os.environ.get('CI_PROJECT_PATH', ''),
        'pipeline_id': os.environ.get('CI_PIPELINE_ID', ''),
        'commit_sha': os.environ.get('CI_COMMIT_SHA', ''),
        'ref': os.environ.get('CI_COMMIT_REF_NAME', 'main'),
        'registry_image': os.environ.get('CI_REGISTRY_IMAGE', ''),
        'job_token': os.environ.get('CI_JOB_TOKEN', ''),
        'server_url': os.environ.get('CI_SERVER_URL', 'https://gitlab.com'),
    }
    
    log.info("[GitLab CI Hook] GitLab environment detected: %s", 
             'Yes' if os.environ.get('GITLAB_CI') else 'No')


def pre_configure_hook(*args, **kwargs):
    """Hook called early in the process."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    log.debug("[GitLab CI Hook] pre_configure_hook called")


def parse_hook(ec_dict):
    """Hook called when parsing easyconfig files."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    # Store easyconfig for pipeline generation
    global PARSED_ECS
    if 'PARSED_ECS' not in globals():
        PARSED_ECS = []
    
    PARSED_ECS.append(ec_dict)
    log.debug("[GitLab CI Hook] Collected easyconfig: %s", ec_dict.get('spec', 'unknown'))
    
    return ec_dict


def post_ready_hook(ec, *args, **kwargs):
    """Hook called when easyconfig is ready - collect dependency info."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    # Store easyconfig in our global list for pipeline generation
    global READY_ECS
    if 'READY_ECS' not in globals():
        READY_ECS = []
    
    # Create a dict with the info we need
    ec_info = {
        'ec': ec,
        'spec': getattr(ec, 'path', 'unknown'),
        'name': ec.name,
        'version': ec.version,
        'toolchain': ec.toolchain,
        'dependencies': ec.dependencies,
        'builddependencies': getattr(ec, 'builddependencies', []),
    }
    
    READY_ECS.append(ec_info)
    log.debug("[GitLab CI Hook] Collected ready easyconfig: %s-%s", ec.name, ec.version)


def pre_build_and_install_loop_hook(ecs, *args, **kwargs):
    """Hook called before starting the build and install loop with all easyconfigs."""
    _status_msg("*** PRE_BUILD_AND_INSTALL_LOOP_HOOK CALLED ***")
    _status_msg(f"*** Received {len(ecs)} easyconfigs ***")
    log.info("[GitLab CI Hook] Processing %d easyconfigs for GitLab CI pipeline generation", len(ecs))
    
    try:
        command_context = _create_eb_command_context()

        # Use the ready easyconfigs if available, otherwise use the provided ones
        global READY_ECS
        if command_context.get('easystack_entries'):
            expanded_easyconfigs = _expand_easystack_easyconfigs(command_context)
            _status_msg(f"*** Using {len(expanded_easyconfigs)} EasyStack-expanded easyconfigs ***")
            log.info("[GitLab CI Hook] Using %d easyconfigs expanded from EasyStack", len(expanded_easyconfigs))
            _process_easyconfigs_for_jobs(expanded_easyconfigs)
        elif 'READY_ECS' in globals() and READY_ECS:
            _status_msg(f"*** Using {len(READY_ECS)} ready easyconfigs ***")
            log.info("[GitLab CI Hook] Using %d ready easyconfigs from post_ready_hook", len(READY_ECS))
            _process_easyconfigs_for_jobs(READY_ECS)
        else:
            _status_msg(f"*** Using {len(ecs)} provided easyconfigs ***")
            log.info("[GitLab CI Hook] Using %d easyconfigs from pre_build_and_install_loop_hook", len(ecs))
            _process_easyconfigs_for_jobs(ecs)
        
        _status_msg("*** Processing complete - generating pipeline ***")
        # Generate and inject defaults into pipeline YAML
        _generate_and_inject_pipeline(command_context)
        
        _status_msg("*** Pipeline generated - exiting ***")
        # Stop EasyBuild execution after pipeline generation
        log.info("[GitLab CI Hook] GitLab CI pipeline generated. Stopping EasyBuild execution.")
        raise SystemExit(0)
        
    except SystemExit:
        # Re-raise SystemExit
        raise
    except Exception as e:
        _status_msg(f"*** ERROR in hook: {e} ***")
        log.exception("[GitLab CI Hook] Error in pre_build_and_install_loop_hook: %s", e)
        raise


def _process_easyconfigs_for_jobs(easyconfigs):
    """Process easyconfigs and build job dependency map."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    global PIPELINE_JOBS, JOB_DEPENDENCIES, JOB_NAME_MAP
    
    # Reset global state
    PIPELINE_JOBS = {}
    JOB_DEPENDENCIES = {}
    JOB_NAME_MAP = {}
    
    # Collect all dependency module names first, then resolve to in-pipeline deps in a second pass.
    raw_dependencies = {}
    easyconfig_records = []
    
    log.info("[GitLab CI Hook] Processing %d easyconfigs", len(easyconfigs))
    
    # Normalize easyconfig payloads up front so dependency fallback matching can use the full pipeline set.
    for i, easyconfig in enumerate(easyconfigs):
        details = _extract_easyconfig_details(easyconfig, i)
        if details is not None:
            easyconfig_records.append(details)

    # ActiveMNS setup may inspect EasyBuild state; reuse one resolver across the pipeline.
    mns = ActiveMNS()

    # Resolve module names for all jobs before dependency processing.
    for record in easyconfig_records:
        try:
            record['module_name'] = _det_full_module_name(record['ec'], easyconfig_records, mns=mns)
        except Exception as err:
            log.warning("Could not determine module name for %s: %s", record['spec'], err)

    easyconfig_records = [record for record in easyconfig_records if record.get('module_name')]
    record_index = _build_easyconfig_record_index(easyconfig_records)

    # Process each easyconfig
    for i, record in enumerate(easyconfig_records):
        try:
            ec = record['ec']
            spec = record['spec']
            easyconfig_name = record['easyconfig_name']
            module_name = record.get('module_name')
            if not module_name:
                continue
            
            # Get all dependencies, filter out external modules.
            all_dependencies = getattr(ec, 'all_dependencies', None)
            if all_dependencies is None:
                all_dependencies = list(getattr(ec, 'dependencies', []) or [])
                all_dependencies.extend(getattr(ec, 'builddependencies', []) or [])
            all_deps = [
                dep for dep in all_dependencies
                if not (isinstance(dep, dict) and dep.get('external_module', False))
            ]
            
            # Map dependency module names
            dep_mod_names = []
            
            # Process all dependencies
            for dep in all_deps:
                try:
                    dep_mod_name = _det_full_module_name(dep, easyconfig_records, record_index, mns)
                    dep_mod_names.append(dep_mod_name)
                except Exception as err:
                    log.warning("Could not determine module name for dependency %s: %s", dep, err)
            
            # Create job entry
            job_info = {
                'name': easyconfig_name,
                'module': module_name,
                'easyconfig_path': spec,
                'dependencies': dep_mod_names,
                'job_dependencies': [],
                'toolchain': ec.toolchain,
                'version': ec.version,
            }
            
            PIPELINE_JOBS[module_name] = job_info
            raw_dependencies[module_name] = dep_mod_names
            
            log.info("[GitLab CI Hook] Added job '%s' (%s) with %d total deps", 
                     module_name, easyconfig_name, len(dep_mod_names))
        
        except Exception as err:
            log.error("[GitLab CI Hook] Error processing easyconfig %d (%s): %s", i, record.get('easyconfig_name'), err)
            continue

    # Resolve dependency edges against the final set of jobs so input order does not matter.
    pipeline_modules = set(PIPELINE_JOBS.keys())
    for module_name, dep_mod_names in raw_dependencies.items():
        seen = set()
        pipeline_deps = []
        for dep_mod_name in dep_mod_names:
            if dep_mod_name == module_name:
                continue
            if dep_mod_name in pipeline_modules and dep_mod_name not in seen:
                pipeline_deps.append(dep_mod_name)
                seen.add(dep_mod_name)

        JOB_DEPENDENCIES[module_name] = pipeline_deps
        PIPELINE_JOBS[module_name]['job_dependencies'] = pipeline_deps
        log.info("[GitLab CI Hook] Resolved pipeline deps for '%s': %s", module_name, pipeline_deps)
    
    _status_msg(f"*** Finished processing - created {len(PIPELINE_JOBS)} jobs ***")
    log.info("[GitLab CI Hook] Processed %d easyconfigs for GitLab CI jobs", len(PIPELINE_JOBS))


def _generate_and_inject_pipeline(command_context=None):
    """Generate the GitLab CI pipeline YAML and inject configuration from .gitlab-ci.yml."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    if not PIPELINE_JOBS:
        log.warning("[GitLab CI Hook] No jobs to generate pipeline for")
        return
    
    # Generate base pipeline
    pipeline = _generate_base_pipeline(command_context)
    
    # Load configuration from .gitlab-ci.yml and inject
    config_file = Path('.gitlab-ci.yml')
    if config_file.exists():
        log.info("[GitLab CI Hook] Loading configuration from .gitlab-ci.yml")
        default_config, child_variables = _load_gitlab_ci_config(config_file)
        pipeline = _inject_configuration(pipeline, default_config, child_variables)
    else:
        log.warning("[GitLab CI Hook] .gitlab-ci.yml not found, using minimal configuration")
    
    # Write pipeline file
    output_dir = os.getcwd()
    mkdir(output_dir, parents=True)
    
    pipeline_file = os.path.join(output_dir, PIPELINE_FILE_NAME)
    pipeline_yaml = yaml.dump(pipeline, default_flow_style=False, width=120, sort_keys=False)
    write_file(pipeline_file, pipeline_yaml)
    
    log.info("[GitLab CI Hook] Generated GitLab CI pipeline: %s", pipeline_file)
    log.info("[GitLab CI Hook] Pipeline contains %d jobs with %d total dependencies", 
             len(PIPELINE_JOBS), sum(len(deps) for deps in JOB_DEPENDENCIES.values()))
    
    # Generate summary
    _generate_pipeline_summary(pipeline_file)


def _generate_base_pipeline(command_context=None):
    """Generate the base GitLab CI pipeline structure."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    global JOB_NAME_MAP
    
    # Set all jobs to a single stage for parallel execution
    pipeline_variables = {
        'EASYBUILD_MODULES_TOOL': 'Lmod',
    }
    for key in PASSTHROUGH_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            pipeline_variables[key] = value

    # EasyBuild reads EASYBUILD_CUDA_COMPUTE_CAPABILITIES (not the unprefixed name).
    # Accept either form from the environment for convenience.
    cuda_cc = os.environ.get(
        'EASYBUILD_CUDA_COMPUTE_CAPABILITIES',
        os.environ.get('CUDA_COMPUTE_CAPABILITIES'),
    )
    if cuda_cc:
        pipeline_variables['EASYBUILD_CUDA_COMPUTE_CAPABILITIES'] = cuda_cc

    pipeline = {
        'stages': ['build'],
        'variables': pipeline_variables,
    }

    # Build unique GitLab job names to avoid collisions after sanitization.
    used_job_names = set()
    JOB_NAME_MAP = {}
    for module_name, job_info in PIPELINE_JOBS.items():
        base_name = _sanitize_job_name(_job_name_from_easyconfig(job_info))
        job_name = base_name
        suffix = 2
        while job_name in used_job_names:
            job_name = f"{base_name}-{suffix}"
            suffix += 1
        if job_name != base_name:
            log.warning("[GitLab CI Hook] Job name collision for '%s'; using '%s'", module_name, job_name)
        used_job_names.add(job_name)
        JOB_NAME_MAP[module_name] = job_name
    
    if command_context is None:
        command_context = _create_eb_command_context()

    # Add jobs
    for module_name, job_info in PIPELINE_JOBS.items():
        sanitized_name = JOB_NAME_MAP[module_name]
        job_yaml = _create_gitlab_job(job_info, 'build', command_context)
        job_yaml['stage'] = 'build'
        pipeline[sanitized_name] = job_yaml

        log.debug("[GitLab CI Hook] Created job '%s' for module '%s'", sanitized_name, module_name)

        # Add dependencies
        deps = JOB_DEPENDENCIES.get(module_name, [])
        if deps:
            pipeline_deps = []
            for dep in deps:
                if dep in JOB_NAME_MAP:
                    pipeline_deps.append(JOB_NAME_MAP[dep])
                    log.debug("[GitLab CI Hook] ✓ Added dependency '%s' -> '%s' for job '%s'", 
                              dep, JOB_NAME_MAP[dep], module_name)
            if pipeline_deps:
                pipeline[sanitized_name]['needs'] = pipeline_deps
                log.info("[GitLab CI Hook] Job '%s' needs: %s", sanitized_name, pipeline_deps)
    
    return pipeline


def _load_gitlab_ci_config(config_file):
    """Load default configuration from .gitlab-ci.yml file."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config_data = yaml.safe_load(f) or {}
        if not isinstance(config_data, dict):
            log.warning("[GitLab CI Hook] Ignoring non-mapping .gitlab-ci.yml content")
            return {}, {}
        
        # Extract the 'default' section from .gitlab-ci.yml
        default_config = config_data.get('default') or {}
        if not isinstance(default_config, dict):
            log.warning("[GitLab CI Hook] Ignoring non-mapping default section in .gitlab-ci.yml")
            default_config = {}
        
        # Extract variables from execute_builds job for child pipeline
        child_variables = {}
        execute_builds = config_data.get('execute_builds') or {}
        if isinstance(execute_builds, dict) and isinstance(execute_builds.get('variables'), dict):
            child_variables = execute_builds['variables']
        
        log.info("[GitLab CI Hook] Loaded configuration from .gitlab-ci.yml")
        return default_config, child_variables
    except Exception as e:
        log.warning("[GitLab CI Hook] Could not load .gitlab-ci.yml: %s", e)
        return {}, {}


def _inject_configuration(pipeline, default_config, child_variables):
    """Inject configuration from .gitlab-ci.yml into the pipeline."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    default_config = default_config if isinstance(default_config, dict) else {}
    child_variables = child_variables if isinstance(child_variables, dict) else {}
    
    # Build default section
    default = {}
    
    for key in DEFAULT_CONFIG_KEYS:
        if key in default_config:
            default[key] = copy.deepcopy(default_config[key])
    
    if 'retry' in default_config:
        retry_value = default_config['retry']
        if isinstance(retry_value, dict):
            default['retry'] = copy.deepcopy(retry_value)
        else:
            # GitLab also allows scalar retry values (for example: retry: 2).
            default['retry'] = retry_value
    else:
        default['retry'] = copy.deepcopy(DEFAULT_RETRY)
    
    # Merge child pipeline variables
    if child_variables:
        variables = pipeline.get('variables', {})
        for key, value in child_variables.items():
            # Skip variables that reference themselves (e.g., EB_PATH: $EB_PATH)
            if isinstance(value, str) and value.strip() == f'${key}':
                log.debug("[GitLab CI Hook] Skipping self-referencing variable: %s", key)
                continue
            # Only add if not already present
            if key not in variables:
                variables[key] = value
        pipeline['variables'] = variables
    
    # Reorder pipeline structure: stages -> variables -> default -> jobs
    ordered_pipeline = {}
    
    if 'stages' in pipeline:
        ordered_pipeline['stages'] = pipeline['stages']
    
    if 'variables' in pipeline:
        ordered_pipeline['variables'] = pipeline['variables']
    
    if default:
        ordered_pipeline['default'] = default
    
    # Add all job definitions
    for key in pipeline:
        if key not in ['stages', 'variables', 'default']:
            ordered_pipeline[key] = pipeline[key]
    
    log.info("[GitLab CI Hook] Injected configuration into pipeline")
    return ordered_pipeline


def _build_eb_arg_template_map():
    """Map concrete generation-time arguments back to runtime variables for matrix fan-out."""
    template_map = {}

    eb_path = os.environ.get('EB_PATH')
    arch = os.environ.get('ARCH')
    if eb_path and arch:
        install_root = posixpath.join(eb_path.rstrip('/'), arch)
        template_map[f'--installpath={install_root}'] = '--installpath=${EB_PATH}/${ARCH}'
        template_map[f'--installpath-modules={install_root}/modules'] = '--installpath-modules=${EB_PATH}/${ARCH}/modules'

    source_path = os.environ.get('SOURCE_PATH')
    if source_path:
        template_map[f'--sourcepath={source_path}'] = '--sourcepath=${SOURCE_PATH}'

    ntasks_per_node = os.environ.get('NTASKS_PER_NODE')
    if ntasks_per_node:
        template_map[f'--max-parallel={ntasks_per_node}'] = '--max-parallel=${NTASKS_PER_NODE}'

    # Prefer a prebuilt CUDA arg variable so CPU rows can leave it empty.
    cuda_option = os.environ.get('CUDA_COMPUTE_OPTION')
    if cuda_option:
        template_map[cuda_option] = '${CUDA_COMPUTE_OPTION}'
    else:
        cuda_cc = os.environ.get('EASYBUILD_CUDA_COMPUTE_CAPABILITIES', os.environ.get('CUDA_COMPUTE_CAPABILITIES'))
        if cuda_cc:
            template_map[f'--cuda-compute-capabilities={cuda_cc}'] = (
                '--cuda-compute-capabilities=${EASYBUILD_CUDA_COMPUTE_CAPABILITIES}'
            )

    return template_map


def _template_eb_arg(arg, template_map=None):
    """Template generation-job-specific paths back to child-pipeline variables."""
    if template_map is None:
        template_map = _build_eb_arg_template_map()

    if arg in template_map:
        return template_map[arg]

    ci_project_dir = os.environ.get('CI_PROJECT_DIR')
    if ci_project_dir:
        return arg.replace(ci_project_dir.rstrip('/'), '${CI_PROJECT_DIR}')

    return arg


def _matches_long_option(arg, option):
    """Return whether an argv token is exactly an option or its --option=value form."""
    return arg == option or arg.startswith(f'{option}=')


def _long_option_value(argv, index, option):
    """Extract a long option value from --option=value or --option value argv forms."""
    arg = argv[index]
    prefix = f'{option}='
    if arg.startswith(prefix):
        return arg.split('=', 1)[1]
    if arg == option and index + 1 < len(argv):
        return argv[index + 1]
    return None


def _find_easystack_path(argv):
    """Return the path supplied through --easystack, if any."""
    for i, arg in enumerate(argv):
        option_value = _long_option_value(argv, i, '--easystack')
        if option_value is not None:
            return option_value
    return None


def _skip_hook_control_option(argv, index):
    """Return the last argv index to skip for hook-only EasyBuild control options."""
    arg = argv[index]
    for option in HOOK_CONTROL_OPTIONS:
        if not _matches_long_option(arg, option):
            continue
        if arg == option and index + 1 < len(argv) and not argv[index + 1].startswith('-'):
            return index + 1
        return index
    return None


def _normalise_easystack_entries(easystack_data):
    """Return EasyStack entries as (easyconfig filename, options dict) tuples."""
    if not isinstance(easystack_data, dict):
        return []

    easyconfigs = easystack_data.get('easyconfigs') or []
    entries = []
    for entry in easyconfigs:
        if isinstance(entry, str):
            entries.append((entry, {}))
        elif isinstance(entry, dict):
            for easyconfig_name, entry_config in entry.items():
                options = {}
                if isinstance(entry_config, dict) and isinstance(entry_config.get('options'), dict):
                    options = entry_config['options']
                entries.append((easyconfig_name, options))
        else:
            log.warning("[GitLab CI Hook] Ignoring unsupported EasyStack entry: %s", entry)

    return entries


def _load_easystack_entries(easystack_path):
    """Load EasyStack entries for command reconstruction."""
    if not easystack_path:
        return []

    try:
        with open(easystack_path, 'r', encoding='utf-8') as easystack_file:
            easystack_data = yaml.safe_load(easystack_file) or {}
        return _normalise_easystack_entries(easystack_data)
    except Exception as err:
        raise RuntimeError(f"Could not load EasyStack file {easystack_path}: {err}")


def _easystack_option_args(options, template_map=None):
    """Convert EasyStack per-entry options to eb command arguments."""
    if not isinstance(options, dict):
        return []

    args = []
    for option, value in options.items():
        option_name = str(option)
        prefix = '-' if len(option_name) == 1 else '--'
        cli_option = f"{prefix}{option_name}"

        values = value if isinstance(value, list) else [value]
        for item in values:
            if item is None:
                continue
            if isinstance(item, bool):
                args.append(cli_option if item else f"--disable-{option_name}")
            else:
                args.append(cli_option)
                args.append(_template_eb_arg(str(item), template_map))

    return args


def _build_easystack_context(easystack_path, template_map=None):
    """Map EasyStack filenames to their per-entry eb arguments."""
    entries = _load_easystack_entries(easystack_path)
    args_by_easyconfig = {}
    for easyconfig_name, options in entries:
        args_by_easyconfig[os.path.basename(easyconfig_name)] = _easystack_option_args(options, template_map)
    return args_by_easyconfig, entries


def _expand_easystack_easyconfigs(command_context):
    """Resolve all EasyStack entries through EasyBuild's parser and robot dependency resolver."""
    entries = command_context.get('easystack_entries') or []
    if not entries:
        return []

    try:
        from easybuild.framework.easyconfig.tools import det_easyconfig_paths, parse_easyconfigs
        from easybuild.tools.modules import modules_tool
        from easybuild.tools.robot import resolve_dependencies
    except Exception as err:
        raise RuntimeError(f"Can not import EasyBuild APIs needed for multi-entry EasyStack expansion: {err}")

    paths = []
    for easyconfig_name, _options in entries:
        determined_paths = det_easyconfig_paths([easyconfig_name])
        if not determined_paths:
            raise RuntimeError(f"Could not resolve EasyStack easyconfig path: {easyconfig_name}")
        paths.extend((path, False) for path in determined_paths)

    try:
        easyconfigs, _generated_ecs = parse_easyconfigs(paths)
        if command_context.get('robot_enabled'):
            modtool = modules_tool(testing=False)
            return resolve_dependencies(easyconfigs, modtool)
        return easyconfigs
    except Exception as err:
        raise RuntimeError(f"Could not expand EasyStack easyconfigs for GitLab CI pipeline generation: {err}")


def _create_eb_command_context(argv=None):
    """Parse EasyBuild argv once for all generated GitLab jobs."""
    if argv is None:
        argv = sys.argv if hasattr(sys, 'argv') else []

    eb_args = []
    arg_template_map = _build_eb_arg_template_map()
    easystack_path = _find_easystack_path(argv)
    easystack_args_by_easyconfig, easystack_entries = _build_easystack_context(
        easystack_path,
        arg_template_map,
    )
    tmp_logdir = None
    buildpath = None
    robot_enabled = False

    i = 1 if argv else 0
    while i < len(argv):
        arg = argv[i]

        if _matches_long_option(arg, '--robot') or arg == '-r':
            robot_enabled = True

        option_value = _long_option_value(argv, i, '--tmp-logdir')
        if option_value is not None:
            tmp_logdir = option_value

        option_value = _long_option_value(argv, i, '--buildpath')
        if option_value is not None:
            buildpath = option_value

        skip_until = _skip_hook_control_option(argv, i)
        if skip_until is not None:
            i = skip_until + 1
            continue

        # Skip .eb files (we'll add the specific one for this job)
        if not arg.endswith('.eb'):
            eb_args.append(_template_eb_arg(arg, arg_template_map))

        i += 1

    return {
        'eb_args': eb_args,
        'easystack_args_by_easyconfig': easystack_args_by_easyconfig,
        'easystack_entries': easystack_entries,
        'easystack_entry_count': len(easystack_entries),
        'robot_enabled': robot_enabled,
        'tmp_logdir': tmp_logdir,
        'buildpath': buildpath,
    }


def _build_eb_command(eb_args, easyconfig_path, per_easyconfig_args=None):
    """Build the EasyBuild command string for a generated child pipeline job."""
    command_parts = ['eb']
    command_parts.extend(eb_args)
    
    # Add dry-run option only if DRYRUN variable is set to true
    if os.environ.get('DRYRUN', '').lower() in TRUTHY_VALUES:
        command_parts.append('--dry-run')
    
    # Add the specific easyconfig for this job
    command_parts.append(os.path.basename(easyconfig_path))
    if per_easyconfig_args:
        command_parts.extend(per_easyconfig_args)
    return ' '.join(command_parts)


def _build_artifact_paths(tmp_logdir, buildpath):
    """Build artifact paths dynamically from the EasyBuild command context."""
    artifact_paths = []
    if tmp_logdir:
        artifact_paths.append(f'{tmp_logdir}/*.log')
    if buildpath:
        artifact_paths.append(f'{buildpath}/**/*.log')
    artifact_paths.extend(['*.log', '*.out', '*.err'])
    return artifact_paths


def _stable_tmpdir(buildpath):
    """Resolve EasyBuild TMPDIR to a stable path under buildpath when available."""
    if not buildpath:
        return None

    # Resolve scratch to a stable absolute path for the job. EasyBuild changes
    # into package-specific build directories, so a relative TMPDIR can point
    # somewhere unintended by the time the CUDA installer runs.
    if buildpath.startswith('$') or os.path.isabs(buildpath):
        buildpath_root = buildpath
    else:
        buildpath_root = posixpath.join('${CI_PROJECT_DIR}', buildpath)
    return posixpath.normpath(posixpath.join(buildpath_root, 'tmp'))


def _create_gitlab_job(job_info, stage_name, command_context=None):
    """Create a GitLab CI job definition."""
    if command_context is None:
        command_context = _create_eb_command_context()

    easyconfig_basename = os.path.basename(job_info['easyconfig_path'])
    per_easyconfig_args = command_context.get('easystack_args_by_easyconfig', {}).get(easyconfig_basename, [])
    eb_command = _build_eb_command(command_context['eb_args'], job_info['easyconfig_path'], per_easyconfig_args)
    artifact_paths = _build_artifact_paths(command_context['tmp_logdir'], command_context['buildpath'])
    tempdir = _stable_tmpdir(command_context['buildpath'])
    
    # Build per-job variables
    job_variables = {
        'EB_MODULE_NAME': job_info['module'],
    }

    # Point TMPDIR at a stable absolute path under the buildpath so large CUDA
    # .run extractions don't overflow /tmp or resolve relative to the package
    # build directory that EasyBuild has chdir'ed into.
    if tempdir:
        job_variables['TMPDIR'] = tempdir
        job_variables['EASYBUILD_TMPDIR'] = tempdir

    script = []
    if tempdir:
        script.append('mkdir -p "$TMPDIR"')
    script.append(eb_command)

    # Create job definition
    job = {
        'stage': stage_name,
        'script': script,
        'variables': job_variables,
        'artifacts': {
            'when': 'always',
            'paths': artifact_paths,
            'expire_in': '1 week',
        }
    }
    
    return job


def _sanitize_job_name(name):
    """Sanitize module name for use as GitLab CI job name."""
    sanitized = name.replace('/', '-').replace(':', '-').replace('+', 'plus')
    sanitized = sanitized.replace('(', '').replace(')', '').replace(' ', '-')
    if sanitized and not (sanitized[0].isalpha() or sanitized[0] == '_'):
        sanitized = 'job-' + sanitized
    return sanitized or 'unknown-job'


def _job_name_from_easyconfig(job_info):
    """Return the desired GitLab job name base: easyconfig basename without .eb."""
    easyconfig_path = job_info.get('easyconfig_path') or job_info.get('name') or job_info.get('module') or ''
    basename = os.path.basename(easyconfig_path)
    if basename.endswith('.eb'):
        basename = basename[:-3]
    return basename


def _generate_pipeline_summary(pipeline_file):
    """Generate and display pipeline summary."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    total_jobs = len(PIPELINE_JOBS)
    
    print_msg("\n" + "="*80, log=log)
    print_msg("GitLab CI Pipeline Generated Successfully!", log=log)
    print_msg("="*80, log=log)
    print_msg("Pipeline file: %s" % pipeline_file, log=log)
    print_msg("Total jobs: %d" % total_jobs, log=log)
    print_msg("\nTo trigger this pipeline, add to your .gitlab-ci.yml:", log=log)
    print_msg("", log=log)
    print_msg("execute_builds:", log=log)
    print_msg("  stage: build", log=log)
    print_msg("  trigger:", log=log)
    print_msg("    include:", log=log)
    print_msg("      - artifact: %s" % os.path.basename(pipeline_file), log=log)
    print_msg("        job: generate_pipeline", log=log)
    print_msg("    strategy: depend", log=log)
    print_msg("", log=log)
    
    # Show example individual job commands
    if PIPELINE_JOBS:
        sample_job = next(iter(PIPELINE_JOBS.values()))
        print_msg("Example job command:", log=log)
        print_msg("  eb --robot %s" % os.path.basename(sample_job['easyconfig_path']), log=log)
    
    print_msg("="*80, log=log)


def end_hook(*args, **kwargs):
    """Cleanup hook called when EasyBuild finishes."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    log.info("[GitLab CI Hook] GitLab CI pipeline generation completed")
