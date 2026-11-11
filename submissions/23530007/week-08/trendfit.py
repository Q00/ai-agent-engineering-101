"""A straight line through a short history, with how sure it is (yms979 11-11).

The book lead over the runner-up moves +-3 M from one 15-min reading to the next (240.3, 246.5, 241.5, 243.3, 244.4,
240.6, 239.0, 242.7 M over 8.4 h on 11-11), so a slope taken from two readings says 'closing at .58 M/h' over 6.6 h
and 'widening at .29 M/h' over 8.4 h of the same evening, and carried 138 h to the round's end that is -80 M or +40 M.
linfit() fits all the readings (least squares) and returns the slope with its standard error; callers treat a slope
smaller than 2 standard errors as 'flat'. With 24 readings over 6 h and that noise the error is about .25 M/h
(+-33 M at the end); over 2 h it is 1.2 M/h (+-170 M), which is why nothing should be decided on 2 h of book.
Used by guards.py (G9), price_ctl.py and ops/brief.py.
"""


def linfit(pts):
    """pts = [(t in seconds, y)]. Returns {slope (y per hour), se (its standard error; None under 3 points),
    n, span_h, lo, hi, flat (|slope| < 2 se)} or None with fewer than 2 points or no time span."""
    pts = sorted((float(t), float(y)) for t, y in pts if y is not None)
    n = len(pts)
    if n < 2 or pts[-1][0] - pts[0][0] <= 0:
        return None
    xs = [(t - pts[0][0]) / 3600 for t, _ in pts]
    ys = [y for _, y in pts]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    se = None
    if n >= 3:
        res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
        se = (res / (n - 2) / sxx) ** 0.5
    return {"slope": slope, "se": se, "n": n, "span_h": xs[-1], "lo": min(ys), "hi": max(ys),
            "flat": se is not None and abs(slope) < 2 * se}
