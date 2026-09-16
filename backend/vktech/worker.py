from __future__ import annotations
import json
import logging
import threading
import time
from .store import Store
from .pipeline import Pipeline
from .model import ModelUnavailable
from .planning import NeedsInput

log=logging.getLogger(__name__)


def execute(store,job,gateway=None):
    stopped=threading.Event()
    def heartbeat():
        while not stopped.wait(8):
            try:store.owned_update(job.id,job.lease_owner,lease_until=time.time()+30)
            except RuntimeError:return
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    try:
        result=Pipeline(store,gateway).run(job)
        store.owned_update(job.id,job.lease_owner,state='ready',stage='ready',result=json.dumps(result,ensure_ascii=False),lease_until=0)
    except (ModelUnavailable,NeedsInput) as exc:
        store.owned_update(job.id,job.lease_owner,state='awaiting_input',stage='awaiting_input',error=str(exc),lease_until=0)
    except Exception as exc:
        # Response bodies and credentials never enter the public error message.
        log.exception('Job %s failed',job.id)
        try:store.owned_update(job.id,job.lease_owner,state='failed',stage='failed',error=f'{type(exc).__name__}: {str(exc)[:300]}',lease_until=0)
        except RuntimeError:pass
    finally:stopped.set();thread.join(timeout=1)


def main():
    logging.basicConfig(level=logging.INFO);store=Store()
    while True:
        job=store.claim()
        if job:execute(store,job)
        else:time.sleep(1)


if __name__=='__main__':main()
