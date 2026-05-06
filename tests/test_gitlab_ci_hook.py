import copy
import importlib
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


class _DummyLogger:
    def debug(self, *args, **kwargs):
        return None

    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        return None


def _install_test_stubs():
    yaml_mod = types.ModuleType("yaml")
    yaml_mod.safe_load = lambda _stream: {}
    yaml_mod.dump = lambda data, **_kwargs: repr(data)
    sys.modules["yaml"] = yaml_mod

    easybuild_mod = types.ModuleType("easybuild")
    base_mod = types.ModuleType("easybuild.base")
    fancylogger_mod = types.ModuleType("easybuild.base.fancylogger")
    fancylogger_mod.getLogger = lambda *_args, **_kwargs: _DummyLogger()

    tools_mod = types.ModuleType("easybuild.tools")
    build_log_mod = types.ModuleType("easybuild.tools.build_log")
    build_log_mod.print_msg = lambda *_args, **_kwargs: None

    filetools_mod = types.ModuleType("easybuild.tools.filetools")
    filetools_mod.write_file = lambda path, content: Path(path).write_text(content)
    filetools_mod.mkdir = lambda path, parents=False: Path(path).mkdir(parents=parents, exist_ok=True)

    framework_mod = types.ModuleType("easybuild.framework")
    framework_easyconfig_mod = types.ModuleType("easybuild.framework.easyconfig")
    easyconfig_mod = types.ModuleType("easybuild.framework.easyconfig.easyconfig")

    class _DefaultActiveMNS:
        def det_full_module_name(self, item):
            if isinstance(item, dict):
                if "module_name" in item:
                    return item["module_name"]
                return f"{item['name']}/{item['version']}"
            if hasattr(item, "module_name"):
                return item.module_name
            return f"{item.name}/{item.version}"

    easyconfig_mod.ActiveMNS = _DefaultActiveMNS

    base_mod.fancylogger = fancylogger_mod
    tools_mod.build_log = build_log_mod
    tools_mod.filetools = filetools_mod
    framework_easyconfig_mod.easyconfig = easyconfig_mod
    framework_mod.easyconfig = framework_easyconfig_mod

    easybuild_mod.base = base_mod
    easybuild_mod.tools = tools_mod
    easybuild_mod.framework = framework_mod

    sys.modules["easybuild"] = easybuild_mod
    sys.modules["easybuild.base"] = base_mod
    sys.modules["easybuild.base.fancylogger"] = fancylogger_mod
    sys.modules["easybuild.tools"] = tools_mod
    sys.modules["easybuild.tools.build_log"] = build_log_mod
    sys.modules["easybuild.tools.filetools"] = filetools_mod
    sys.modules["easybuild.framework"] = framework_mod
    sys.modules["easybuild.framework.easyconfig"] = framework_easyconfig_mod
    sys.modules["easybuild.framework.easyconfig.easyconfig"] = easyconfig_mod


_install_test_stubs()
HOOK = importlib.import_module("gitlab_ci_hook")


class _FakeEC:
    def __init__(self, module_name, deps=None, name=None, version=None, versionsuffix="", toolchain=None):
        self.module_name = module_name
        base_name, base_version = module_name.split("/", 1)
        self.name = name or base_name
        self.version = version or base_version
        self.versionsuffix = versionsuffix
        self.path = f"/tmp/{self.name}-{self.version}.eb"
        self.toolchain = toolchain or {"name": "foss", "version": "2023a"}
        self.dependencies = []
        self.builddependencies = []
        self.all_dependencies = deps or []


class _FakeActiveMNS:
    def det_full_module_name(self, item):
        if isinstance(item, dict):
            return item["module_name"]
        return item.module_name


