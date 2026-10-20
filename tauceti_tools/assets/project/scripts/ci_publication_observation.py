#!/usr/bin/env python3
import os
import subprocess
import sys
raise SystemExit(subprocess.call(["tauceti-tools", "script", 'ci_publication_observation.py', *sys.argv[1:]]))
