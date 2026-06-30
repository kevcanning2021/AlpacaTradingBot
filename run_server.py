#!/usr/bin/env python3
"""Headless entry point for running the scheduler as a long-lived service."""

import sys
import os
import time
import signal
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scheduler import get_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = get_scheduler()


def shutdown(signum, frame):
    logger.info("Shutdown signal received, stopping scheduler...")
    scheduler.stop()
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

scheduler.start()

while True:
    time.sleep(60)
