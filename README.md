# EasyBuild GitLab CI Hook

This repository contains an EasyBuild hook that automatically generates GitLab CI pipeline YAML files from EasyBuild easyconfigs. It analyzes dependencies and creates a child pipeline with proper job dependencies for distributed builds on HPC clusters.

## Prerequisites

This hook requires:

1. **GitLab Runner** installed and configured on your HPC cluster
2. **Jacamar CI** - GitLab Runner executor for HPC batch systems (SLURM, PBS, LSF)
   - Project: https://gitlab.com/ecp-ci/jacamar-ci
   - Documentation: https://ecp-ci.gitlab.io/
3. **EasyBuild** environment with Python and required modules

Jacamar CI enables GitLab Runner to submit jobs to HPC schedulers, allowing GitLab CI pipelines to execute on compute nodes with proper resource allocation.

## How It Works

The hook intercepts EasyBuild execution and generates a GitLab CI pipeline:

1. **Dependency Resolution:** EasyBuild resolves all dependencies with `--robot`
2. **Hook Capture:** Captures easyconfig objects and dependency information
3. **Pipeline Generation:** Creates `easybuild-child-pipeline.yml` with proper job dependencies
4. **Configuration Injection:** Reads `.gitlab-ci.yml` and injects `default:` and `variables:` sections
5. **Clean Exit:** Exits before any builds start

### Pipeline Components

**Main Pipeline (`.gitlab-ci.yml`):**
- `generate_pipeline` job: Runs EasyBuild with hook to generate child pipeline
- `execute_builds` job: Triggers the generated child pipeline

**Generated Child Pipeline (`easybuild-child-pipeline.yml`):**
- Individual jobs for each package/dependency
- Proper `needs:` relationships for dependency ordering
- Dynamic artifact paths from `--tmp-logdir` and `--buildpath`
- All configuration inherited from main pipeline

See `.gitlab-ci.yml` in this repository for a complete example.

## Quick Start

### 1. Setup GitLab CI Configuration

Create `.gitlab-ci.yml` with two stages:
- **generate:** Run EasyBuild with hook to create child pipeline
- **build:** Trigger the generated child pipeline

The hook reads configuration from:
- `default:` section - tags, before_script, id_tokens, retry settings
- `execute_builds.variables:` - SCHEDULER_PARAMETERS and other child pipeline variables

**Example:** See `.gitlab-ci.yml` in this repository

### 2. Run the Pipeline

Push to GitLab and the pipeline will:
- Generate child pipeline from your easyconfigs
- Inject configuration automatically
- Execute builds on HPC compute nodes via Jacamar CI

### 3. Local Testing

```bash
source /path/to/easybuild/venv/bin/activate

eb --hooks=gitlab_ci_hook.py \
   --installpath=/path/to/software \
   --tmp-logdir=eblog \
   --buildpath=ebbuild \
   --robot \
   YourPackage.eb

cat easybuild-child-pipeline.yml
```

## Key Features

✅ **Automatic dependency mapping** - Jobs include proper `needs:` relationships  
✅ **Configuration inheritance** - Reads everything from `.gitlab-ci.yml`  
✅ **Dynamic artifact paths** - Uses `--tmp-logdir` and `--buildpath` from your command  
✅ **Command preservation** - All eb options automatically passed to child jobs  
✅ **HPC integration** - Works with Jacamar CI for SLURM/PBS/LSF job submission  

## Configuration

The hook automatically reads from `.gitlab-ci.yml`:

**From `default:` section:**
- `tags` - Runner tags (e.g., for selecting HPC runners)
- `before_script` - Setup commands (module loads, environment activation)
- `id_tokens` - JWT authentication
- `retry` - Retry configuration for failed jobs

**From `execute_builds.variables:`:**
- `SCHEDULER_PARAMETERS` - HPC scheduler parameters (nodes, cores, partition, memory, etc.)
- Custom variables for child pipeline jobs

## Architecture Examples

**CPU (Genoa/Milan/Turin):**
```yaml
execute_builds:
  variables:
    SCHEDULER_PARAMETERS: "--nodes=1 --ntasks-per-node=8 --partition=cpu-genoa --mem=200G"
```

**GPU (Ampere/Hopper):**
```yaml
execute_builds:
  variables:
    SCHEDULER_PARAMETERS: "--nodes=1 --ntasks-per-node=16 --partition=gpu-a100 --gres=gpu:1 --mem=400G"
```

## Troubleshooting

**Circular variable reference error:**
Remove self-referencing variables like `EB_PATH: $EB_PATH` from `execute_builds.variables`

**Artifacts not found:**
Ensure `--tmp-logdir` and `--buildpath` are set in your eb command

**Missing configuration:**
Verify `.gitlab-ci.yml` exists in the working directory during pipeline generation

**Jobs not running on HPC:**
Check that Jacamar CI is properly configured and GitLab Runner has access to the HPC scheduler

## Debug Information

Look for these messages during pipeline generation:

```
*** GITLAB CI HOOK LOADED ***
*** START_HOOK CALLED ***
*** PRE_BUILD_AND_INSTALL_LOOP_HOOK CALLED ***
*** Using N ready easyconfigs ***
*** Finished processing - created N jobs ***
*** Pipeline generated - exiting ***
```

Detailed logs start with `[GitLab CI Hook]` showing configuration loading, dependency mapping, and pipeline generation.
