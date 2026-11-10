"""Explicit synthetic endpoint for tests; never bundled in the application."""
import os
os.environ.setdefault('TRACER_SERVER_URL', 'https://tracer.test.invalid')
