"""
GitLab CI Hook for EasyBuild

This hook generates GitLab CI child pipelines with job dependencies instead of 
submitting to SLURM directly. It works exactly like the SLURM backend but creates
GitLab CI jobs that run via Jacamar CI Batch.

Usage:
  # Enable GitLab CI generation and set environment variable
  export GITLAB_CI_GENERATE=1
  
  # Run just like you would with SLURM backend
  eb --hooks=gitlab_hook.py --robot --job pkg1.eb pkg2.eb
  
  # Or with multiple easyconfigs
  eb --hooks=gitlab_hook.py --robot --job *.eb

The hook will:
1. Process all easyconfigs linearly (like SLURM backend)
2. Resolve dependencies with --robot
3. Create GitLab CI jobs with proper dependencies
4. Generate easybuild-child-pipeline.yml
5. Stop execution (preventing actual builds)

Author: Custom GitLab CI Integration
"""

import os
import sys
import yaml
from easybuild.base import fancylogger
from easybuild.tools.build_log import print_msg
from easybuild.tools.config import build_option
from easybuild.tools.filetools import write_file, mkdir
from easybuild.framework.easyconfig.easyconfig import ActiveMNS


# Print a message when the hook module is loaded
print("*** GITLAB HOOK LOADED ***")
log = fancylogger.getLogger('gitlab_hook', fname=False)
log.info("GitLab CI Hook module loaded successfully")

# Global variables to track pipeline state
PIPELINE_JOBS = {}
JOB_DEPENDENCIES = {}
GITLAB_CONFIG = {}

def start_hook(*args, **kwargs):
    """Initialize GitLab CI pipeline generation."""
    global PIPELINE_JOBS, JOB_DEPENDENCIES, GITLAB_CONFIG
    
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    # Always print this to help with debugging
    print("*** START_HOOK CALLED ***")
    log.info("*** START_HOOK CALLED ***")
    
    # Debug: Print environment and option detection
    gitlab_ci_env = os.environ.get('GITLAB_CI_GENERATE', '')
    
    log.info("[GitLab CI Hook] DEBUG: GITLAB_CI_GENERATE env var: '%s'", gitlab_ci_env)
    log.info("[GitLab CI Hook] DEBUG: build_option('gitlab_ci_generate'): %s", build_option('gitlab_ci_generate'))
    
    # Check if GitLab CI generation is enabled (no longer require --job)
    if not build_option('gitlab_ci_generate'):
        log.info("[GitLab CI Hook] GitLab CI mode not enabled - exiting hook")
        return
    
    log.info("[GitLab CI Hook] Initializing GitLab CI pipeline generation")
    log.info("[GitLab CI Hook] Running in GitLab CI mode (will intercept before builds)")
    
    # Reset global state
    PIPELINE_JOBS = {}
    JOB_DEPENDENCIES = {}
    
    # Load GitLab configuration
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
    """Hook called early in the process - try to intercept here."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    print("*** PRE_CONFIGURE_HOOK CALLED ***")
    log.info("*** PRE_CONFIGURE_HOOK CALLED ***")
    
    # Check if GitLab CI generation is enabled (no longer require --job)
    if not build_option('gitlab_ci_generate'):
        return
    
    log.info("[GitLab CI Hook] GitLab CI mode detected in pre_configure_hook")


def parse_hook(ec_dict):
    """Hook called when parsing easyconfig files - collect them for GitLab CI pipeline generation."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    # Check if GitLab CI generation is enabled (no longer require --job)
    if not build_option('gitlab_ci_generate'):
        return ec_dict
    
    # Store easyconfig for pipeline generation
    global PARSED_ECS
    if 'PARSED_ECS' not in globals():
        PARSED_ECS = []
    
    PARSED_ECS.append(ec_dict)
    log.debug("[GitLab CI Hook] Collected easyconfig: %s", ec_dict.get('spec', 'unknown'))
    
    return ec_dict


