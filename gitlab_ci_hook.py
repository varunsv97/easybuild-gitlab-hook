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

import os
import sys
import yaml
from pathlib import Path
from easybuild.base import fancylogger
from easybuild.tools.build_log import print_msg
from easybuild.tools.filetools import write_file, mkdir
from easybuild.framework.easyconfig.easyconfig import ActiveMNS


# Print a message when the hook module is loaded
print("*** GITLAB CI HOOK LOADED ***")
log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
log.info("GitLab CI Hook module loaded successfully")

# Global variables to track pipeline state
PIPELINE_JOBS = {}
JOB_DEPENDENCIES = {}
GITLAB_CONFIG = {}
JOB_NAME_MAP = {}


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


def _resolve_dependency_module_name(dep, easyconfig_records=None):
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
        log.debug(
            "Dependency %s/%s inherited toolchain; falling back to first pipeline match %s",
            dep_name,
            dep_version,
            matches[0]['module_name'],
        )
        return matches[0]['module_name']

    return None


def _det_full_module_name(item, easyconfig_records=None):
    """Resolve a module name with a fallback for inherited/toolchain-shifted dependencies."""
    try:
        return ActiveMNS().det_full_module_name(item)
    except Exception as err:
        fallback_module_name = _resolve_dependency_module_name(item, easyconfig_records)
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
    
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    print("*** START_HOOK CALLED ***")
    log.info("*** START_HOOK CALLED ***")
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
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    print("*** PRE_BUILD_AND_INSTALL_LOOP_HOOK CALLED ***")
    print(f"*** Received {len(ecs)} easyconfigs ***")
    log.info("[GitLab CI Hook] Processing %d easyconfigs for GitLab CI pipeline generation", len(ecs))
    
    try:
        # Use the ready easyconfigs if available, otherwise use the provided ones
        global READY_ECS
        if 'READY_ECS' in globals() and READY_ECS:
            print(f"*** Using {len(READY_ECS)} ready easyconfigs ***")
            log.info("[GitLab CI Hook] Using %d ready easyconfigs from post_ready_hook", len(READY_ECS))
            _process_easyconfigs_for_jobs(READY_ECS)
        else:
            print(f"*** Using {len(ecs)} provided easyconfigs ***")
            log.info("[GitLab CI Hook] Using %d easyconfigs from pre_build_and_install_loop_hook", len(ecs))
            _process_easyconfigs_for_jobs(ecs)
        
        print("*** Processing complete - generating pipeline ***")
        # Generate and inject defaults into pipeline YAML
        _generate_and_inject_pipeline()
        
        print("*** Pipeline generated - exiting ***")
        # Stop EasyBuild execution after pipeline generation
        log.info("[GitLab CI Hook] GitLab CI pipeline generated. Stopping EasyBuild execution.")
        raise SystemExit(0)
        
    except SystemExit:
        # Re-raise SystemExit
        raise
    except Exception as e:
        print(f"*** ERROR in hook: {e} ***")
        log.error("[GitLab CI Hook] Error in pre_build_and_install_loop_hook: %s", e)
        import traceback
        traceback.print_exc()
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

    # Resolve module names for all jobs before dependency processing.
    for record in easyconfig_records:
        try:
            record['module_name'] = _det_full_module_name(record['ec'], easyconfig_records)
        except Exception as err:
            log.warning("Could not determine module name for %s: %s", record['spec'], err)

    easyconfig_records = [record for record in easyconfig_records if record.get('module_name')]

    # Process each easyconfig
    for i, record in enumerate(easyconfig_records):
        try:
            ec = record['ec']
            spec = record['spec']
            easyconfig_name = record['easyconfig_name']
            module_name = record.get('module_name')
            if not module_name:
                continue
            
            # Get all dependencies, filter out external modules
            all_deps = [d for d in ec.all_dependencies if not d.get('external_module', False)]
            
            # Map dependency module names
            dep_mod_names = []
            
            # Process all dependencies
            for dep in all_deps:
                try:
                    dep_mod_name = _det_full_module_name(dep, easyconfig_records)
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
    
    print(f"*** Finished processing - created {len(PIPELINE_JOBS)} jobs ***")
    log.info("[GitLab CI Hook] Processed %d easyconfigs for GitLab CI jobs", len(PIPELINE_JOBS))


