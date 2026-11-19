"""Reproducible validation, causal backtests, and explicitly synthetic stress tests."""

import argparse
import copy
import datetime as dt
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('entry', ROOT / 'main.py')
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
TAUS = (0.05, 0.25, 0.5, 0.75, 0.95)
ZS = (-1.645, -0.674, 0.0, 0.674, 1.645)
EPOCH = entry.hour('2026-11-19T00:00:00Z')


def iso(hours):
    return dt.datetime.fromtimestamp(hours * 3600, dt.timezone.utc).isoformat().replace('+00:00', 'Z')


def validate(output, case):
    assert set(output) == {'trophies'}
    rows = output['trophies']
    assert len(rows) == len(case['trophies'])
    assert sorted(r['id'] for r in rows) == sorted(t['id'] for t in case['trophies'])
    for row in rows:
        assert set(row) == {'id', 'q'}
        q = row['q']
        assert len(q) == 5 and q == sorted(q)
        assert all(type(v) in (float, int) and math.isfinite(v) and 0 <= v <= 100 for v in q)
    assert len(json.dumps(output).encode()) <= 65536


def baseline(case, curve=False):
    now, future = entry.hour(case['at']), entry.hour(case['target'])
    output = []
    for i, trophy in enumerate(case['trophies']):
        points = [(entry.hour(h.get('slot', h['at'])), entry.odds(h['rates'][i]))
                  for h in case['history'] if i < len(h['rates']) and h['rates'][i] is not None]
        center = points[-1][1] if points else entry.odds(0.0)
        spread = 0.5 * math.sqrt(max(0, case['horizon_minutes']) / 1440.0)
        window = [(math.log(max(1e-6, 1.0 + t - EPOCH)), y) for t, y in points if t >= now - 24]
        if curve and len(window) >= 3:
            mx, my = (statistics.mean(p[j] for p in window) for j in (0, 1))
            xx = sum((x - mx) ** 2 for x, y in window)
            slope = sum((x - mx) * (y - my) for x, y in window) / max(1e-12, xx)
            center = my + slope * (math.log(max(1e-6, 1.0 + future - EPOCH)) - mx)
            residual = sum((y - my - slope * (x - mx)) ** 2 for x, y in window) / max(1, len(window) - 2)
            spread = math.sqrt(spread * spread + residual)
        q = [math.floor(entry.percent(center + z * spread) * 10000 + 0.5) / 10000 for z in ZS]
        output.append({'id': trophy['id'], 'q': q})
    return {'trophies': output}


def score(output, truth, trophies):
    predictions = {r['id']: r['q'] for r in output['trophies']}
    losses, misses, covers = [], [], []
    for trophy, actual in zip(trophies, truth):
        if actual is None:
            continue
        q = predictions[trophy['id']]
        for tau, prediction in zip(TAUS, q):
            error = entry.odds(actual) - entry.odds(prediction)
            losses.append(2 * max(tau * error, (tau - 1) * error))
        misses.append(abs(actual - q[2]))
        covers.append(q[0] <= actual <= q[4])
    return {'loss': statistics.mean(losses), 'miss_pp': statistics.mean(misses),
            'coverage90': statistics.mean(covers)}


def compare(case, truth, spread_factor=0.65):
    methods = {'model': entry.forecast(case, spread_factor), 'persistence': baseline(case), 'curve': baseline(case, True)}
    for out in methods.values():
        validate(out, case)
    return {name: score(out, truth, case['trophies']) for name, out in methods.items()}


def average(results):
    return {name: {metric: statistics.mean(row[name][metric] for row in results)
                   for metric in results[0][name]} for name in results[0]} if results else {}


