"""Offline regression checks for the POSIX setup and launcher scripts."""
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from vktech.settings import ROOT


def executable(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def shell_project(tmp_path):
    # Spaces exercise shell quoting as well as the common Linux setup.
    root = tmp_path / 'project with spaces'
    (root / 'scripts').mkdir(parents=True)
    (root / 'frontend').mkdir()
    for name in ('pnpm.sh', 'start_all.sh'):
        shutil.copyfile(ROOT / 'scripts' / name, root / 'scripts' / name)
    shutil.copyfile(ROOT / 'frontend/package.json', root / 'frontend/package.json')
    binaries = root / 'bin'
    binaries.mkdir()
    (binaries / 'dirname').symlink_to(shutil.which('dirname'))
    env = dict(os.environ, PATH=str(binaries))
    return root, binaries, env


def run_script(root, env, name, *args):
    return subprocess.run(['/bin/sh', str(root / 'scripts' / name), *args],
                          env=env, capture_output=True, text=True, timeout=15)


def test_pnpm_missing_node_has_actionable_error(shell_project):
    root, _, env = shell_project
    result = run_script(root, env, 'pnpm.sh', '--version')
    assert result.returncode == 2
    assert 'Node.js 22.13+' in result.stderr


def test_pnpm_rejects_old_node_before_install(shell_project):
    root, binaries, env = shell_project
    executable(binaries / 'node', '#!/bin/sh\nif [ "$1" = --version ]; then echo v20.0.0; else exit 1; fi\n')
    result = run_script(root, env, 'pnpm.sh', '--version')
    assert result.returncode == 2
    assert 'found v20.0.0' in result.stderr
    assert not (root / '.tools').exists()


@pytest.mark.parametrize('cached', [False, True])
def test_pnpm_bypasses_broken_global_shims(shell_project, cached):
    root, binaries, env = shell_project
    node = shutil.which('node')
    if not node:
        pytest.skip('Node.js is needed for the local pnpm wrapper test')
    (binaries / 'node').symlink_to(node)
    for name in ('corepack', 'pnpm'):
        executable(binaries / name, '#!/bin/sh\necho "Broken global shim was used" >&2\nexit 99\n')
    version = json.loads((root / 'frontend/package.json').read_text())['packageManager'].split('@')[1]
    entry = root / '.tools' / 'pnpm' / version / 'node_modules/pnpm/bin/pnpm.mjs'
    stub = 'console.log(JSON.stringify({cwd:process.cwd(),args:process.argv.slice(2)}));'
    if cached:
        executable(entry, stub)
    else:
        executable(binaries / 'npm', f'#!{sys.executable}\n' +
            'import json, sys\nfrom pathlib import Path\n' +
            'prefix = Path(sys.argv[sys.argv.index("--prefix") + 1])\n' +
            'entry = prefix / "node_modules/pnpm/bin/pnpm.mjs"\n' +
            'entry.parent.mkdir(parents=True, exist_ok=True)\n' +
            f'entry.write_text({stub!r})\n' +
            '(prefix / "install-args.json").write_text(json.dumps(sys.argv[1:]))\n')
    result = run_script(root, env, 'pnpm.sh', 'run', 'build', 'argument with spaces')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'cwd': str(root / 'frontend'),
                                         'args': ['run', 'build', 'argument with spaces']}
    if not cached:
        args = json.loads((root / '.tools/pnpm' / version / 'install-args.json').read_text())
        assert f'pnpm@{version}' in args
        assert '--ignore-scripts' in args and '--package-lock=false' in args
        assert '--global' not in args


@pytest.mark.parametrize('absolute', [False, True])
def test_launcher_resolves_renderer_names_and_paths(shell_project, absolute):
    root, binaries, env = shell_project
    for name in ('soffice', 'pdftoppm', 'curl'):
        executable(binaries / name, '#!/bin/sh\nexit 0\n')
    (root / 'frontend/dist').mkdir()
    (root / 'frontend/dist/index.html').write_text('<html></html>')
    # Stop at API configuration validation, before any service is started.
    executable(root / '.venv/bin/python', '#!/bin/sh\necho "dependencies-checked"\nexit 47\n')
    (root / '.env').write_text('\n'.join(
        f'{var}={shlex.quote(str(binaries / name) if absolute else name)}'
        for var, name in [('SOFFICE', 'soffice'), ('PDFTOPPM', 'pdftoppm')]))
    result = run_script(root, env, 'start_all.sh')
    assert result.returncode == 47, result.stderr
    assert 'dependencies-checked' in result.stdout


def test_frontend_build_policy_and_container_use_pinned_wrapper():
    assert yaml.safe_load((ROOT / 'frontend/pnpm-workspace.yaml').read_text()) == {'allowBuilds': {'esbuild': True}}
    for path in ('Dockerfile', 'scripts/setup_local.sh', 'scripts/start_all.sh'):
        source = (ROOT / path).read_text()
        assert 'scripts/pnpm.sh' in source
        assert 'corepack pnpm' not in source and 'corepack prepare' not in source
