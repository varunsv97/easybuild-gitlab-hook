"""
GitLab CI Hook for EasyBuild

This hook generates GitLab CI child pipelines with job dependencies instead of 
submitting to SLURM directly. It works exactly like the SLURM backend but creates
GitLab CI jobs that run via Jacamar CI Batch.

Usage:
  # Enable GitLab CI generation and set environment variable
  export EASYBUILD_GITLAB_CI_GENERATE=1
  
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
import json
from collections import defaultdict, deque

from easybuild.base import fancylogger
from easybuild.tools.build_log import EasyBuildError, print_msg
from easybuild.tools.config import build_option
from easybuild.tools.filetools import write_file, mkdir
from easybuild.framework.easyconfig.easyconfig import ActiveMNS
from easybuild.tools.module_naming_scheme.utilities import det_full_ec_version


# Global variables to track pipeline state
PIPELINE_JOBS = {}
JOB_DEPENDENCIES = {}
GITLAB_CONFIG = {}

def start_hook(*args, **kwargs):
    """Initialize GitLab CI pipeline generation."""
    global PIPELINE_JOBS, JOB_DEPENDENCIES, GITLAB_CONFIG
    
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    # Check if GitLab CI generation is enabled
    if not (build_option('gitlab_ci_generate') and build_option('job')):
        return
    
    log.info("[GitLab CI Hook] Initializing GitLab CI pipeline generation")
    log.info("[GitLab CI Hook] Running in GitLab CI mode (equivalent to --job with SLURM backend)")
    
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
    log.info("[GitLab CI Hook] Will process multiple easyconfigs just like SLURM backend")


def pre_build_and_install_loop_hook(ecs, *args, **kwargs):
    """Hook called before starting the build and install loop with all easyconfigs."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    # Check if GitLab CI generation is enabled and job mode is enabled
    if not (build_option('gitlab_ci_generate') and build_option('job')):
        return
    
    log.info("[GitLab CI Hook] Processing %d easyconfigs for GitLab CI pipeline generation", len(ecs))
    
    # Process easyconfigs linearly like SLURM backend does
    # Dependencies have already been resolved by --robot
    _process_easyconfigs_for_jobs(ecs)
    
    # Generate pipeline YAML
    _generate_gitlab_pipeline()
    
    # Stop EasyBuild execution after pipeline generation
    log.info("[GitLab CI Hook] GitLab CI pipeline generated. Stopping EasyBuild execution.")
    raise SystemExit(0)


