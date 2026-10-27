"""Exercise executable startup in fresh processes, before config is imported."""

import json
import os
from pathlib import Path
import subprocess
import sys


def launch(directory: Path, overrides: dict[str, str] | None = None) -> dict:
    root = str(Path(__file__).resolve().parents[1])
    env = {name: value for name, value in os.environ.items()
           if not name.startswith("VEGAPUNK_") and name != "ZAI_KEY"}
    env.update({"PYTHONPATH": root, **(overrides or {})})
    code = """
import importlib.abc, importlib.util, json, os, runpy, sys
class CliLoader(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'vegapunk.cli':
            return importlib.util.spec_from_loader(fullname, self)
    def create_module(self, spec):
        return None
    def exec_module(self, module):
        # Like the real CLI, bind config at module import, not inside main().
        from vegapunk.config import config
        def main(*, auto=False):
            print(json.dumps({'has_key': bool(os.getenv('ZAI_KEY')),
                              'provider': config.provider, 'effort': config.zai_effort,
                              'auto': auto}))
        module.main = main
sys.meta_path.insert(0, CliLoader())
sys.argv = ['vegapunk', '--auto']
runpy.run_module('vegapunk', run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", code], cwd=directory, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_local_env_is_loaded_before_configuration(tmp_path):
    (tmp_path / ".env").write_text("ZAI_KEY=fake-test-key\nVEGAPUNK_PROVIDER=zai\nVEGAPUNK_ZAI_EFFORT=high\n")
    assert launch(tmp_path) == {"has_key": True, "provider": "zai", "effort": "high", "auto": True}


def test_explicit_environment_wins_over_file(tmp_path):
    (tmp_path / ".env").write_text("ZAI_KEY=fake-test-key\nVEGAPUNK_PROVIDER=zai\nVEGAPUNK_ZAI_EFFORT=high\n")
    result = launch(tmp_path, {"ZAI_KEY": "exported-test-key", "VEGAPUNK_PROVIDER": "local", "VEGAPUNK_ZAI_EFFORT": "low"})
    assert result == {"has_key": True, "provider": "local", "effort": "low", "auto": True}


def test_no_env_file_keeps_normal_defaults(tmp_path):
    assert launch(tmp_path) == {"has_key": False, "provider": "zai", "effort": "low", "auto": True}


def test_explicit_empty_key_is_not_overwritten(tmp_path):
    (tmp_path / ".env").write_text("ZAI_KEY=file-test-key\n")
    assert launch(tmp_path, {"ZAI_KEY": ""})["has_key"] is False


def test_parent_environment_file_is_not_discovered(tmp_path):
    (tmp_path / ".env").write_text("ZAI_KEY=parent-test-key\nVEGAPUNK_PROVIDER=local\n")
    child = tmp_path / "workspace"
    child.mkdir()
    assert launch(child) == {"has_key": False, "provider": "zai", "effort": "low", "auto": True}
