"""Lifecycle helpers for memory-safe local text/image model switching."""
from __future__ import annotations
import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen
from .settings import ROOT, artifact_path


def _listener(port: int):
    result=subprocess.run(['lsof','-nP',f'-iTCP:{port}','-sTCP:LISTEN','-t'],capture_output=True,text=True)
    return int(result.stdout.splitlines()[0]) if result.returncode==0 and result.stdout.strip() else None


def _wait(url: str,ready: bool,timeout=180):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        try:
            with urlopen(url,timeout=2) as response:online=200<=response.status<500
        except Exception:online=False
        if online==ready:return
        time.sleep(1)
    raise RuntimeError(f'Local model endpoint did not become {"ready" if ready else "stopped"}: {url}')


@contextlib.contextmanager
def local_image_phase():
    """Unload MLX-VLM while Z-Image runs, then restore it for vision audit."""
    if os.environ.get('LOCAL_MODEL_SEQUENTIAL','0')!='1':
        yield;return
    base=os.environ.get('MODEL_BASE_URL','')
    if not (base.startswith('http://127.0.0.1:') or base.startswith('http://localhost:')):
        yield;return
    port=int(base.split(':')[2].split('/')[0]);pid=_listener(port)
    if pid is None:
        yield;return
    command=subprocess.run(['ps','-p',str(pid),'-o','command='],capture_output=True,text=True).stdout
    if 'mlx_vlm.server' not in command:raise RuntimeError('Refusing to stop an unknown process on the local model port')
    os.kill(pid,signal.SIGTERM);_wait(base+'/models',False,30)
    try:yield
    finally:
        log=artifact_path('logs/model-server.log');log.parent.mkdir(parents=True,exist_ok=True)
        stream=log.open('ab')
        subprocess.Popen([str(ROOT/'scripts/start_mlx_model.sh')],cwd=ROOT,env=os.environ.copy(),stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        _wait(base+'/models',True,180)
