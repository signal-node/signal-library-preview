#!/usr/bin/env python3
"""Signal Libraryの共通統合実装への互換入口。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"src"))
from signal_library.reading.assembly import *

if __name__=="__main__":main()
