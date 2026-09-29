"""Authorization contrast metrics for already frozen serving predictions."""
from collections import defaultdict
import math
from mupyjava.judge_samples import digest


def pair_metrics(predictions):
    pairs=defaultdict(list)
    for row in predictions:
        identity=row.get('tags',{}).get('contrast_pair')
        if identity:pairs[identity].append(row)
    records=[]
    for identity,rows in sorted(pairs.items()):
        if (len(rows)!=2 or {row['label'] for row in rows}!={True,False}
            or len({digest({k:row['state'][k] for k in ['tool','arguments']}) for row in rows})!=1):
            raise ValueError('Invalid frozen contrast pair')
        allow=next(row for row in rows if row['label'] is True)
        decline=next(row for row in rows if row['label'] is False)
        a,d=allow['probability'],decline['probability']
        if any(type(p) not in (int,float) or not math.isfinite(p) or not 0<=p<=1 for p in [a,d]):
            raise ValueError('Invalid probability')
        records.append({'pair':identity,'allow_probability':a,'decline_probability':d,
                        'correct_order':a>d,'both_correct_decisive':a>=.8 and d<=.2,
                        'both_wrong_decisive':a<=.2 and d>=.8,'margin':a-d})
    return {'pairs':len(records),'correct_order':sum(r['correct_order'] for r in records),
            'both_correct_decisive':sum(r['both_correct_decisive'] for r in records),
            'both_wrong_decisive':sum(r['both_wrong_decisive'] for r in records),
            'mean_margin':sum(r['margin'] for r in records)/len(records) if records else None,'records':records}
