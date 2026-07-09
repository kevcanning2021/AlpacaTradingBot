#!/usr/bin/env python3
"""Headless entry point for running the read-only dashboard as a long-lived service."""

import sys
import os
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
from dashboard.app import app
from dashboard import config

logging.basicConfig(level=logging.INFO)

if __name__ == '__main__':
    if not config.DASHBOARD_PASSWORD_HASH or not config.DASHBOARD_SESSION_SECRET:
        raise SystemExit('DASHBOARD_PASSWORD_HASH and DASHBOARD_SESSION_SECRET must be set in dashboard/.env')

    uvicorn.run(app, host=config.DASHBOARD_BIND_HOST, port=config.DASHBOARD_BIND_PORT)
