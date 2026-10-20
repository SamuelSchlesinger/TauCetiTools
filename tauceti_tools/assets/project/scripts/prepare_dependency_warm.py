#!/usr/bin/env python3
import os
import subprocess
import sys
raise SystemExit(subprocess.call(["tauceti-tools", "script", 'prepare_dependency_warm.py', *sys.argv[1:]]))