def _process_easyconfigs_for_jobs(easyconfigs):
    """Process easyconfigs linearly like SLURM backend, building job dependency map."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    global PIPELINE_JOBS, JOB_DEPENDENCIES
    
    # Reset global state
    PIPELINE_JOBS = {}
    JOB_DEPENDENCIES = {}
    
    # Keep track of which job builds which module (like SLURM backend)
    module_to_job = {}
    
    # Process each easyconfig linearly
    for easyconfig in easyconfigs:
        # Get module name
        try:
            module_name = ActiveMNS().det_full_module_name(easyconfig['ec'])
        except Exception as err:
            log.warning("Could not determine module name for %s: %s", easyconfig.get('spec', 'unknown'), err)
            continue
        
        easyconfig_name = os.path.basename(easyconfig.get('spec', ''))
        
        # Get dependencies that are not external modules (like SLURM backend)
        deps = [d for d in easyconfig['ec'].all_dependencies if not d.get('external_module', False)]
        
        # Map dependency module names to job names
        dep_mod_names = []
        job_deps = []
        for dep in deps:
            try:
                dep_mod_name = ActiveMNS().det_full_module_name(dep)
                dep_mod_names.append(dep_mod_name)
                # Only include dependencies that are being built in this pipeline
                if dep_mod_name in module_to_job:
                    job_deps.append(dep_mod_name)
            except Exception as err:
                log.warning("Could not determine module name for dependency %s: %s", dep, err)
        
        # Create job entry
        job_info = {
            'name': easyconfig_name,
            'module': module_name,
            'easyconfig_path': easyconfig.get('spec', ''),
            'dependencies': dep_mod_names,  # All deps for reference
            'job_dependencies': job_deps,   # Only deps being built in this pipeline
            'toolchain': easyconfig['ec']['toolchain'],
            'version': easyconfig['ec']['version'],
            'cores': build_option('job_cores') or 1,
            'walltime': build_option('job_max_walltime') or 24,
        }
        
        PIPELINE_JOBS[module_name] = job_info
        JOB_DEPENDENCIES[module_name] = job_deps  # Only pipeline dependencies for GitLab CI
        
        # Update module-to-job mapping (like SLURM backend)
        module_to_job[module_name] = job_info
        
        log.debug("[GitLab CI Hook] Added job '%s' with %d total deps, %d pipeline deps", 
                 module_name, len(dep_mod_names), len(job_deps))
    
    log.info("[GitLab CI Hook] Processed %d easyconfigs for GitLab CI jobs", len(PIPELINE_JOBS))


def _calculate_job_stages():
    """Calculate pipeline stages based on dependency depth."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    stages = {}
    visited = set()
    
    def get_stage(module_name):
        if module_name in visited:
            return stages.get(module_name, 0)
        
        visited.add(module_name)
        
        # If no dependencies, it's stage 0
        deps = JOB_DEPENDENCIES.get(module_name, [])
        if not deps:
            stages[module_name] = 0
            return 0
        
        # Calculate max dependency stage + 1
        max_dep_stage = 0
        for dep in deps:
            if dep in PIPELINE_JOBS:  # Only consider deps that are being built
                dep_stage = get_stage(dep)
                max_dep_stage = max(max_dep_stage, dep_stage)
        
        stages[module_name] = max_dep_stage + 1
        return stages[module_name]
    
    # Calculate stages for all jobs
    for module_name in PIPELINE_JOBS:
        get_stage(module_name)
    
    log.info("[GitLab CI Hook] Calculated stages for %d jobs (max stage: %d)", 
             len(stages), max(stages.values()) if stages else 0)
    
    return stages