def real_splits(cases):
    # Deduplicate the shared history; p00 and p01 are not independent datasets.
    base = max(cases, key=lambda c: len(c['history']))
    history = base['history']
    scores, detail = [], []
    for cutoff in range(2, len(history)):
        for steps in (1, 2, 3):
            target_index = cutoff + steps - 1
            if target_index >= len(history):
                continue
            case = copy.deepcopy(base)
            case['history'] = history[:cutoff]
            case['at'] = iso(entry.hour(history[cutoff - 1]['slot']) + 1 / 60)
            case['target'] = history[target_index]['slot']
            case['horizon_minutes'] = (entry.hour(case['target']) - entry.hour(case['at'])) * 60
            result = compare(case, history[target_index]['rates'])
            scores.append(result)
            detail.append({'prefix_reads': cutoff, 'target_index': target_index,
                           'hours': case['horizon_minutes'] / 60, 'scores': result})
    return {'splits': len(scores), 'aggregate': average(scores), 'detail': detail}


def synthetic_history(template, scenario, length=168):
    """Independent cohort simulator: lognormal completion delays, batched syncs.

    Not real GTA data or an estimate of a competition score. Counts are formed
    by summing each joining cohort's completion CDF, rather than the model ODE.
    """
    trophies = template['trophies']
    history = []
    cohorts = []
    previous = 0.0
    cumulative = []
    for h in range(length + 1):
        if scenario == 'power':
            players = 600 + 2400 * (h + 1) ** 0.83
        elif scenario == 'surge':
            players = 600 + 3000 * (h + 1) ** 0.55 + 16000 / (1 + math.exp(-(h - 20) / 3))
        else:
            players = 600 + 25000 * (1 - math.exp(-(h + 1) / 22))
        cohorts.append(max(0, players - previous))
        previous = players
        cumulative.append(players)
        if h % (4 if scenario != 'surge' else 6) != 0:
            continue
        rates = []
        for i, trophy in enumerate(trophies):
            if i == 1:
                rates.append(100.0)
                continue
            # Different tiers, independent broad delay/participation distribution.
            factor = {'platinum': 8.0, 'gold': 4.0, 'silver': 2.3}.get(trophy['tier'], 1.0)
            median = (12 + ((i * 17) % 55)) * factor
            participation = 0.15 + 0.7 * ((i * 13) % 31) / 30
            if trophy['tier'] == 'platinum':
                participation = 0.08
            sigma = 0.65 + (i % 4) * 0.15
            earned = 4.0
            for joined, size in enumerate(cohorts):
                age = max(0.01, h - joined + 0.5)
                probability = statistics.NormalDist().cdf(math.log(age / median) / sigma)
                earned += size * participation * probability
            rates.append(round(min(99.99, 100 * earned / players), 2))
        history.append({'slot': iso(EPOCH + h), 'at': iso(EPOCH + h + 0.0001),
                        'players_tracked': round(players), 'trophies_earned': 0, 'rates': rates})
    return history


def synthetic_splits(template, spread_factor=0.65):
    details = []
    for scenario in ('power', 'surge', 'saturating'):
        history = synthetic_history(template, scenario)
        for cutoff in (24, 36, 48):
            for ahead in (4, 12, 24, 72, 108):
                target = cutoff + ahead
                truth = min(history, key=lambda h: abs(entry.hour(h['slot']) - EPOCH - target))
                case = copy.deepcopy(template)
                case['history'] = [h for h in history if entry.hour(h['slot']) < EPOCH + cutoff]
                case['at'], case['target'] = iso(EPOCH + cutoff), truth['slot']
                case['horizon_minutes'] = (entry.hour(case['target']) - EPOCH - cutoff) * 60
                result = compare(case, truth['rates'], spread_factor)
                details.append({'scenario': scenario, 'cutoff_h': cutoff, 'horizon_h': ahead, 'scores': result})
    return {'splits': len(details), 'aggregate': average([r['scores'] for r in details]),
            'by_scenario': {name: average([r['scores'] for r in details if r['scenario'] == name])
                            for name in ('power', 'surge', 'saturating')}, 'detail': details}