class GitLabCIHookTests(unittest.TestCase):
    def setUp(self):
        HOOK.PIPELINE_JOBS = {}
        HOOK.JOB_DEPENDENCIES = {}
        HOOK.JOB_NAME_MAP = {}

    def test_sanitize_job_name(self):
        self.assertEqual(HOOK._sanitize_job_name("foo/1.0+cuda"), "foo-1.0pluscuda")
        self.assertEqual(HOOK._sanitize_job_name("1foo"), "job-1foo")
        self.assertEqual(HOOK._sanitize_job_name(""), "unknown-job")

    def test_create_gitlab_job_filters_args_and_adds_dry_run(self):
        job_info = {
            "module": "Foo/1.2.3",
            "easyconfig_path": "/tmp/Foo-1.2.3-foss-2023a.eb",
        }
        argv = [
            "eb",
            "--hooks",
            "gitlab_ci_hook.py",
            "--tmp-logdir=eblog",
            "--buildpath",
            "ebbuild",
            "--robot",
            "--trace",
            "/tmp/Other-0.1.eb",
        ]

        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict(os.environ, {"DRYRUN": "true"}, clear=False):
                job = HOOK._create_gitlab_job(job_info, "build")

        self.assertEqual(job["script"][0], 'mkdir -p "$TMPDIR"')
        command = job["script"][-1]
        self.assertIn("--dry-run", command)
        self.assertIn("Foo-1.2.3-foss-2023a.eb", command)
        self.assertNotIn("--hooks", command)
        self.assertNotIn("Other-0.1.eb", command)

        artifact_paths = job["artifacts"]["paths"]
        self.assertEqual(artifact_paths[0], "eblog/*.log")
        self.assertEqual(artifact_paths[1], "ebbuild/**/*.log")
        self.assertEqual(job["variables"]["EB_MODULE_NAME"], "Foo/1.2.3")
        # TMPDIR should be made job-stable so it doesn't become relative to
        # EasyBuild's package-specific build directory.
        self.assertEqual(job["variables"]["TMPDIR"], "${CI_PROJECT_DIR}/ebbuild/tmp")
        self.assertEqual(job["variables"]["EASYBUILD_TMPDIR"], "${CI_PROJECT_DIR}/ebbuild/tmp")

    def test_create_gitlab_job_keeps_absolute_buildpath_for_tmpdir(self):
        job_info = {
            "module": "Foo/1.2.3",
            "easyconfig_path": "/tmp/Foo-1.2.3-foss-2023a.eb",
        }
        argv = [
            "eb",
            "--buildpath=/scratch/ebbuild",
            "--robot",
        ]

        with mock.patch.object(sys, "argv", argv):
            job = HOOK._create_gitlab_job(job_info, "build")

        self.assertEqual(job["script"][0], 'mkdir -p "$TMPDIR"')
        self.assertEqual(job["script"][1], "eb --buildpath=/scratch/ebbuild --robot Foo-1.2.3-foss-2023a.eb")
        self.assertEqual(job["variables"]["TMPDIR"], "/scratch/ebbuild/tmp")
        self.assertEqual(job["variables"]["EASYBUILD_TMPDIR"], "/scratch/ebbuild/tmp")

    def test_create_gitlab_job_templates_generation_args_for_matrix_jobs(self):
        job_info = {
            "module": "Foo/1.2.3",
            "easyconfig_path": "/tmp/Foo-1.2.3-foss-2023a.eb",
        }
        argv = [
            "eb",
            "--installpath=/opt/easybuild/hopper",
            "--installpath-modules=/opt/easybuild/hopper/modules",
            "--sourcepath=/srv/sources",
            "--max-parallel=8",
            "--cuda-compute-capabilities=9.0",
            "--robot",
        ]
        env = {
            "EB_PATH": "/opt/easybuild",
            "ARCH": "hopper",
            "SOURCE_PATH": "/srv/sources",
            "NTASKS_PER_NODE": "8",
            "CUDA_COMPUTE_OPTION": "--cuda-compute-capabilities=9.0",
        }

        with mock.patch.object(sys, "argv", argv):
            with mock.patch.dict(os.environ, env, clear=True):
                job = HOOK._create_gitlab_job(job_info, "build")

        command = job["script"][-1]
        self.assertIn("--installpath=${EB_PATH}/${ARCH}", command)
        self.assertIn("--installpath-modules=${EB_PATH}/${ARCH}/modules", command)
        self.assertIn("--sourcepath=${SOURCE_PATH}", command)
        self.assertIn("--max-parallel=${NTASKS_PER_NODE}", command)
        self.assertIn("${CUDA_COMPUTE_OPTION}", command)

    def test_create_gitlab_job_preserves_options_that_share_control_prefix(self):
        job_info = {
            "module": "Foo/1.2.3",
            "easyconfig_path": "/tmp/Foo-1.2.3.eb",
        }
        argv = [
            "eb",
            "--hooks",
            "gitlab_ci_hook.py",
            "--job-cores=4",
            "--job",
            "--robot",
            "/tmp/Other-0.1.eb",
        ]

        with mock.patch.object(sys, "argv", argv):
            job = HOOK._create_gitlab_job(job_info, "build")

        self.assertEqual(job["script"][-1], "eb --job-cores=4 --robot Foo-1.2.3.eb")

    def test_create_gitlab_job_strips_easystack_from_child_command(self):
        job_info = {
            "module": "Foo/1.2.3",
            "easyconfig_path": "/tmp/Foo-1.2.3.eb",
        }
        argv = [
            "eb",
            "--easystack=easybuild-easystack.yml",
            "--robot",
            "--tmp-logdir=eblog",
        ]

        with mock.patch.object(sys, "argv", argv):
            job = HOOK._create_gitlab_job(job_info, "build")

        command = job["script"][-1]
        self.assertIn("--robot", command)
        self.assertIn("--tmp-logdir=eblog", command)
        self.assertTrue(command.endswith("Foo-1.2.3.eb"))
        self.assertNotIn("--easystack", command)

    def test_inject_configuration_merges_defaults_and_skips_self_reference(self):
        pipeline = {
            "stages": ["build"],
            "variables": {"KEEP": "original"},
            "my-job": {"script": ["echo ok"]},
        }
        default_config = {
            "before_script": ["echo setup"],
            "tags": ["runner-a"],
            "retry": 2,
        }
        child_variables = {
            "KEEP": "new-value",
            "EXTRA": "42",
            "SELF_REF": "$SELF_REF",
        }

        result = HOOK._inject_configuration(copy.deepcopy(pipeline), default_config, child_variables)

        self.assertEqual(list(result.keys())[:3], ["stages", "variables", "default"])
        self.assertEqual(result["variables"]["KEEP"], "original")
        self.assertEqual(result["variables"]["EXTRA"], "42")
        self.assertNotIn("SELF_REF", result["variables"])
        self.assertEqual(result["default"]["retry"], 2)
        self.assertEqual(result["default"]["tags"], ["runner-a"])

    def test_inject_configuration_adds_default_retry_when_missing(self):
        pipeline = {"stages": ["build"], "variables": {}}
        result = HOOK._inject_configuration(pipeline, default_config={}, child_variables={})
        self.assertEqual(result["default"]["retry"]["max"], 2)
        self.assertIn("runner_system_failure", result["default"]["retry"]["when"])

    def test_process_easyconfigs_resolves_internal_dependencies_only(self):
        ec_a = _FakeEC(
            "A/1.0",
            deps=[
                {"module_name": "B/1.0"},
                {"module_name": "B/1.0"},
                {"module_name": "External/9.9", "external_module": True},
            ],
        )
        ec_b = _FakeEC("B/1.0", deps=[])
        ec_c = _FakeEC("C/1.0", deps=[{"module_name": "C/1.0"}])

        with mock.patch.object(HOOK, "ActiveMNS", _FakeActiveMNS):
            HOOK._process_easyconfigs_for_jobs([ec_a, ec_b, ec_c])

        self.assertEqual(set(HOOK.PIPELINE_JOBS.keys()), {"A/1.0", "B/1.0", "C/1.0"})
        self.assertEqual(HOOK.JOB_DEPENDENCIES["A/1.0"], ["B/1.0"])
        self.assertEqual(HOOK.JOB_DEPENDENCIES["B/1.0"], [])
        self.assertEqual(HOOK.JOB_DEPENDENCIES["C/1.0"], [])

    def test_process_easyconfigs_reuses_active_mns_instance(self):
        ec_a = _FakeEC("A/1.0", deps=[{"module_name": "B/1.0"}])
        ec_b = _FakeEC("B/1.0", deps=[])
        instances = []

        class _CountingActiveMNS:
            def __init__(self):
                instances.append(self)

            def det_full_module_name(self, item):
                if isinstance(item, dict):
                    return item["module_name"]
                return item.module_name

        with mock.patch.object(HOOK, "ActiveMNS", _CountingActiveMNS):
            HOOK._process_easyconfigs_for_jobs([ec_a, ec_b])

        self.assertEqual(len(instances), 1)
        self.assertEqual(HOOK.JOB_DEPENDENCIES["A/1.0"], ["B/1.0"])

    def test_process_easyconfigs_falls_back_when_dependency_module_lookup_fails(self):
        dep_pkgconf = {
            "name": "pkgconf",
            "version": "2.2.0",
            "versionsuffix": "",
            "toolchain": {"name": "nvidia-compilers", "version": "25.3-CUDA-12.8.0"},
            "toolchain_inherited": True,
            "build_only": True,
            "external_module": False,
        }
        ec_openmpi = _FakeEC(
            "OpenMPI/5.0.3",
            deps=[dep_pkgconf],
            name="OpenMPI",
            version="5.0.3",
            toolchain={"name": "nvidia-compilers", "version": "25.3-CUDA-12.8.0"},
        )
        ec_pkgconf = _FakeEC(
            "pkgconf/2.2.0",
            deps=[],
            name="pkgconf",
            version="2.2.0",
            toolchain={"name": "GCCcore", "version": "13.3.0"},
        )

        class _FallbackActiveMNS:
            def det_full_module_name(self, item):
                if isinstance(item, dict):
                    raise RuntimeError("missing easyconfig for dependency")
                return item.module_name

        with mock.patch.object(HOOK, "ActiveMNS", _FallbackActiveMNS):
            HOOK._process_easyconfigs_for_jobs([ec_openmpi, ec_pkgconf])

        self.assertEqual(HOOK.JOB_DEPENDENCIES["OpenMPI/5.0.3"], ["pkgconf/2.2.0"])
        self.assertEqual(HOOK.JOB_DEPENDENCIES["pkgconf/2.2.0"], [])

    def test_process_easyconfigs_inherited_dep_resolves_among_multiple_matches(self):
        """When multiple pipeline records match name+version, inherited deps should still resolve."""
        dep_pkgconf = {
            "name": "pkgconf",
            "version": "2.2.0",
            "versionsuffix": "",
            "toolchain": {"name": "nvidia-compilers", "version": "25.3-CUDA-12.8.0"},
            "toolchain_inherited": True,
            "build_only": True,
            "external_module": False,
        }
        ec_openmpi = _FakeEC(
            "OpenMPI/5.0.3",
            deps=[dep_pkgconf],
            name="OpenMPI",
            version="5.0.3",
            toolchain={"name": "nvidia-compilers", "version": "25.3-CUDA-12.8.0"},
        )
        ec_pkgconf_gcc13 = _FakeEC(
            "pkgconf-gcc13/2.2.0",
            deps=[],
            name="pkgconf",
            version="2.2.0",
            toolchain={"name": "GCCcore", "version": "13.3.0"},
        )
        ec_pkgconf_gcc14 = _FakeEC(
            "pkgconf-gcc14/2.2.0",
            deps=[],
            name="pkgconf",
            version="2.2.0",
            toolchain={"name": "GCCcore", "version": "14.2.0"},
        )

        class _FallbackActiveMNS:
            def det_full_module_name(self, item):
                if isinstance(item, dict):
                    raise RuntimeError("missing easyconfig for dependency")
                return item.module_name

        with mock.patch.object(HOOK, "ActiveMNS", _FallbackActiveMNS):
            HOOK._process_easyconfigs_for_jobs([ec_openmpi, ec_pkgconf_gcc13, ec_pkgconf_gcc14])

        self.assertEqual(HOOK.JOB_DEPENDENCIES["OpenMPI/5.0.3"], ["pkgconf-gcc13/2.2.0"])

    def test_generate_base_pipeline_handles_job_name_collisions(self):
        HOOK.PIPELINE_JOBS = {
            "pkg/1.0+cuda": {
                "module": "pkg/1.0+cuda",
                "easyconfig_path": "/tmp/pkgA.eb",
            },
            "pkg-1.0pluscuda": {
                "module": "pkg-1.0pluscuda",
                "easyconfig_path": "/tmp/pkgB.eb",
            },
        }
        HOOK.JOB_DEPENDENCIES = {
            "pkg/1.0+cuda": [],
            "pkg-1.0pluscuda": ["pkg/1.0+cuda"],
        }

        with mock.patch.object(sys, "argv", ["eb", "--robot"]):
            pipeline = HOOK._generate_base_pipeline()

        self.assertIn("pkg-1.0pluscuda", pipeline)
        self.assertIn("pkg-1.0pluscuda-2", pipeline)
        self.assertEqual(pipeline["pkg-1.0pluscuda-2"]["needs"], ["pkg-1.0pluscuda"])

    def test_generate_base_pipeline_omits_optional_variables_without_env(self):
        HOOK.PIPELINE_JOBS = {
            "pkg/1.0": {
                "module": "pkg/1.0",
                "easyconfig_path": "/tmp/pkg.eb",
            },
        }
        HOOK.JOB_DEPENDENCIES = {"pkg/1.0": []}

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(sys, "argv", ["eb", "--robot"]):
                pipeline = HOOK._generate_base_pipeline()

        self.assertEqual(pipeline["variables"], {"EASYBUILD_MODULES_TOOL": "Lmod"})

    def test_generate_base_pipeline_keeps_explicit_cuda_env(self):
        HOOK.PIPELINE_JOBS = {
            "pkg/1.0": {
                "module": "pkg/1.0",
                "easyconfig_path": "/tmp/pkg.eb",
            },
        }
        HOOK.JOB_DEPENDENCIES = {"pkg/1.0": []}

        env = {
            "CUDA_COMPUTE_CAPABILITIES": "8.6,9.0",
            "SCHEDULER_PARAMETERS": "--partition=gpu-a100 --gres=gpu:1",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(sys, "argv", ["eb", "--robot"]):
                pipeline = HOOK._generate_base_pipeline()

        self.assertEqual(
            pipeline["variables"]["EASYBUILD_CUDA_COMPUTE_CAPABILITIES"], "8.6,9.0"
        )
        self.assertNotIn("CUDA_COMPUTE_CAPABILITIES", pipeline["variables"])
        self.assertEqual(
            pipeline["variables"]["SCHEDULER_PARAMETERS"],
            "--partition=gpu-a100 --gres=gpu:1",
        )

    def test_generate_base_pipeline_prefers_easybuild_prefixed_cuda_env(self):
        """EASYBUILD_CUDA_COMPUTE_CAPABILITIES takes precedence over unprefixed."""
        HOOK.PIPELINE_JOBS = {
            "pkg/1.0": {
                "module": "pkg/1.0",
                "easyconfig_path": "/tmp/pkg.eb",
            },
        }
        HOOK.JOB_DEPENDENCIES = {"pkg/1.0": []}

        env = {
            "EASYBUILD_CUDA_COMPUTE_CAPABILITIES": "7.0,8.0",
            "CUDA_COMPUTE_CAPABILITIES": "9.0",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch.object(sys, "argv", ["eb", "--robot"]):
                pipeline = HOOK._generate_base_pipeline()

        self.assertEqual(
            pipeline["variables"]["EASYBUILD_CUDA_COMPUTE_CAPABILITIES"], "7.0,8.0"
        )

    def test_load_gitlab_ci_config_extracts_default_and_child_variables(self):
        config_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp_file:
                tmp_file.write("dummy: true\n")
                config_path = Path(tmp_file.name)

            config_data = {
                "default": {"tags": ["rosi-slurm"]},
                "execute_builds": {"variables": {"SCHEDULER_PARAMETERS": "--nodes=1"}},
            }

            with mock.patch.object(HOOK.yaml, "safe_load", return_value=config_data):
                default, variables = HOOK._load_gitlab_ci_config(config_path)

            self.assertEqual(default, {"tags": ["rosi-slurm"]})
            self.assertEqual(variables, {"SCHEDULER_PARAMETERS": "--nodes=1"})
        finally:
            if config_path and config_path.exists():
                config_path.unlink()


if __name__ == "__main__":
    unittest.main(verbosity=2)
