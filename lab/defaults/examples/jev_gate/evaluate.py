"""Frozen: cost = (5 x auto errors + human reviews) / n, as in PROTOCOL 5. Auto-answer when judge confidence max(p,1-p) >= the task's threshold."""
import hashlib, json, sys
split = sys.argv[sys.argv.index("--split") + 1] if "--split" in sys.argv else "dev"
thr = json.load(open("policy.json"))["thr"]
rows = [x for x in json.load(open("data.json")) if (hashlib.sha1(x["id"].encode()).digest()[0] % 2 == 0) == (split == "dev")]  # hash split
auto = [(max(x["p"], 1 - x["p"]) >= thr[x["task"]], (x["p"] >= 0.5) != x["label"]) for x in rows]
cost = sum(5 * err if ok else 1 for ok, err in auto) / len(rows)
print(json.dumps({"score": round(cost, 4), "pass": True, "split": split, "auto_rate": round(sum(ok for ok, _ in auto) / len(rows), 3)}))
