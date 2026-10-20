#!/usr/bin/env python3
import os
import subprocess
import sys
raise SystemExit(subprocess.call(["tauceti-tools", "script", 'cancel_bors_candidate.py', *sys.argv[1:]]))
