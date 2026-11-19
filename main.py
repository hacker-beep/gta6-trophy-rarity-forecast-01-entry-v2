"""Offline count-and-population forecasts for step-refreshed trophy pages."""

import datetime as dt
import json
import math
import os
from pathlib import Path
import statistics

LEVELS = (0.05, 0.25, 0.5, 0.75, 0.95)
NORMAL = statistics.NormalDist()


def hour(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp() / 3600.0


def odds(rate):
    p = max(0.01, min(99.99, float(rate))) / 100.0
    return math.log(p) - math.log1p(-p)


def percent(value):
    return 100.0 / (1.0 + math.exp(-max(-35.0, min(35.0, value))))


def limit(value, low, high):
    return max(low, min(high, value))


def fit(points):
    """Small weighted sufficient-statistic regression; no iterative fitting."""
    if len(points) < 2:
        return 0.0, 0.0
    total = sum(w for x, y, w in points)
    mx = sum(x * w for x, y, w in points) / total
    my = sum(y * w for x, y, w in points) / total
    xx = sum(w * (x - mx) ** 2 for x, y, w in points)
    slope = sum(w * (x - mx) * (y - my) for x, y, w in points) / max(xx, 1e-12)
    residual = math.sqrt(sum(w * (y - my - slope * (x - mx)) ** 2
                             for x, y, w in points) / total)
    return slope, residual


def snapshots(case):
    result = []
    for row in case['history']:
        t = hour(row.get('slot', row['at']))
        n = max(1.0, float(row.get('players_tracked') or 1))
        rates = row['rates']
        if not result or n != result[-1][1] or rates != result[-1][2]:
            result.append((t, n, rates))
    return result


def attributes(trophy):
    """Progression-time prior, used only when observations are scarce."""
    description = trophy.get('description', '').lower()
    tier = trophy.get('tier', 'bronze')
    delay = {'platinum': 100.0, 'gold': 70.0, 'silver': 48.0}.get(tier, 28.0)
    if 'chapter 1' in description:
        delay = 10.0
    elif 'chapter 2' in description:
        delay = 22.0
    elif 'chapter 3' in description:
        delay = 35.0
    elif 'chapter 4' in description:
        delay = 48.0
    elif 'final mission' in description:
        delay = 55.0
    if any(word in description for word in ('all ', 'every ', '100%', '250 ', '100 locations')):
        delay *= 1.5
    return delay


def mixed_quantiles(location, scale, unchanged, refresh_probability):
    """Invert a normal refresh component plus a point mass for an unchanged page."""
    if refresh_probability <= 0.0:
        return [round(percent(unchanged), 7)] * len(LEVELS)
    mass = 1.0 - refresh_probability
    values = []
    for tau in LEVELS:
        low, high = -30.0, 30.0
        for _ in range(46):
            mid = (low + high) / 2.0
            cdf = refresh_probability * NORMAL.cdf((mid - location) / max(scale, 1e-7))
            if mid >= unchanged:
                cdf += mass
            if cdf < tau:
                low = mid
            else:
                high = mid
        values.append(round(percent((low + high) / 2.0), 7))
    return values


def forecast(case, spread_factor=0.65):
    states = snapshots(case)
    if not states:
        raise ValueError('At least one historical read is required')
    release = hour(case['game']['release_date'][:10] + 'T00:00:00Z')
    now = hour(case['at'])
    target = hour(case['target']) if case.get('target') else now + case['horizon_minutes'] / 60.0
    end, population, _ = states[-1]
    ahead = max(0.0, target - end)
    age = max(1.0, end - release)
    gaps = [b[0] - a[0] for a, b in zip(states, states[1:]) if b[0] > a[0]]
    # Public launch-night evidence supplies a prior, never a per-case lookup.
    if not gaps:
        elapsed = max(0.0, target - states[0][0])
        refresh = limit((elapsed - 4.0) / 1.25, 0.0, 1.0)
        player_growth, growth_uncertainty = 1.8, 0.45
    else:
        period = max(0.25, statistics.median(gaps))
        refresh = limit(ahead / period, 0.0, 1.0)
        points = [(math.log1p(max(0.0, t - release)), math.log(n),
                   math.exp(-(end - t) / 24.0)) for t, n, rates in states]
        exponent, pop_noise = fit(points)
        exponent = limit(exponent, 0.0, 2.5)
        player_growth = exponent * math.log1p(ahead / (age + 1.0))
        growth_uncertainty = pop_noise + 0.12 * math.sqrt(ahead / 24.0)
    future_population = population * math.exp(min(8.0, player_growth))
    rows = []
    for index, trophy in enumerate(case['trophies']):
        observed = [(t, n, rates[index]) for t, n, rates in states
                    if index < len(rates) and rates[index] is not None]
        if not observed:
            observed = [(end, population, 0.01)]
        tlast, nlast, rate = observed[-1]
        last_logit = odds(rate)
        if rate >= 99.99:
            # An effectively universal introduction remains universal for newcomers.
            rows.append({'id': trophy['id'], 'q': [100.0] * 5})
            continue
        count = max(0.0, rate) * nlast / 100.0
        trophy_ahead = max(0.0, target - tlast)
        numer = denom = 0.0
        innovations = []
        causal_errors = []
        for left, right in zip(observed, observed[1:]):
            t0, n0, r0 = left
            t1, n1, r1 = right
            elapsed = t1 - t0
            if elapsed <= 0.0:
                continue
            c0, c1 = n0 * r0 / 100.0, n1 * r1 / 100.0
            exposure = max(1.0, (n0 + n1 - c0 - c1) / 2.0) * elapsed
            weight = math.exp(-(end - t1) / 24.0)
            if denom > 0.0:
                prior_hazard = numer / denom
                implied = n1 - (n1 - c0) * math.exp(-min(30.0, prior_hazard * elapsed))
                causal_errors.append(odds(r1) - odds(100.0 * implied / n1))
            numer += weight * max(0.0, c1 - c0)
            denom += weight * exposure
            # Count innovations isolate progress from denominator jumps.
            innovations.append(math.log((c1 + 0.5) / (c0 + 0.5)))
        hazard = numer / denom if denom else 0.0
        if len(observed) == 1:
            # Existing completions are a launch cohort, not evidence that every
            # newly tracked player immediately completes a rare trophy.
            growth = trophy_ahead / attributes(trophy) * 0.12
            predicted = 100.0 * count * (1.0 + growth) / future_population
            center = odds(predicted)
            spread = 0.35 + growth_uncertainty
        else:
            # Integrate dC/dt = hazard * (players - C), with positive population
            # growth. The fixed eight steps keep long horizons inexpensive.
            step = trophy_ahead / 8.0
            total_growth = math.log(max(1.0, future_population / nlast))
            projected = count
            for j in range(8):
                fraction = (j + 0.5) / 8.0
                n = nlast * math.exp(total_growth * fraction)
                projected = n - (n - projected) * math.exp(-min(30.0, hazard * step))
            center = odds(100.0 * projected / future_population)
            volatility = statistics.pstdev(innovations) if len(innovations) > 1 else 0.0
            error_scale = (math.sqrt(statistics.mean(e * e for e in causal_errors))
                           if causal_errors else 0.0)
            directional = 0.35 * abs(center - last_logit) * math.sqrt(ahead / (ahead + 24.0))
            spread = math.sqrt(growth_uncertainty ** 2 +
                               (volatility / math.sqrt(max(1, len(innovations)))) ** 2 +
                               error_scale ** 2 * (1.0 + ahead / 24.0) +
                               directional ** 2 + (0.10 * math.sqrt(ahead / 24.0)) ** 2)
        if len(observed) > 1:
            spread *= spread_factor
        rows.append({'id': trophy['id'], 'q': mixed_quantiles(
            center, max(0.025, spread), last_logit, refresh)})
    return {'trophies': sorted(rows, key=lambda row: row['id'])}


def run():
    destination = Path(os.getenv('MAKEFUN_OUTPUT_DIR', '/output'))
    destination.mkdir(parents=True, exist_ok=True)
    source = Path(os.getenv('MAKEFUN_INPUT_DIR', '/input'))
    for file in sorted(source.glob('*.in'), key=lambda p: p.name):
        case = json.loads(file.read_text(encoding='utf-8'))
        data = json.dumps(forecast(case), allow_nan=False, separators=(',', ':')) + '\n'
        (destination / (str(case['id']) + '.out')).write_text(data, encoding='utf-8')


if __name__ == '__main__':
    run()
