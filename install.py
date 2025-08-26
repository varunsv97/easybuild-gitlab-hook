import os
import shutil
import yaml
import reframe as rfm
import reframe.utility.sanity as sn

# Configuration from environment
easyconfigs_yaml = os.environ.get('EASYCONFIGS_YAML', 'easyconfigs.yaml')
working_dir = os.environ.get('CI_PROJECT_DIR', os.getcwd())
easyconfigs_path = os.path.join(working_dir, easyconfigs_yaml)
arch = os.environ.get('ARCH')


def load_easyconfigs():
    if not os.path.exists(easyconfigs_path):
        raise FileNotFoundError(f'{easyconfigs_path} not found')
    with open(easyconfigs_path) as f:
        data = yaml.safe_load(f)
    # Expect YAML to have a top-level 'easyconfigs' list
    return data.get('easyconfigs', [])



@rfm.simple_test
class EasyBuildInstall(rfm.RunOnlyRegressionTest):
    descr = 'Install easyconfigs from YAML using EasyBuild'
    valid_systems = [f'rosi:{arch}']
    valid_prog_environs = ['easybuild']
    executable = 'eb'

    num_tasks = int(os.environ.get('NTASKS', '4'))
    num_tasks_per_node = num_tasks

    memory = os.environ.get('MEMORY', '10G')
    extra_resources = {'memory': {'memory': memory}}

    source_path = os.environ.get('SOURCE_PATH')
    patheb = os.environ.get('EB_PATH')
    hook = os.environ.get('HOOK')

    easyconfigs = load_easyconfigs()

    @run_before('run')
    def run_installation(self):
        architecture = self.current_partition.name
        base_opts = [
            f'--hooks={self.hook}',
            f'--installpath={self.patheb}/{architecture}',
            f'--installpath-modules={self.patheb}/{architecture}/modules',
            f'--tmp-logdir=eb_logs/{architecture}_tmplog',
            f'--buildpath=eb_logs/{architecture}_tmpbuild',
            f'--sourcepath={self.source_path}',
            f'--robot-paths={working_dir}/easyconfigs',
            f'--parallel={self.num_tasks}',
            '--experimental',
            '--force',
            '--robot',
            '--ignore-checksums',
            '--insecure-download',
            '--disable-mpi-tests',
            '--skip-test-step',
            '--skip-test-cases',
            '--detect-loaded-modules=unload',
            '--accept-eula-for=Intel-oneAPI,CUDA,NVHPC,cuDNN'
        ]
        if architecture == 'ampere':
            self.extra_resources = {'gpu': {'num_gpus_per_node': '1'}}
            base_opts.append('--cuda-compute-capabilities=8.0')
        elif architecture == 'hopper':
            base_opts.append('--cuda-compute-capabilities=9.0')
        elif architecture == 'blackwell':
            base_opts.append('--cuda-compute-capabilities=9.2')
        # Pass all easyconfigs together as arguments
        self.executable_opts = self.easyconfigs + base_opts
        self.env_vars
        self.logger.info(f'Running EasyBuild with: {self.executable} {" ".join(self.executable_opts)}')

    @sanity_function
    def assert_success(self):
        return sn.all([
            sn.assert_found('SUCCESS', self.stdout)
        ])