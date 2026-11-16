from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
for p in [ROOT/"ui",ROOT/"telecom",ROOT/"edu"]:sys.path.insert(0,str(p))
