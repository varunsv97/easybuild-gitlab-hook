#!/usr/bin/env python3
"""
Test script to verify the hook's easyconfig format handling.
This simulates the data structure that EasyBuild passes to the hook.
"""

import sys
import os

# Add current directory to path so we can import the hook
sys.path.insert(0, '/home/sudhar46/easybuild-gitlab-hook')

# Mock EasyBuild classes for testing
class MockEasyConfig:
    def __init__(self, name, version):
        self.name = name
        self.version = version
        self.toolchain = {'name': 'GCCcore', 'version': '12.3.0'}
        self.all_dependencies = []

class MockMNS:
    def det_full_module_name(self, ec):
        return f"{ec.name}/{ec.version}"

# Mock the EasyBuild imports
class MockModule:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

sys.modules['easybuild.tools.config'] = MockModule(build_option=lambda x: None)
sys.modules['easybuild.tools.modules'] = MockModule(ActiveMNS=lambda: MockMNS())
sys.modules['easybuild.tools.filetools'] = MockModule(mkdir=lambda x, **kwargs: None, write_file=lambda x, y: None)
sys.modules['vsc.utils'] = MockModule(fancylogger=MockModule(getLogger=lambda x, **kwargs: MockModule(info=print, warning=print, error=print, debug=print)))

# Import the hook functions
from gitlab_hook import _process_easyconfigs_for_jobs, PIPELINE_JOBS

def test_easyconfig_format():
    """Test the format that EasyBuild actually uses."""
    
    # This is the format shown in the debug output
    test_easyconfigs = [
        {
            'ec': MockEasyConfig('scipy', '1.11.1'),
            'spec': '/path/to/scipy-1.11.1-gfbf-2023a.eb', 
            'short_mod_name': 'scipy/1.11.1',
            'full_mod_name': 'scipy/1.11.1-gfbf-2023a',
            'dependencies': [],
            'builddependencies': [],
            'hiddendependencies': [],
            'hidden': False
        },
        {
            'ec': MockEasyConfig('numpy', '1.24.3'),
            'spec': '/path/to/numpy-1.24.3-gfbf-2023a.eb',
            'short_mod_name': 'numpy/1.24.3', 
            'full_mod_name': 'numpy/1.24.3-gfbf-2023a',
            'dependencies': [],
            'builddependencies': [],
            'hiddendependencies': [],
            'hidden': False
        }
    ]
    
    print("Testing easyconfig processing...")
    _process_easyconfigs_for_jobs(test_easyconfigs)
    
    print(f"\nResult: {len(PIPELINE_JOBS)} jobs created")
    for job_name, job_info in PIPELINE_JOBS.items():
        print(f"  - {job_name}: {job_info['name']}")

if __name__ == '__main__':
    test_easyconfig_format()
