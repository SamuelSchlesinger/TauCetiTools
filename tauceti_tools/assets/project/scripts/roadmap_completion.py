#!/usr/bin/env python3
import os
import subprocess
import sys
raise SystemExit(subprocess.call(["tauceti-tools", "script", 'roadmap_completion.py', *sys.argv[1:]]))
