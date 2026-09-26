"""Independent small model generator for incidence/operator regression.

No detector imports; symbols and permutations returned for ground-truth checks.
"""

import random


def model(
    kernel,
    groups=3,
    *,
    seed=0,
    permute=False,
    scaled=False,
    perturb=None,
    missing_cell=None,
    duplicate=False,
    extra=False,
):
    rng = random.Random(seed)
    n = len(kernel[0])
    outputs = len(kernel)
    names = [f"v{q}" for q in range(n * groups + outputs)]
    if permute:
        rng.shuffle(names)
    X = {
        (j, k): names[j * groups + k]
        for j in range(n)
        for k in range(groups)
        if (j, k) != missing_cell
    }
    y = {p: names[n * groups + p] for p in range(outputs)}
    rows = []
    tags = {}

    def add(tag, terms, sense, rhs):
        scale = rng.choice([-3, 2, 5]) if scaled else 1
        if scale < 0:
            sense = {">=": "<=", "<=": ">=", "=": "="}[sense]
        terms = list(terms)
        if permute:
            rng.shuffle(terms)
        text = " ".join(
            f"{'+' if scale * c >= 0 else '-'}{abs(scale * c)} {v}"
            for v, c in terms
            if c
        )
        rows.append((tag, text, sense, rhs * scale))

    for j in range(n):
        add(("left", j), [(v, 1) for (a, k), v in X.items() if a == j], "=", 1)
    for k in range(groups):
        add(("right", k), [(v, 1) for (j, b), v in X.items() if b == k], "=", 2 + k)
    for p in range(outputs):
        for k in range(groups):
            ts = [(y[p], 1)] + [
                (X[j, k], -a) for j, a in enumerate(kernel[p]) if (j, k) in X and a
            ]
            if perturb == (p, k):
                ts.append((X[0, k], -1))
            if extra and (p, k) == (0, 0):
                ts.append((X[0, (k + 1) % groups], 1))
            add(("op", p, k), ts, ">=", -7)
    if duplicate:
        rows.append((("duplicate",), *rows[-1][1:]))
    if permute:
        rng.shuffle(rows)
    lines = [
        "Minimize",
        " obj: " + " ".join("+1 " + v for v in y.values()),
        "Subject To",
    ]
    for i, (tag, expr, sense, rhs) in enumerate(rows):
        tags[tag] = i
        lines.append(f" r{i}: {expr} {sense} {rhs}")
    lines += ["Bounds"] + [f" 0 <= {v} <= 20" for v in y.values()]
    lines += ["Binaries", " " + " ".join(X.values()), "End"]
    return "\n".join(lines) + "\n", {"X": X, "y": y, "rows": tags, "kernel": kernel}
