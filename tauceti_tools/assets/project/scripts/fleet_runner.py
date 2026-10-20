#!/usr/bin/env python3
import os
import subprocess
import sys
raise SystemExit(subprocess.call(["tauceti-tools", "script", 'fleet_runner.py', *sys.argv[1:]]))