def post_ready_hook(ec, *args, **kwargs):
    """Hook called when easyconfig is ready - use this to collect dependency info."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    # Check if GitLab CI generation is enabled (no longer require --job)
    if not build_option('gitlab_ci_generate'):
        return
    
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
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    # Debug logging
    print("*** PRE_BUILD_AND_INSTALL_LOOP_HOOK CALLED ***")
    print(f"*** Received {len(ecs)} easyconfigs ***")
    log.info("[GitLab CI Hook] pre_build_and_install_loop_hook called with %d easyconfigs", len(ecs))
    log.info("[GitLab CI Hook] DEBUG: build_option('gitlab_ci_generate'): %s", build_option('gitlab_ci_generate'))
    
    # Check if GitLab CI generation is enabled (no longer require --job)
    if not build_option('gitlab_ci_generate'):
        print("*** GitLab CI mode not enabled - exiting ***")
        log.info("[GitLab CI Hook] GitLab CI mode not enabled in pre_build_and_install_loop_hook - exiting")
        return
    
    print("*** GitLab CI mode enabled - proceeding ***")
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
        # Generate pipeline YAML
        _generate_gitlab_pipeline()
        
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
    """Process easyconfigs linearly like SLURM backend, building job dependency map."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    global PIPELINE_JOBS, JOB_DEPENDENCIES
    
    # Reset global state
    PIPELINE_JOBS = {}
    JOB_DEPENDENCIES = {}
    
    # Keep track of which job builds which module (like SLURM backend)
    module_to_job = {}
    
    log.info("[GitLab CI Hook] Processing %d easyconfigs", len(easyconfigs))
    
    # Process each easyconfig linearly
    for i, easyconfig in enumerate(easyconfigs):
        try:
            # Handle different easyconfig formats
            if isinstance(easyconfig, dict):
                if 'ec' in easyconfig:
                    # This is the format we're getting: {'ec': <EasyConfig>, 'spec': '...', ...}
                    ec = easyconfig['ec']
                    spec = easyconfig.get('spec', 'unknown')
                    easyconfig_name = f"{ec.name}-{ec.version}.eb"
                elif 'name' in easyconfig and 'version' in easyconfig:
                    # post_ready_hook format (fallback)
                    ec = easyconfig.get('ec')
                    if ec is None:
                        log.warning("Skipping easyconfig %d: no 'ec' object found", i)
                        continue
                    easyconfig_name = f"{easyconfig['name']}-{easyconfig['version']}.eb"
                    spec = easyconfig.get('spec', easyconfig_name)
                else:
                    # Try to get ec anyway
                    ec = easyconfig.get('ec')
                    if ec is None:
                        log.warning("Skipping easyconfig %d: no 'ec' key found", i)
                        continue
                    easyconfig_name = f"{ec.name}-{ec.version}.eb"
                    spec = easyconfig.get('spec', easyconfig_name)
            else:
                # Direct easyconfig object
                ec = easyconfig
                easyconfig_name = f"{ec.name}-{ec.version}.eb"
                spec = getattr(ec, 'path', easyconfig_name)
            
            # Get module name
            try:
                module_name = ActiveMNS().det_full_module_name(ec)
            except Exception as err:
                log.warning("Could not determine module name for %s: %s", spec, err)
                continue
            
            # Use the same dependency logic as EasyBuild's SLURM backend
            # Get all dependencies, filter out external modules, then only include
            # dependencies that are being built in this pipeline
            all_deps = [d for d in ec.all_dependencies if not d.get('external_module', False)]
            
            # Map dependency module names to job names
            dep_mod_names = []
            job_deps = []
            
            # Process all dependencies (same as SLURM backend)
            for dep in all_deps:
                try:
                    dep_mod_name = ActiveMNS().det_full_module_name(dep)
                    dep_mod_names.append(dep_mod_name)
                    # Only include dependencies that are being built in this pipeline (same as SLURM backend)
                    if dep_mod_name in module_to_job:
                        # Store the dependency module name (not job name) for consistency with PIPELINE_JOBS keys
                        job_deps.append(dep_mod_name)
                        log.debug("[GitLab CI Hook] Added dependency to job '%s': %s", easyconfig_name, dep_mod_name)
                    else:
                        log.debug("[GitLab CI Hook] Skipped dependency for job '%s' (not in pipeline): %s", easyconfig_name, dep_mod_name)
                except Exception as err:
                    log.warning("Could not determine module name for dependency %s: %s", dep, err)
            
            # Create job entry
            job_info = {
                'name': easyconfig_name,
                'module': module_name,
                'easyconfig_path': spec,
                'dependencies': dep_mod_names,  # All deps for reference
                'job_dependencies': job_deps,   # Only deps being built in this pipeline
                'toolchain': ec.toolchain,
                'version': ec.version,
                'cores': build_option('job_cores') or 1,
                'walltime': build_option('job_max_walltime') or 24,
            }
            
            PIPELINE_JOBS[module_name] = job_info
            JOB_DEPENDENCIES[module_name] = job_deps  # Only pipeline dependencies for GitLab CI
            
            # Update module-to-job mapping (like SLURM backend)
            module_to_job[module_name] = job_info
            
            log.info("[GitLab CI Hook] Added job '%s' (%s) with %d total deps, %d pipeline deps: %s", 
                     module_name, easyconfig_name, len(dep_mod_names), len(job_deps), job_deps)
        
        except Exception as err:
            log.error("[GitLab CI Hook] Error processing easyconfig %d: %s", i, err)
            log.error("[GitLab CI Hook] Easyconfig type: %s", type(easyconfig))
            if isinstance(easyconfig, dict):
                log.error("[GitLab CI Hook] Easyconfig keys: %s", list(easyconfig.keys()))
            continue
    
    print(f"*** Finished processing - created {len(PIPELINE_JOBS)} jobs ***")
    log.info("[GitLab CI Hook] Processed %d easyconfigs for GitLab CI jobs", len(PIPELINE_JOBS))



