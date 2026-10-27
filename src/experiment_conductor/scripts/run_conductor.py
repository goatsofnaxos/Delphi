#!/usr/bin/env python
"""Entry point for the experiment conductor.

Usage
-----
    uv run scripts/run_conductor.py [options]
    uv run scripts/run_conductor.py --data-root /path/to/data --subject-id 12345

All options can also be set in a .env file.  CLI flags override .env values.
Run with --help to see all options.
"""
from experiment_conductor.conductor import main

if __name__ == "__main__":
    main()
