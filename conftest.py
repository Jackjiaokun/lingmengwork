import os
import sys

# 让 pytest 无论从哪个 cwd 运行都能 import lingmengwork 包
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
