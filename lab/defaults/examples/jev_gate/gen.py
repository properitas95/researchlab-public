"""Synthetic stand-in for a judge's outputs: 5 tasks x 300 items, label + judge probability (fixed seed, no API key)."""
import json, math, random
rng = random.Random(20261021)
sep, over = [0.4, 0.8, 1.5, 2.2, 3.0], [3, 2, 1, 1, 1]  # per-task judge strength and overconfidence
items = []
for t in range(5):
    for i in range(300):
        y = rng.random() < 0.5
        z = ((1 if y else -1) * sep[t] + rng.gauss(0, 1)) * over[t]
        items.append({"id": f"t{t}-{i}", "task": t, "label": int(y), "p": round(1 / (1 + math.exp(-z)), 4)})
json.dump(items, open("data.json", "w"))
