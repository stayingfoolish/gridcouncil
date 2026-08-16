
import json, pickle, sys, traceback
sys.path.insert(0, '/Users/prajwalsreenivas/Documents/GitHub/gridcouncil')
from engine.aps_dispatch import simulate_dispatch

code_path, env_path = sys.argv[1], sys.argv[2]
env = pickle.load(open(env_path, "rb"))
source = open(code_path).read()
try:
    ns = {}
    exec(compile(source, "policy.py", "exec"), ns)
    cls = ns.get("DispatchPolicy")
    if cls is None:
        raise NameError("generated code does not define class 'DispatchPolicy'")
    out = simulate_dispatch(cls(), env)
except Exception:
    print(json.dumps({"ok": False, "error": traceback.format_exc(limit=6)[-2000:]}))
    sys.exit(0)
print(json.dumps({
    "ok": True, "system_cost_delta": out.system_cost_delta,
    "energy_cost": out.energy_cost, "peak_price_delta": out.peak_price_delta,
    "forced_mwh": out.forced_mwh, "battery_util_pct": out.battery_util_pct,
    "served_mw": out.served_mw, "battery_mw": out.battery_mw,
    "backlog_mwh": out.backlog_mwh,
}))
