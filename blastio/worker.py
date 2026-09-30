"""Run one worker on the same persistent local disk as the API."""
import logging
import signal
import time
from .config import settings
from .db import init_db, transaction, get_setting, set_setting, now
from .engine import dispatch_one, recover_stale

log=logging.getLogger('blastio.worker')
running=True

def stop(*args):
    global running
    running=False

def main():
    init_db()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    last_probe=0
    while running:
        try:
            with transaction() as conn:
                recover_stale(conn)
                set_setting(conn,'worker_heartbeat',now())
                connected=conn.execute("SELECT 1 FROM credentials WHERE environment='production'").fetchone() is not None
            if settings.mode=='production' and connected and time.monotonic()-last_probe>300:
                from .health import probe_webhooks
                probe_webhooks();last_probe=time.monotonic()
            processed=dispatch_one()
            if not processed:time.sleep(1)
        except Exception as exc:
            # Exception text may include secrets. Only emit its type.
            log.error('Worker stopped dispatch after %s',type(exc).__name__)
            try:
                with transaction() as conn:
                    set_setting(conn,'global_pause',True);set_setting(conn,'pause_reason','Essential worker safeguard failed; operator review required')
            except Exception:pass
            time.sleep(2)

if __name__=='__main__':main()
