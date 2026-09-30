"""Within-model changes for an added unrelated restriction on the same action."""
from collections import defaultdict
from mupyjava.judge_samples import digest
from mupyjava.judge import _laya_judgment


def restriction_metrics(predictions):
    groups=defaultdict(dict)
    for row in predictions:
        key=row['tags']['restriction_pair'];role=row['tags']['role']
        if role in groups[key]:raise ValueError('Repeated condition')
        groups[key][role]=row
    records=[]
    for key,conditions in sorted(groups.items()):
        if set(conditions)!={'plain','restricted','decline','unknown'}:raise ValueError('Incomplete condition group')
        if len({digest({k:r['state'][k] for k in ['tool','arguments']}) for r in conditions.values()})!=1:raise ValueError('Action changed')
        for role,row in conditions.items():
            expected=None if role=='unknown' else role!='decline'
            if row['label'] is not expected or _laya_judgment(row['probability']).answer is not row['answer']:raise ValueError('Label or decision mismatch')
        plain,limited=conditions['plain'],conditions['restricted']
        if not limited['state']['user_request'].startswith(plain['state']['user_request']+' '):raise ValueError('Not a restriction-only request change')
        records.append({'pair':key,'tool':plain['state']['tool'],'plain_probability':plain['probability'],
          'restricted_probability':limited['probability'],'delta':limited['probability']-plain['probability'],
          'plain_answer':plain['answer'],'restricted_answer':limited['answer'],
          'lost_allow':plain['answer'] is True and limited['answer'] is not True,
          'new_wrong_decline':plain['answer'] is True and limited['answer'] is False,
          'recovered_allow':plain['answer'] is not True and limited['answer'] is True})
    return {'pairs':len(records),'lost_allow':sum(r['lost_allow'] for r in records),
       'new_wrong_decline':sum(r['new_wrong_decline'] for r in records),'recovered_allow':sum(r['recovered_allow'] for r in records),
       'mean_probability_delta':sum(r['delta'] for r in records)/len(records) if records else None,'records':records}
