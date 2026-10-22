"""Fixed evaluator (frozen): mean squared error of shrunk group means on synthetic data. Standard library only."""
import json, random, statistics, sys
from candidate import K

split = sys.argv[sys.argv.index("--split") + 1] if "--split" in sys.argv else "dev"
rng = random.Random(7 if split == "dev" else 11)  # dev and holdout are different draws of the same task
errors = []
for _ in range(200):
    truth = [rng.gauss(0, 1) for _ in range(20)]
    means = [t + rng.gauss(0, 1.5) / 2 for t in truth]  # four noisy observations per group
    grand = statistics.fmean(means)
    errors += [((1 - K) * m + K * grand - t) ** 2 for m, t in zip(means, truth)]
print(json.dumps({"score": round(statistics.fmean(errors), 5), "pass": True, "split": split}))
