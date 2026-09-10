"""Reproducible escalation-model gate. Never publishes a model that fails baselines.

Input: a provenance-bearing Parquet feature table; labels must be matured outcomes.
No synthetic fallback and no deserialization of unexplained pickle artifacts.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
import numpy as np
import pandas as pd
from lightgbm import Booster, LGBMClassifier
from sklearn.metrics import average_precision_score, brier_score_loss, recall_score

WEATHER=['temperature_2m','relative_humidity_2m','wind_speed_10m','precipitation']
BASE_FEATURES=['detection_count','mean_frp','max_frp','persistence',*WEATHER]
HISTORY=['history_scan_count','history_max_frp','history_mean_frp','history_age_hours','neighbor_max_frp','hour_sin','hour_cos']
FEATURES=[*BASE_FEATURES,*HISTORY]

def split(frame, cutoff, holdout):
    required=set(FEATURES+['feature_time','label_end','region','target','provenance'])
    if not required.issubset(frame.columns):
        raise ValueError('Missing required columns: '+', '.join(sorted(required-set(frame.columns))))
    frame=frame.copy()
    frame['feature_time']=pd.to_datetime(frame.feature_time, utc=True)
    frame['label_end']=pd.to_datetime(frame.label_end, utc=True)
    if not frame.provenance.eq('observed').all() or not frame.target.isin([0,1]).all():
        raise ValueError('Only observed provenance and binary matured targets are accepted')
    values = frame[FEATURES].apply(pd.to_numeric, errors='raise').to_numpy(dtype=float)
    if not np.isfinite(values).all() or not frame.persistence.between(0, 1).all():
        raise ValueError('Features must be finite; persistence must be in [0,1]')
    if not (frame.label_end>frame.feature_time).all() or (frame.label_end>pd.Timestamp.now(tz='UTC')).any():
        raise ValueError('Invalid or unmatured label window')
    boundary=pd.Timestamp(cutoff)
    boundary=boundary.tz_localize('UTC') if boundary.tzinfo is None else boundary.tz_convert('UTC')
    train=frame[(frame.label_end<boundary)&(frame.region!=holdout)]
    temporal=frame[(frame.feature_time>=boundary)&(frame.region!=holdout)]
    spatial=frame[(frame.feature_time>=boundary)&(frame.region==holdout)]
    for name,part in [('train',train),('temporal',temporal),('spatial',spatial)]:
        if len(part)<20 or part.target.nunique()!=2:
            raise ValueError(f'{name} requires at least 20 rows and both outcomes')
    if train.label_end.max()>=min(temporal.feature_time.min(),spatial.feature_time.min()):
        raise ValueError('Temporal leakage')
    return train,temporal,spatial

def train_model(frame, cutoff, holdout):
    train,temporal,spatial=split(frame,cutoff,holdout)
    tuning = {}
    inner_cutoff = train.feature_time.quantile(.75)
    inner_train = train[train.label_end < inner_cutoff]
    inner_valid = train[train.feature_time >= inner_cutoff]
    def fit(columns):
        candidates = []
        parameters = {'n_estimators':75, 'num_leaves':3}
        common = dict(learning_rate=.04,random_state=42,n_jobs=1,verbosity=-1,deterministic=True,force_col_wise=True)
        if min(len(inner_train), len(inner_valid)) >= 20 and inner_train.target.nunique() == inner_valid.target.nunique() == 2:
            for leaves in (3, 7):
                for trees in (25, 75, 150):
                    candidate = LGBMClassifier(n_estimators=trees, num_leaves=leaves, **common)
                    candidate.fit(inner_train[columns], inner_train.target)
                    score = float(average_precision_score(inner_valid.target, candidate.predict_proba(inner_valid[columns])[:,1]))
                    candidates.append({'pr_auc':score, 'n_estimators':trees, 'num_leaves':leaves})
            best = max(candidates, key=lambda row: (row['pr_auc'], -row['n_estimators'], -row['num_leaves']))
            parameters = {key:best[key] for key in parameters}
        tuning['weather' if columns == WEATHER else 'final'] = {'selected':parameters, 'training_only_validation':candidates, 'inner_cutoff':inner_cutoff.isoformat()}
        model=LGBMClassifier(**parameters, **common)
        model.fit(train[columns],train.target)
        return model
    model,weather=fit(FEATURES),fit(WEATHER)
    metrics={}
    for name,part in [('temporal',temporal),('spatial',spatial)]:
        scores=model.predict_proba(part[FEATURES])[:,1]
        persistence=pd.to_numeric(part.persistence,errors='raise').to_numpy()
        if not np.isfinite(persistence).all() or ((persistence<0)|(persistence>1)).any():
            raise ValueError('Persistence must be a probability in [0,1]')
        metrics[name]={'rows':len(part),'pr_auc':float(average_precision_score(part.target,scores)), 'persistence_pr_auc':float(average_precision_score(part.target,persistence)), 'weather_pr_auc':float(average_precision_score(part.target,weather.predict_proba(part[WEATHER])[:,1])), 'brier':float(brier_score_loss(part.target,scores)), 'recall_at_0_5':float(recall_score(part.target,scores>=.5))}
        metrics[name]['positives'] = int(part.target.sum())
        metrics[name]['calibration'] = []
        for low in np.arange(0, 1, .2):
            selected = (scores >= low) & (scores < low+.2 if low < .8 else scores <= 1)
            if selected.any():
                metrics[name]['calibration'].append({'lower':float(low), 'rows':int(selected.sum()), 'mean_probability':float(scores[selected].mean()), 'observed_rate':float(part.target.to_numpy()[selected].mean())})
    passed=all(m['pr_auc']>max(m['persistence_pr_auc'],m['weather_pr_auc']) for m in metrics.values())
    return model,{'release_passed':passed,'metrics':metrics,'features':FEATURES,'holdout_region':holdout,'cutoff':cutoff,'training_rows':len(train),'tuning':tuning,'limitations':'Offline held-out evaluation is not a guarantee of future performance. Input provenance must be independently audited.'}

def load_validated_model(directory):
    """Fail closed on failed evaluations, stale artifacts, or changed feature order."""
    directory = Path(directory)
    report = json.loads((directory / 'evaluation.json').read_text(encoding='utf-8'))
    if report.get('release_passed') is not True:
        raise ValueError('Model release not approved')
    model_bytes = (directory / 'model.txt').read_bytes()
    if hashlib.sha256(model_bytes).hexdigest() != report.get('model_sha256'):
        raise ValueError('Model checksum mismatch')
    if report.get('features') != FEATURES or report.get('data_audit_approved') is not True:
        raise ValueError('Model features or data audit not approved')
    for name in ('temporal', 'spatial'):
        metrics = report.get('metrics', {}).get(name, {})
        scores = [metrics.get(key) for key in ('pr_auc', 'persistence_pr_auc', 'weather_pr_auc')]
        if any(not isinstance(s, (float, int)) or not np.isfinite(s) or not 0 <= s <= 1 for s in scores) or scores[0] <= max(scores[1:]):
            raise ValueError('Model evaluation not approved')
    model = Booster(model_str=model_bytes.decode('utf-8'))
    if model.feature_name() != FEATURES:
        raise ValueError('Model feature order mismatch')
    return model, report


def predict_validated(directory, features):
    model, report = load_validated_model(directory)
    frame = pd.DataFrame([features]).reindex(columns=FEATURES)
    values = frame.apply(pd.to_numeric, errors='raise').to_numpy(dtype=float)
    if not np.isfinite(values).all() or not 0 <= values[0, 3] <= 1:
        raise ValueError('Missing or invalid inference features')
    score = float(model.predict(frame)[0])
    if not np.isfinite(score) or not 0 <= score <= 1:
        raise ValueError('Invalid model output')
    return {'probability': score, 'model_sha256': report['model_sha256'],
            'features_sha256': hashlib.sha256(json.dumps(values.tolist(), separators=(',', ':')).encode()).hexdigest(),
            'limitations': report.get('limitations', 'Research indicator, not emergency guidance.')}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--features',required=True,type=Path)
    parser.add_argument('--cutoff',required=True)
    parser.add_argument('--holdout-region',required=True)
    parser.add_argument('--audit',required=True,type=Path,help='Independent feature audit bound to the input SHA-256')
    parser.add_argument('--output',type=Path,default=Path('lake/models/validated'))
    args=parser.parse_args()
    input_hash=hashlib.sha256(args.features.read_bytes()).hexdigest()
    audit=json.loads(args.audit.read_text(encoding='utf-8'))
    if audit.get('training_approved') is not True or audit.get('input_sha256') != input_hash:
        raise SystemExit('Training rejected: missing approval or feature audit checksum mismatch')
    model,report=train_model(pd.read_parquet(args.features),args.cutoff,args.holdout_region)
    report['input_sha256']=input_hash
    report['data_audit_approved']=True
    report['audit_sha256']=hashlib.sha256(args.audit.read_bytes()).hexdigest()
    report['trained_at']=datetime.now(timezone.utc).isoformat()
    report['packages']={name:version(name) for name in ('lightgbm','scikit-learn','pandas','numpy')}
    report['target_definition']=audit.get('definition')
    report['approval_scope']=audit.get('approval_scope')
    report['limitations']=' '.join(audit.get('limitations', [])) or report['limitations']
    report['feature_importance_gain']=dict(zip(FEATURES, model.booster_.feature_importance(importance_type='gain').tolist()))
    args.output.mkdir(parents=True,exist_ok=True)
    if report['release_passed']:
        model_bytes=model.booster_.model_to_string().encode('utf-8')
        report['model_sha256']=hashlib.sha256(model_bytes).hexdigest()
        (args.output/'model.txt.tmp').write_bytes(model_bytes)
        os.replace(args.output/'model.txt.tmp',args.output/'model.txt')
    (args.output/'evaluation.json.tmp').write_text(json.dumps(report,indent=2),encoding='utf-8')
    os.replace(args.output/'evaluation.json.tmp',args.output/'evaluation.json')
    if not report['release_passed']:
        raise SystemExit('Release rejected: final model did not beat both baselines on both holdouts; no model published')
    print(json.dumps(report,indent=2))

if __name__=='__main__':
    main()
