#!/usr/bin/env python3
import os
import subprocess
import sys
raise SystemExit(subprocess.call(["tauceti-tools", "script", 'lake_cache_publish.py', *sys.argv[1:]]))
