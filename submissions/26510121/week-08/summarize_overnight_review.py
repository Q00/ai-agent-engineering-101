"""Present inspected overnight results and a standard chart from frozen sources."""
from datetime import datetime
import json
from r3.state import ROOT,read_json,atomic_json
from review_progress_20261110 import KST,OLD,NEW

SOURCE='logs/overnight-progress-20261110T000148504111Z.json'
DETAIL='logs/overnight-details-20261110T090100515484.json'


def main():
    data=read_json(ROOT/SOURCE);detail=read_json(ROOT/DETAIL)
    for name,cohort in data['buyer_cohorts'].items():
        print(name,json.dumps({k:{f:r.get(f) for f in ('orders','paid','conversion','median_seconds','six_hour_eligible','public_unit_breaks','public_full_refunds','final_saved_mean_from_unit_feed')} for k,r in cohort['kinds'].items()}))
    print('NEW_ENTRANTS',json.dumps({role:[r for r in rs if r['new_participant']] for role,rs in data['peers'].items()}))
    print('FEED',data['feed_watermark_relative_to_cutoff_seconds'])
    sources=[read_json(ROOT/p) for p in (OLD,NEW)]
    rows=[]
    for source in sources:
        board=source['responses']['/api/board']['data']
        rows.append({'time':datetime.fromtimestamp(source['responses']['/api/board']['finished_at'],KST).isoformat(),
                     **{label:next(r['all'] for r in board[role] if r['name']=='seochanit') for role,label in [('shops','Shop ALL'),('buyers','Buyer ALL')]}})
    mid=read_json(ROOT/'logs/autonomous-verified-20261109T130427431736Z.json')
    board=mid['public'][2]['data']
    rows.insert(1,{'time':datetime.fromisoformat(mid['local']['checked_at']).astimezone(KST).isoformat(), 'Shop ALL':board['shops'][0]['all'],'Buyer ALL':board['buyers'][0]['all']})
    config={'id':'overnight-progress-20261110','title':'Book 성장에도 Shop ALL은 하락',
        'description':'공식 보드 3개 관측. 시간은 KST, ALL 축은 0.78–0.86으로 확대. Buyer의 Saved 상승과 ALL 상승 폭은 다릅니다.',
        'height':260,'spec':{'type':'line','x':'time','y':['Shop ALL','Buyer ALL'],'yLabel':'ALL 점수',
        'yDomain':[.78,.86],'startAtZero':False,'showValues':True,'showLegend':True,
        'colors':{'Shop ALL':'#167D9A','Buyer ALL':'#B55D22'},'valueFormat':{'style':'decimal','maximumFractionDigits':3}},'rows':rows}
    atomic_json(ROOT/'logs/progress-chart-20261110-0858-kst.json',config)


if __name__=='__main__':main()
