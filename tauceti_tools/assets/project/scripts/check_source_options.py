#!/usr/bin/env python3
import os
import subprocess
import sys
raise SystemExit(subprocess.call(["tauceti-tools", "script", 'check_source_options.py', *sys.argv[1:]]))