def _generate_gitlab_pipeline():
    """Generate the complete GitLab CI pipeline YAML."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    if not PIPELINE_JOBS:
        log.warning("[GitLab CI Hook] No jobs to generate pipeline for")
        return
    
    # Set all jobs to a single stage for parallel execution
    stages = ['build']
    pipeline = {
        'stages': stages,
        'variables': {
            # Essential EasyBuild variables
            'EASYBUILD_MODULES_TOOL': 'Lmod',
            # Read scheduler parameters from environment
            'SCHEDULER_PARAMETERS': os.environ.get('SCHEDULER_PARAMETERS', '$SCHEDULER_PARAMETERS'),
            # Preserve path variables if set
            'patheb': os.environ.get('patheb', '$patheb'),
            # GPU configuration
            'CUDA_COMPUTE_CAPABILITIES': os.environ.get('CUDA_COMPUTE_CAPABILITIES', '9.0'),
            # Dry run option
            'DRYRUN': os.environ.get('DRYRUN', '$DRYRUN'),
        },
    }
    
    # Add jobs
    for module_name, job_info in PIPELINE_JOBS.items():
        sanitized_name = _sanitize_job_name(module_name)
        job_yaml = _create_gitlab_job(job_info, 'build')  # All jobs use 'build' stage
        job_yaml['stage'] = 'build'
        pipeline[sanitized_name] = job_yaml

        log.debug("[GitLab CI Hook] Created job '%s' for module '%s'", sanitized_name, module_name)

        # Add dependencies
        deps = JOB_DEPENDENCIES.get(module_name, [])
        log.debug("[GitLab CI Hook] Job '%s' has %d dependencies: %s", module_name, len(deps), deps)
        log.debug("[GitLab CI Hook] Available jobs in pipeline: %s", list(pipeline.keys()))

        if deps:
            pipeline_deps = []
            for dep in deps:
                sanitized_dep = _sanitize_job_name(dep)
                if sanitized_dep in pipeline:
                    pipeline_deps.append(sanitized_dep)
                    log.debug("[GitLab CI Hook] ✓ Added dependency '%s' -> '%s' for job '%s'", dep, sanitized_dep, module_name)
                else:
                    log.debug("[GitLab CI Hook] ✗ Skipping dependency '%s' for job '%s' - not in pipeline", dep, module_name)
            if pipeline_deps:
                pipeline[sanitized_name]['needs'] = pipeline_deps
                log.info("[GitLab CI Hook] Job '%s' needs: %s", sanitized_name, pipeline_deps)
            else:
                log.info("[GitLab CI Hook] Job '%s' has no pipeline dependencies", sanitized_name)
    
    # Write pipeline file
    output_dir = build_option('job_output_dir') or os.getcwd()
    mkdir(output_dir, parents=True)
    
    pipeline_file = os.path.join(output_dir, 'easybuild-child-pipeline.yml')
    pipeline_yaml = yaml.dump(pipeline, default_flow_style=False, width=120, sort_keys=False)

    write_file(pipeline_file, pipeline_yaml)
    
    log.info("[GitLab CI Hook] Generated GitLab CI pipeline: %s", pipeline_file)
    log.info("[GitLab CI Hook] Pipeline contains %d jobs with %d total dependencies", 
             len(PIPELINE_JOBS), sum(len(deps) for deps in JOB_DEPENDENCIES.values()))
    # Generate summary
    _generate_pipeline_summary(pipeline_file)


def _create_gitlab_job(job_info, stage_name):
    """Create a GitLab CI job definition."""
    
    # Build EasyBuild command with all relevant options
    eb_command = 'eb'
    
    # Add path options if set
    if build_option('installpath'):
        eb_command += f' --installpath={build_option("installpath")}'
    if build_option('installpath_modules'):
        eb_command += f' --installpath-modules={build_option("installpath_modules")}'
    if build_option('buildpath'):
        eb_command += f' --buildpath={build_option("buildpath")}'
    if build_option('sourcepath'):
        eb_command += f' --sourcepath={build_option("sourcepath")}'
    if build_option('tmp_logdir'):
        eb_command += f' --tmp-logdir={build_option("tmp_logdir")}'
    
    # Add robot if enabled
    if build_option('robot'):
        robot_paths = build_option('robot_paths') or []
        if robot_paths:
            eb_command += ' --robot=' + ':'.join(robot_paths)
        else:
            eb_command += ' --robot'
    
    # Add common build options
    if build_option('force'):
        eb_command += ' --force'
    if build_option('debug'):
        eb_command += ' --debug'
    if build_option('insecure'):
        eb_command += ' --insecure-download'
    if build_option('disable_mpi_tests'):
        eb_command += ' --disable-mpi-tests'
    if build_option('skip_test_step'):
        eb_command += ' --skip-test-step'
    if build_option('skip_test_cases'):
        eb_command += ' --skip-test-cases'
    if build_option('detect_loaded_modules'):
        eb_command += f' --detect-loaded-modules={build_option("detect_loaded_modules")}'
    
    # Add dry-run option only if DRYRUN variable is set to true
    if os.environ.get('DRYRUN', '').lower() in ['1', 'true', 'yes']:
        eb_command += ' --dry-run'
    
    # Add CUDA compute capabilities if set
    cuda_capabilities = os.environ.get('CUDA_COMPUTE_CAPABILITIES')
    if cuda_capabilities:
        eb_command += f' --cuda-compute-capabilities={cuda_capabilities}'
    
    # Add EULA acceptance
    accept_eula = build_option('accept_eula_for')
    if accept_eula:
        # If it's a list or string with brackets/quotes, format as comma-separated
        if isinstance(accept_eula, (list, tuple)):
            accept_eula_str = ','.join(str(x) for x in accept_eula)
        else:
            accept_eula_str = str(accept_eula).strip()
            if accept_eula_str.startswith('[') and accept_eula_str.endswith(']'):
                accept_eula_str = accept_eula_str[1:-1]
            accept_eula_str = accept_eula_str.replace("'", "").replace('"', "")
            accept_eula_str = ','.join([x.strip() for x in accept_eula_str.split(',')])
        eb_command += f' --accept-eula-for={accept_eula_str}'
    
    # Add the easyconfig
    eb_command += ' ' + os.path.basename(job_info['easyconfig_path'])
    
    # Create minimal job definition - only essential elements
    job = {
        'stage': stage_name,
        'script': [
            eb_comman
        ],
        'variables': {
            'EB_MODULE_NAME': job_info['module'],
            'SCHEDULER_PARAMETERS': os.environ.get('SCHEDULER_PARAMETERS', '$SCHEDULER_PARAMETERS'),
        },
        'artifacts': {
            'when': 'always',
            'paths': [],
            'expire_in': '1 week',
        }
    }
    
    # Add log files from tmplogdir as artifacts
    tmplogdir = build_option('tmp_logdir')
    if tmplogdir:
        # Add path for log files in the specified tmplogdir
        job['artifacts']['paths'].append(f'{tmplogdir}/*.log')
    else:
        # Fallback to current directory log files if no tmplogdir specified
        job['artifacts']['paths'].extend(['*.log', '*.out', '*.err'])
    
    return job


def _sanitize_job_name(name):
    """Sanitize module name for use as GitLab CI job name."""
    # Replace invalid characters with hyphens
    sanitized = name.replace('/', '-').replace(':', '-').replace('+', 'plus')
    sanitized = sanitized.replace('(', '').replace(')', '').replace(' ', '-')
    # Ensure it starts with a letter or underscore
    if sanitized and not (sanitized[0].isalpha() or sanitized[0] == '_'):
        sanitized = 'job-' + sanitized
    return sanitized or 'unknown-job'


def _generate_pipeline_summary(pipeline_file):
    """Generate and display pipeline summary."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    total_jobs = len(PIPELINE_JOBS)
    
    print_msg("\n" + "="*80, log=log)
    print_msg("GitLab CI Pipeline Generated Successfully!", log=log)
    print_msg("="*80, log=log)
    print_msg("Pipeline file: %s" % pipeline_file, log=log)
    print_msg("Total jobs: %d" % total_jobs, log=log)
    print_msg("\nTo trigger this pipeline, add to your .gitlab-ci.yml:", log=log)
    print_msg("", log=log)
    print_msg("easybuild_pipeline:", log=log)
    print_msg("  trigger:", log=log)
    print_msg("    include: %s" % os.path.basename(pipeline_file), log=log)
    print_msg("    strategy: depend", log=log)
    print_msg("  tags:", log=log)
    print_msg("    - batch", log=log)
    print_msg("", log=log)
    
    # Show example individual job commands
    if PIPELINE_JOBS:
        sample_job = next(iter(PIPELINE_JOBS.values()))
        print_msg("Example job command:", log=log)
        print_msg("  eb --robot %s" % os.path.basename(sample_job['easyconfig_path']), log=log)
    
    print_msg("="*80, log=log)


