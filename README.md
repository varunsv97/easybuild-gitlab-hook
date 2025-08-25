# EasyBuild GitLab CI GPU Pipeline Setup

This directory contains the GitLab CI hook and configuration for building GPU-accelerated software packages using EasyBuild. It's designed to work with CUDA toolchains and GPU-specific packages on the Hopper architecture.

## Files

- `gitlab_hook.py` - EasyBuild hook that generates GitLab CI pipelines instead of SLURM jobs
- `inject_defaults.py` - Script to add default configuration to generated pipelines
- `.gitlab-ci.yml` - Main GitLab CI configuration that triggers child pipelines
- `build_gpu_packages.sh` - Example build script for GPU packages

## Setup

### 1. Environment Configuration

```bash
# Set up EasyBuild environment
source /data/rosi/shared/eb/easybuild_environments/hopper/eb_env/bin/activate

# Configure paths
export patheb=/data/rosi/shared/eb
export architecture_rosi=hopper

# Enable GitLab CI pipeline generation
export EASYBUILD_GITLAB_CI_GENERATE=1
```

### 2. Usage

Generate a GitLab CI pipeline for GPU packages:

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
   CUDA-Python-12.4.0-gfbf-2023b-CUDA-12.4.0.eb \
   PyTorch-bundle-2.1.2-foss-2023a-CUDA-12.1.1.eb \
   TensorFlow-2.15.1-foss-2023a-CUDA-12.1.1.eb \
   [... more GPU packages ...] \
   --robot --job --insecure-download --disable-mpi-tests --skip-test-step --skip-test-cases \
   --ignore-checksums \
   --accept-eula-for=Intel-oneAPI,CUDA,NVHPC,cuDNN --force \
   --trace
```

### 3. Post-Processing

After the pipeline is generated, inject the default configuration:

```bash
python inject_defaults.py easybuild-child-pipeline.yml
```

This adds:
- EasyBuild environment activation
- Custom runner tags
- JWT tokens for authentication
- Retry and timeout configurations

## Pipeline Structure

The generated pipeline will:

1. **Create individual jobs** for each GPU package
2. **Handle dependencies** automatically (CUDA toolchains first, then packages)
3. **Use GPU runners** with appropriate tags
4. **Include CUDA-specific options** like compute capabilities
5. **Collect artifacts** including logs and build outputs

## GPU Package Examples

Common GPU packages that can be built:

- **CUDA Toolchains**: NVHPC, CUDA runtime
- **Deep Learning**: PyTorch, TensorFlow, JAX
- **Scientific Computing**: CuPy, GROMACS with CUDA
- **Math Libraries**: cuDNN, cuBLAS, cuSPARSE
- **Visualization**: Blender with CUDA support

## Architecture

- **Target**: Hopper (H100 GPUs)
- **CUDA Compute Capability**: 9.0
- **Partition**: gpu-h100
- **Max Walltime**: 96 hours
- **Cores per job**: 16

## Notes

- All packages are built with `--ignore-checksums` for flexibility
- CUDA compute capabilities are set to 9.0 for H100 GPUs
- Build and log directories use `/tmp` for better I/O performance
- Automatic cleanup of temporary directories after builds
