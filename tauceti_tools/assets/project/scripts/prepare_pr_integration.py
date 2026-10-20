#!/usr/bin/env python3
import os
import subprocess
import sys
raise SystemExit(subprocess.call(["tauceti-tools", "script", 'prepare_pr_integration.py', *sys.argv[1:]]))