def end_hook(*args, **kwargs):
    """Cleanup hook called when EasyBuild finishes."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    if build_option('gitlab_ci_generate'):
        log.info("[GitLab CI Hook] GitLab CI pipeline generation completed")


# Register custom build options for GitLab CI
def modify_build_options():
    """Add custom build options for GitLab CI support."""
    try:
        from easybuild.tools.config import ConfigurationVariables
        from easybuild.tools.options import EasyBuildOptions
        
        # This would need to be integrated into EasyBuild's option system
        # For now, we'll check environment variables or use existing options
        pass
    except ImportError:
        pass


# Check if GitLab CI generation is enabled via environment or command line
def build_option(option_name):
    """Helper function to get build options, including custom GitLab CI options."""
    try:
        from easybuild.tools.config import build_option as eb_build_option
        
        # Handle custom GitLab CI options
        if option_name == 'gitlab_ci_generate':
            # Check environment variable or command line flag
            return (os.environ.get('GITLAB_CI_GENERATE', '').lower() in ['1', 'true', 'yes'] or
                    '--gitlab-ci-generate' in (sys.argv if hasattr(sys, 'argv') else []) or
                    any('gitlab-ci' in arg.lower() for arg in (sys.argv if hasattr(sys, 'argv') else [])))
        
        return eb_build_option(option_name)
    except:
        # Fallback for options that might not exist
        if option_name == 'gitlab_ci_generate':
            return (os.environ.get('GITLAB_CI_GENERATE', '').lower() in ['1', 'true', 'yes'] or
                    any('gitlab-ci' in arg.lower() for arg in (sys.argv if hasattr(sys, 'argv') else [])))
        elif option_name == 'job_output_dir':
            return os.environ.get('JOB_OUTPUT_DIR', os.getcwd())
        elif option_name == 'job_cores':
            return int(os.environ.get('JOB_CORES', '1'))
        elif option_name == 'job_max_walltime':
            return int(os.environ.get('JOB_MAX_WALLTIME', '24'))
        elif option_name == 'robot':
            return '--robot' in (sys.argv if hasattr(sys, 'argv') else [])
        elif option_name == 'job':
            # We don't actually need --job for GitLab CI mode
            return '--job' in (sys.argv if hasattr(sys, 'argv') else [])
        elif option_name == 'force':
            return '--force' in (sys.argv if hasattr(sys, 'argv') else [])
        elif option_name == 'debug':
            return '--debug' in (sys.argv if hasattr(sys, 'argv') else [])
        elif option_name == 'insecure':
            return '--insecure-download' in (sys.argv if hasattr(sys, 'argv') else [])
        elif option_name == 'disable_mpi_tests':
            return '--disable-mpi-tests' in (sys.argv if hasattr(sys, 'argv') else [])
        elif option_name == 'skip_test_step':
            return '--skip-test-step' in (sys.argv if hasattr(sys, 'argv') else [])
        elif option_name == 'skip_test_cases':
            return '--skip-test-cases' in (sys.argv if hasattr(sys, 'argv') else [])
        elif option_name in ['installpath', 'installpath_modules', 'buildpath', 'sourcepath', 'tmp_logdir', 'detect_loaded_modules', 'accept_eula_for']:
            # Extract option value from command line
            argv = sys.argv if hasattr(sys, 'argv') else []
            option_flag = f'--{option_name.replace("_", "-")}'
            for i, arg in enumerate(argv):
                if arg.startswith(f'{option_flag}='):
                    return arg.split('=', 1)[1]
                elif arg == option_flag and i + 1 < len(argv):
                    return argv[i + 1]
            return None
        else:
            return None