def _generate_gitlab_pipeline():
    """Generate the complete GitLab CI pipeline YAML."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    if not PIPELINE_JOBS:
        log.warning("[GitLab CI Hook] No jobs to generate pipeline for")
        return
    
    # Calculate stages - each job gets its own stage named after the easyconfig
    job_stages = {}
    stages = []
    
    # Create stages list using easyconfig names
    for module_name, job_info in PIPELINE_JOBS.items():
        stage_name = _sanitize_job_name(job_info['name'].replace('.eb', ''))
        job_stages[module_name] = stage_name
        if stage_name not in stages:
            stages.append(stage_name)
    
    # Create minimal pipeline structure - only essential variables
    pipeline = {
        'stages': stages,
        'variables': {
            # Essential EasyBuild variables
            'EASYBUILD_PREFIX': '/tmp/easybuild',
            'EASYBUILD_MODULES_TOOL': 'Lmod',
            # Inherit Jacamar CI Batch parameters
            'SCHEDULER_PARAMETERS': '$SCHEDULER_PARAMETERS',
            'SBATCH_ACCOUNT': '$SBATCH_ACCOUNT',
            'SBATCH_PARTITION': '$SBATCH_PARTITION',
            'SBATCH_QOS': '$SBATCH_QOS',
            # Preserve architecture and path variables if set
            'patheb': '${patheb:-/tmp/easybuild}',
            'architecture_rosi': '${architecture_rosi:-x86_64}',
        },
    }
    
    # Add jobs
    for module_name, job_info in PIPELINE_JOBS.items():
        stage_name = job_stages[module_name]
        job_yaml = _create_gitlab_job(job_info, stage_name)
        
        # Sanitize job name for GitLab CI
        sanitized_name = _sanitize_job_name(module_name)
        pipeline[sanitized_name] = job_yaml
        
        # Add dependencies
        deps = JOB_DEPENDENCIES.get(module_name, [])
        if deps:
            # Only include dependencies that are being built in this pipeline
            pipeline_deps = [_sanitize_job_name(dep) for dep in deps if dep in PIPELINE_JOBS]
            if pipeline_deps:
                pipeline[sanitized_name]['needs'] = pipeline_deps
    
    # Write pipeline file
    output_dir = build_option('job_output_dir') or os.getcwd()
    mkdir(output_dir, parents=True)
    
    pipeline_file = os.path.join(output_dir, 'easybuild-child-pipeline.yml')
    pipeline_yaml = yaml.dump(pipeline, default_flow_style=False, width=120, sort_keys=False)
    
    write_file(pipeline_file, pipeline_yaml)
    
    log.info("[GitLab CI Hook] Generated GitLab CI pipeline: %s", pipeline_file)
    
    # Generate summary
    _generate_pipeline_summary(pipeline_file, job_stages)


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
    
    # Add EULA acceptance
    accept_eula = build_option('accept_eula_for')
    if accept_eula:
        eb_command += f' --accept-eula-for={accept_eula}'
    
    # Add the easyconfig
    eb_command += ' ' + os.path.basename(job_info['easyconfig_path'])
    
    # Create minimal job definition - only essential elements
    job = {
        'stage': stage_name,
        'tags': ['batch'],  # Jacamar CI Batch tag
        'script': [
            '# Create required directories',
            'mkdir -p $EASYBUILD_PREFIX',
            'mkdir -p /data/rosi/shared/eb/${architecture_rosi}_tmplog',
            'mkdir -p /data/rosi/shared/eb/${architecture_rosi}_tmpbuild',
            'mkdir -p /data/rosi/shared/eb/easybuild_source',
            '# Run EasyBuild installation',
            eb_command
        ],
        'variables': {
            'EB_MODULE_NAME': job_info['module'],
            'SLURM_CPUS_PER_TASK': str(job_info['cores']),
        },
        'timeout': '%dh' % job_info['walltime'],
        'artifacts': {
            'when': 'always',
            'paths': ['*.log', '*.out', '*.err'],
            'expire_in': '1 week',
        }
    }
    
    # Add Jacamar CI Batch specific configuration
    if job_info['cores'] > 1:
        job['variables']['SBATCH_CPUS_PER_TASK'] = str(job_info['cores'])
    
    if job_info['walltime'] > 1:
        job['variables']['SBATCH_TIME'] = '%d:00:00' % job_info['walltime']
    
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


def _generate_pipeline_summary(pipeline_file, job_stages):
    """Generate and display pipeline summary."""
    log = fancylogger.getLogger('gitlab_hook', fname=False)
    
    total_jobs = len(PIPELINE_JOBS)
    total_stages = len(set(job_stages.values()))
    
    print_msg("\n" + "="*80, log=log)
    print_msg("GitLab CI Pipeline Generated Successfully!", log=log)
    print_msg("="*80, log=log)
    print_msg("Pipeline file: %s" % pipeline_file, log=log)
    print_msg("Total jobs: %d" % total_jobs, log=log)
    print_msg("Total stages: %d (named after easyconfigs)" % total_stages, log=log)
    
    # Show some example stages
    example_stages = list(set(job_stages.values()))[:5]
    print_msg("Example stages: %s" % ', '.join(example_stages), log=log)
    if len(job_stages) > 5:
        print_msg("  ... and %d more" % (len(job_stages) - 5), log=log)
    
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
    
    if build_option('gitlab_ci_generate') and build_option('job'):
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
            return (os.environ.get('EASYBUILD_GITLAB_CI_GENERATE', '').lower() in ['1', 'true', 'yes'] or
                    '--gitlab-ci-generate' in (sys.argv if hasattr(sys, 'argv') else []) or
                    any('gitlab-ci' in arg.lower() for arg in (sys.argv if hasattr(sys, 'argv') else [])))
        
        return eb_build_option(option_name)
    except:
        # Fallback for options that might not exist
        if option_name == 'gitlab_ci_generate':
            return (os.environ.get('EASYBUILD_GITLAB_CI_GENERATE', '').lower() in ['1', 'true', 'yes'] or
                    any('gitlab-ci' in arg.lower() for arg in (sys.argv if hasattr(sys, 'argv') else [])))
        elif option_name == 'job_output_dir':
            return os.environ.get('EASYBUILD_JOB_OUTPUT_DIR', os.getcwd())
        elif option_name == 'job_cores':
            return int(os.environ.get('EASYBUILD_JOB_CORES', '1'))
        elif option_name == 'job_max_walltime':
            return int(os.environ.get('EASYBUILD_JOB_MAX_WALLTIME', '24'))
        elif option_name == 'robot':
            return '--robot' in (sys.argv if hasattr(sys, 'argv') else [])
        elif option_name == 'job':
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
