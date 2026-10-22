#!/usr/bin/env python3
"""ResearchLab launcher without install; the CLI and its command list live in lab/cli.py."""
import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab.cli import main
main()
