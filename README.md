# EasyBuild GitLab CI GPU Pipeline Setup

This directory contains the GitLab CI hook and configuration for building GPU-accelerated software packages using EasyBuild. It is designed for CUDA toolchains and GPU-specific packages on the Hopper architecture.

**Key Feature:** The hook runs EasyBuild in dry-run mode to resolve dependencies and generate GitLab CI pipelines **without submitting actual SLURM jobs**.

## Files

- `gitlab_hook.py`: EasyBuild hook that generates GitLab CI pipelines instead of SLURM jobs.
- `inject_defaults.py`: Script to add default configuration to generated pipelines.
- `.gitlab-ci.yml`: Main GitLab CI configuration that triggers child pipelines.
- `FIXED_ENVIRONMENT_VARIABLES.md`: Documentation of environment variable fixes.
- `DRY_RUN_CHANGES.md`: Details about dry-run implementation.

## How It Works

### Problem Solved
Previously, the hook allowed EasyBuild to submit SLURM jobs instead of only resolving dependencies and creating a GitLab CI pipeline file.

### Solution: Dry-Run Implementation
The hook now uses EasyBuild's `--dry-run` mode to:

1. Parse and resolve all dependencies with `--robot`.
2. Prepare job information without submission.
3. Capture complete dependency data via hooks.
4. Generate a GitLab CI pipeline with proper job dependencies.
5. Exit cleanly without starting actual builds.

### Hook Strategy
- **`post_ready_hook`**: Captures easyconfig objects after dependency resolution.
- **`pre_build_and_install_loop_hook`**: Generates the GitLab CI pipeline and exits.
- Enhanced processing: Handles dependency mapping for GitLab CI jobs.

## Setup

### 1. Environment Configuration

```bash
# Set up EasyBuild environment
source /data/rosi/shared/eb/easybuild_environments/hopper/eb_env/bin/activate

# Configure paths
export patheb=/data/rosi/shared/eb
export architecture_rosi=hopper

# Enable GitLab CI pipeline generation
export GITLAB_CI_GENERATE=1

# Optional: Set job parameters
export JOB_OUTPUT_DIR=/path/to/output
export JOB_CORES=16
export JOB_MAX_WALLTIME=96
```

**Important:** Use `GITLAB_CI_GENERATE` (not `EASYBUILD_GITLAB_CI_GENERATE`) to avoid EasyBuild environment variable validation errors.

### 2. Usage

Run EasyBuild with the following command:

```bash
eb --hooks=gitlab_hook.py \
   --installpath=${patheb}/${architecture_rosi} \
   --installpath-modules=${patheb}/${architecture_rosi}/modules \
   --tmp-logdir=/tmp/eblog \
   --buildpath=/tmp/ebbuild \
   --sourcepath=/data/rosi/shared/eb/easybuild_source \
   --cuda-compute-capabilities=9.0 \
   --job-cores=16 \
   --job-max-walltime=96 \
   --dry-run \
   --robot --job --insecure-download --disable-mpi-tests --skip-test-step --skip-test-cases \
   --ignore-checksums \
   --accept-eula-for=Intel-oneAPI,CUDA,NVHPC,cuDNN --force \
   --trace \
   Blender-4.3.2-GCCcore-13.3.0-linux-x86_64-CUDA-12.6.0.eb \
   CUDA-Python-12.4.0-gfbf-2023b-CUDA-12.4.0.eb \
   CUTLASS-3.4.0-foss-2023a-CUDA-12.1.1.eb \
   Clang-18.1.8-GCCcore-13.3.0-CUDA-12.6.0.eb
```

**Key flags:**
- `--dry-run`: Prevents actual SLURM job submission.
- `--robot`: Resolves all dependencies automatically.
- `--job`: Enables job mode (required for hook activation).

### 3. Post-Processing

After the pipeline is generated, inject configuration:

```bash
python inject_defaults.py easybuild-child-pipeline.yml
```

This adds:
- EasyBuild environment activation.
- Custom runner tags (`rosi-admin-slurm`).
- JWT tokens for authentication.
- Retry and timeout configurations.
- Hopper-specific variables.

## Expected Behavior

When you run the GitLab CI pipeline:

1. **Dependency Resolution:** EasyBuild resolves all dependencies.
2. **Hook Execution:** GitLab CI hook captures dependency information.
3. **Pipeline Generation:** Creates `easybuild-child-pipeline.yml` with all jobs.
4. **Dependency Mapping:** Jobs include proper `needs` relationships.
5. **Clean Exit:** Process stops after pipeline generation (no actual builds).

## Pipeline Structure

The generated pipeline will:

- Create individual jobs for each GPU package and dependency.
- Handle dependencies automatically (CUDA toolchains first, then packages).
- Use GPU runners with appropriate tags (`rosi-admin-slurm`, `gpu-h100`).
- Include CUDA-specific options like compute capabilities.
- Collect artifacts including logs and build outputs.
- Support retry logic for failed jobs.

## GPU Package Examples

Common GPU packages that can be built:

- **CUDA Toolchains:** NVHPC, CUDA runtime.
- **Deep Learning:** PyTorch, TensorFlow, JAX.
- **Scientific Computing:** CuPy, GROMACS with CUDA.
- **Math Libraries:** cuDNN, cuBLAS, cuSPARSE.
- **Visualization:** Blender with CUDA support.

## Architecture Configuration

- **Target:** Hopper (H100 GPUs)
- **CUDA Compute Capability:** 9.0
- **Partition:** gpu-h100
- **Max Walltime:** 96 hours
- **Cores per job:** 16

## Debug Information

The hook includes comprehensive logging. Look for messages starting with `[GitLab CI Hook]` to track:

- Environment variable detection.
- Number of easyconfigs processed.
- Hook function calls.
- Pipeline generation progress.
- File creation status.

## Troubleshooting

### Common Issues

1. **Environment Variable Error:** Use `GITLAB_CI_GENERATE=1` not `EASYBUILD_GITLAB_CI_GENERATE=1`.
2. **No Pipeline File:** Check that `--dry-run` and `--job` flags are present.
3. **SLURM Jobs Submitted:** Ensure `--dry-run` flag is included in the command.
4. **Missing Dependencies:** Verify `--robot` flag is enabling dependency resolution.

### Environment Variables

Fixed environment variables to avoid conflicts:
- `GITLAB_CI_GENERATE`: Enables GitLab CI mode.
- `JOB_OUTPUT_DIR`: Output directory for pipeline files.
- `JOB_CORES`: Number of cores per job.
- `JOB_MAX_WALLTIME`: Maximum walltime for jobs.

## Notes

- All packages are built with `--ignore-checksums` for flexibility.
- CUDA compute capabilities are set to 9.0 for H100 GPUs.
- Build and log directories use `/tmp` for better I/O performance.
- Automatic cleanup of temporary directories after builds.
- The hook generates pipelines **without submitting actual builds** to SLURM.