def _generate_and_inject_pipeline():
    """Generate the GitLab CI pipeline YAML and inject configuration from .gitlab-ci.yml."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    if not PIPELINE_JOBS:
        log.warning("[GitLab CI Hook] No jobs to generate pipeline for")
        return
    
    # Generate base pipeline
    pipeline = _generate_base_pipeline()
    
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
    
    pipeline_file = os.path.join(output_dir, 'easybuild-child-pipeline.yml')
    pipeline_yaml = yaml.dump(pipeline, default_flow_style=False, width=120, sort_keys=False)
    write_file(pipeline_file, pipeline_yaml)
    
    log.info("[GitLab CI Hook] Generated GitLab CI pipeline: %s", pipeline_file)
    log.info("[GitLab CI Hook] Pipeline contains %d jobs with %d total dependencies", 
             len(PIPELINE_JOBS), sum(len(deps) for deps in JOB_DEPENDENCIES.values()))
    
    # Generate summary
    _generate_pipeline_summary(pipeline_file)


def _generate_base_pipeline():
    """Generate the base GitLab CI pipeline structure."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    global JOB_NAME_MAP
    
    # Set all jobs to a single stage for parallel execution
    pipeline_variables = {
        'EASYBUILD_MODULES_TOOL': 'Lmod',
    }
    for key in ['SCHEDULER_PARAMETERS', 'patheb', 'DRYRUN']:
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
    for module_name in PIPELINE_JOBS:
        base_name = _sanitize_job_name(module_name)
        job_name = base_name
        suffix = 2
        while job_name in used_job_names:
            job_name = f"{base_name}-{suffix}"
            suffix += 1
        if job_name != base_name:
            log.warning("[GitLab CI Hook] Job name collision for '%s'; using '%s'", module_name, job_name)
        used_job_names.add(job_name)
        JOB_NAME_MAP[module_name] = job_name
    
    # Add jobs
    for module_name, job_info in PIPELINE_JOBS.items():
        sanitized_name = JOB_NAME_MAP[module_name]
        job_yaml = _create_gitlab_job(job_info, 'build')
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
        with open(config_file, 'r') as f:
            config_data = yaml.safe_load(f)
        
        # Extract the 'default' section from .gitlab-ci.yml
        default_config = config_data.get('default', {})
        
        # Extract variables from execute_builds job for child pipeline
        child_variables = {}
        execute_builds = config_data.get('execute_builds', {})
        if 'variables' in execute_builds:
            child_variables = execute_builds['variables']
        
        log.info("[GitLab CI Hook] Loaded configuration from .gitlab-ci.yml")
        return default_config, child_variables
    except Exception as e:
        log.warning("[GitLab CI Hook] Could not load .gitlab-ci.yml: %s", e)
        return {}, {}


def _inject_configuration(pipeline, default_config, child_variables):
    """Inject configuration from .gitlab-ci.yml into the pipeline."""
    log = fancylogger.getLogger('gitlab_ci_hook', fname=False)
    
    # Build default section
    default = {}
    
    if 'before_script' in default_config:
        default['before_script'] = default_config['before_script'].copy()
    
    if 'after_script' in default_config:
        default['after_script'] = default_config['after_script'].copy()
    
    if 'tags' in default_config:
        default['tags'] = default_config['tags'].copy()
    
    if 'id_tokens' in default_config:
        default['id_tokens'] = default_config['id_tokens'].copy()
    
    if 'retry' in default_config:
        retry_value = default_config['retry']
        if isinstance(retry_value, dict):
            default['retry'] = retry_value.copy()
        else:
            # GitLab also allows scalar retry values (for example: retry: 2).
            default['retry'] = retry_value
    else:
        default['retry'] = {
            'max': 2,
            'when': ['runner_system_failure', 'stuck_or_timeout_failure', 'job_execution_timeout']
        }
    
    if 'timeout' in default_config:
        default['timeout'] = default_config['timeout']
    
    if 'image' in default_config:
        default['image'] = default_config['image']
    
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


def _create_gitlab_job(job_info, stage_name):
    """Create a GitLab CI job definition."""
    
    # Reconstruct eb command from sys.argv
    argv = sys.argv if hasattr(sys, 'argv') else []
    
    # Filter out options we don't want to pass to child jobs
    skip_options = ['--hooks', '--job']
    eb_args = []
    
    # Extract tmp-logdir and buildpath for artifact paths
    tmp_logdir = None
    buildpath = None
    
    i = 0
    while i < len(argv):
        arg = argv[i]
        # Skip the program name (eb)
        if i == 0:
            i += 1
            continue
        
        # Extract tmp-logdir
        if arg.startswith('--tmp-logdir='):
            tmp_logdir = arg.split('=', 1)[1]
        elif arg == '--tmp-logdir' and i + 1 < len(argv):
            tmp_logdir = argv[i + 1]
        
        # Extract buildpath
        if arg.startswith('--buildpath='):
            buildpath = arg.split('=', 1)[1]
        elif arg == '--buildpath' and i + 1 < len(argv):
            buildpath = argv[i + 1]
        
        # Skip hook-related options and their values
        skip_this = False
        for skip_opt in skip_options:
            if arg.startswith(skip_opt):
                skip_this = True
                # If it's --option=value format, we're done
                if '=' in arg:
                    break
                # If it's --option value format, skip the next arg too
                if i + 1 < len(argv) and not argv[i + 1].startswith('-'):
                    i += 1
                break
        
        # Skip .eb files (we'll add the specific one for this job)
        if not skip_this and not arg.endswith('.eb'):
            eb_args.append(arg)
        
        i += 1
    
    # Build the command
    eb_command = 'eb ' + ' '.join(eb_args)
    
    # Add dry-run option only if DRYRUN variable is set to true
    if os.environ.get('DRYRUN', '').lower() in ['1', 'true', 'yes']:
        eb_command += ' --dry-run'
    
    # Add the specific easyconfig for this job
    eb_command += ' ' + os.path.basename(job_info['easyconfig_path'])
    
    # Build artifact paths dynamically
    artifact_paths = ['*.log', '*.out', '*.err']
    if tmp_logdir:
        artifact_paths.insert(0, f'{tmp_logdir}/*.log')
    if buildpath:
        artifact_paths.insert(1 if tmp_logdir else 0, f'{buildpath}/**/*.log')
    
    # Build per-job variables
    job_variables = {
        'EB_MODULE_NAME': job_info['module'],
    }

    # Point TMPDIR at the buildpath so large CUDA .run extractions don't
    # overflow a small /tmp tmpfs and SIGSEGV the installer.
    if buildpath:
        job_variables['TMPDIR'] = f'{buildpath}/tmp'
        job_variables['EASYBUILD_TMPDIR'] = f'{buildpath}/tmp'

    # Create job definition
    job = {
        'stage': stage_name,
        'script': [eb_command],
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
