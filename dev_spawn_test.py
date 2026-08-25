import sys, time
sys.path.insert(0, "D:/开发/配置AI应用/lingmengwork")
from lingmengwork.config import load_config
from lingmengwork.tools import mcp as m

cfg = load_config("D:/dev_lmw_e2e_config.toml")
try:
    m.get_manager().connect_all(cfg)
    print("STATUS:", m.get_manager().status(), flush=True)
    open("D:/spawn_result.txt", "w").write(str(m.get_manager().status()))
except Exception as e:
    print("ERR:", type(e).__name__, e, flush=True)
    open("D:/spawn_result.txt", "w").write("ERR: %s %s" % (type(e).__name__, e))
