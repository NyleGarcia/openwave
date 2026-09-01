"""Headless daemon that just runs the audio capture fix."""

import logging
import signal
import time
import sys

from .audio import AudioManager
from .health import HealthMonitor

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
log = logging.getLogger("openwave.daemon")


def main():
    log.info("Starting OpenWave audio daemon")

    def on_status(present, healthy, state):
        if not present:
            log.info("Device not detected")
        elif state == "silent":
            log.error(
                "Capture stream is up but every sample is zero -- the device "
                "is delivering digital silence. Power-cycle it; a USB "
                "re-enumeration does not clear this state."
            )
        elif healthy:
            log.info("Capture keepalive active")
        else:
            log.warning("Establishing capture keepalive...")

    mgr = AudioManager(on_status_change=on_status)
    mgr.start()

    # Slow watchdogs for the faults the keepalive cannot see: xrun
    # accumulation (robotic capture) and a running sink whose hardware
    # has stopped consuming (silent output).
    health = HealthMonitor()
    health.start()

    def shutdown(sig, frame):
        log.info("Shutting down")
        health.stop()
        mgr.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    # Keep main thread alive
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