def run_program(case, folder):
    incoming, outgoing = folder / 'input', folder / 'output'
    incoming.mkdir(parents=True, exist_ok=True)
    outgoing.mkdir(parents=True, exist_ok=True)
    (incoming / (case['id'] + '.in')).write_text(json.dumps(case))
    env = dict(os.environ, MAKEFUN_INPUT_DIR=str(incoming), MAKEFUN_OUTPUT_DIR=str(outgoing), PYTHONHASHSEED='0')
    measurements, outputs = [], []
    for attempt in range(2):
        start = time.perf_counter()
        subprocess.run([sys.executable, '-P', '-s', str(ROOT / 'main.py')], env=env, check=True, timeout=8)
        measurements.append(time.perf_counter() - start)
        payload = (outgoing / (case['id'] + '.out')).read_bytes()
        assert len(payload) <= 65536
        validate(json.loads(payload), case)
        outputs.append(payload)
    assert outputs[0] == outputs[1], 'Non-deterministic output'
    return {'seconds': measurements, 'bytes': len(outputs[0]), 'rows': len(case['trophies']), 'identical': True}


def edge_checks(template, artifacts):
    cases = []
    nulls = copy.deepcopy(template)
    nulls['id'] = 'nulls'
    for j, row in enumerate(nulls['history']):
        row['rates'] = [None if i % 7 == 0 or (j == 0 and i % 5 == 0) else r
                        for i, r in enumerate(row['rates'])]
    cases.append(nulls)
    extreme = copy.deepcopy(template)
    extreme['id'] = 'extremes'
    extreme['horizon_minutes'] = 0
    extreme['target'] = extreme['at']
    for row in extreme['history']:
        row['rates'] = [0, 0.01, 99.99, 100, None] * 11
    cases.append(extreme)
    dense = copy.deepcopy(template)
    dense['id'] = 'dense_5days'
    coarse = synthetic_history(template, 'surge', 120)
    dense['history'] = []
    for step in range(721):
        hours = step / 6
        row = copy.deepcopy(coarse[min(len(coarse) - 1, int(hours // 6))])
        row['at'] = row['slot'] = iso(EPOCH + hours)
        dense['history'].append(row)
    dense['at'], dense['target'] = iso(EPOCH + 120.1), iso(EPOCH + 228.6)
    dense['horizon_minutes'] = 6510
    cases.append(dense)
    return {c['id']: run_program(c, artifacts / c['id']) for c in cases}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--kit', type=Path, required=True)
    parser.add_argument('--report', type=Path, default=ROOT / 'artifacts' / 'report.json')
    parser.add_argument('--answers', type=Path, help='Optional released JSON mapping case id to actual rates')
    parser.add_argument('--calibrate', action='store_true', help='Reproduce synthetic width calibration')
    args = parser.parse_args()
    cases = [json.loads(p.read_text()) for p in sorted((args.kit / 'input').glob('*.in'))]
    artifacts = args.report.parent
    artifacts.mkdir(parents=True, exist_ok=True)
    report = {'python': sys.version, 'real_history': real_splits(cases),
              'synthetic': synthetic_splits(cases[0]),
              'practice_execution': {c['id']: run_program(c, artifacts / c['id']) for c in cases},
              'edge_execution': edge_checks(cases[0], artifacts),
              'practice_scores': None,
              'practice_score_note': 'Targets sealed: kit contains no reference/answer.json. No practice loss fabricated.'}
    if args.calibrate:
        calibration = {}
        for scale in (0.5, 0.65, 0.8, 1.0, 1.15):
            rows = synthetic_splits(cases[0], scale)['detail']
            calibration[str(scale)] = {
                'calibration_24_36h': average([r['scores'] for r in rows if r['cutoff_h'] < 48]),
                'later_48h': average([r['scores'] for r in rows if r['cutoff_h'] == 48])}
        report['synthetic_width_calibration'] = calibration
    if args.answers:
        # Accept a simple mapping for reproducible scoring of subsequently released truth.
        truth = json.loads(args.answers.read_text())
        report['practice_scores'] = {c['id']: compare(c, truth[c['id']],) for c in cases}
        report['practice_score_note'] = 'Scored against the supplied id -> rates mapping.'
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    summary = {k: ({x: y for x, y in v.items() if x != 'detail'} if isinstance(v, dict) else v)
               for k, v in report.items()}
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
