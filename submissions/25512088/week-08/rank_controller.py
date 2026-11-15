"""Official-board rank velocity and irreversible leader-defense controller."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from adaptive_control import Control


KST=ZoneInfo('Asia/Seoul')
MILESTONES=(
    (10,datetime(2026,11,10,23,59,tzinfo=KST).timestamp()),
    (5,datetime(2026,11,13,23,59,tzinfo=KST).timestamp()),
    (3,datetime(2026,11,15,23,59,tzinfo=KST).timestamp()),
    (1,datetime(2026,11,16,23,59,tzinfo=KST).timestamp()),
)


def _score(row):
    # Class Player ranks students by the board's combined student score.  The
    # public Round 3 board currently exposes it as ``total``.  Keep the older
    # names so archived snapshots and deterministic replays remain readable.
    value=row.get('total',row.get('student_total',row.get('all')))
    return float(value) if isinstance(value,(int,float)) and math.isfinite(value) else None


def _ours(row):
    owner=row.get('owner') or {}
    nested=(row.get('shop') or {}).get('name')=='25512088' or (row.get('buyer') or {}).get('name')=='25512088'
    player=str(row.get('player',row.get('name','')))
    return player in ('25512088','morethanwhatiwant') or nested or owner.get('login')=='morethanwhatiwant'


class RankController:
    def __init__(self,state='.market',control=None):
        self.control=control or Control(state);self.owns_control=control is None
        self.control.db.executescript('''
        CREATE TABLE IF NOT EXISTS rank_observations(
          id INTEGER PRIMARY KEY, observed REAL, round INTEGER, our_score REAL,
          our_rank INTEGER, top_score REAL, second_score REAL, cutoffs TEXT,
          board_hash TEXT);
        CREATE TABLE IF NOT EXISTS rank_controller_state(
          key TEXT PRIMARY KEY, value TEXT);
        ''')

    def _get(self,key,default=None):
        row=self.control.db.execute('SELECT value FROM rank_controller_state WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _put(self,key,value):
        with self.control.db:
            self.control.db.execute('INSERT OR REPLACE INTO rank_controller_state VALUES(?,?)',
                (key,json.dumps(value,ensure_ascii=False,sort_keys=True)))

    def mode(self):return self._get('mode','ATTACK')

    def rate(self,seconds,now=None,minimum_coverage=.8,field='our_score'):
        now=time.time() if now is None else now;start=now-seconds
        rows=self.control.db.execute(
            f'SELECT observed,{field} AS score FROM rank_observations WHERE observed>=? AND observed<=? '
            'AND '+field+' IS NOT NULL ORDER BY observed',(start,now)).fetchall()
        if len(rows)<2 or rows[-1]['observed']-rows[0]['observed']<seconds*minimum_coverage:return None
        hours=(rows[-1]['observed']-rows[0]['observed'])/3600
        return (rows[-1]['score']-rows[0]['score'])/hours

    def cutoff_rate(self,target,seconds,now=None,minimum_coverage=.8):
        now=time.time() if now is None else now;start=now-seconds
        rows=self.control.db.execute(
            'SELECT observed,cutoffs FROM rank_observations WHERE observed>=? AND observed<=? ORDER BY observed',
            (start,now)).fetchall()
        points=[(row['observed'],json.loads(row['cutoffs']).get(str(target))) for row in rows]
        points=[point for point in points if point[1] is not None]
        if len(points)<2 or points[-1][0]-points[0][0]<seconds*minimum_coverage:return None
        return (points[-1][1]-points[0][1])/((points[-1][0]-points[0][0])/3600)

    def _milestone(self,rank,now):
        for target,deadline in MILESTONES:
            if rank>target or now<deadline:return target,deadline
        return 1,MILESTONES[-1][1]

    def observe(self,board,observed_at=None,board_hash=None):
        now=time.time() if observed_at is None else observed_at
        if board.get('round')!=3 or not isinstance(board.get('players'),list):
            raise ValueError('round3_board_required')
        players=[row for row in board['players']
            if not row.get('house') and row.get('student') is not False and _score(row) is not None]
        players.sort(key=lambda row:_score(row),reverse=True)
        own=[(index,row) for index,row in enumerate(players,1) if _ours(row)]
        if len(own)!=1:raise ValueError('unique_player_identity_required')
        rank,row=own[0];our_score=_score(row)
        scores=[_score(r) for r in players]
        cutoffs={str(k):scores[min(k,len(scores))-1] for k in (1,3,5,10) if scores}
        with self.control.db:
            self.control.db.execute('INSERT INTO rank_observations VALUES(?,?,?,?,?,?,?,?,?)',(
                None,now,3,our_score,rank,scores[0],scores[1] if len(scores)>1 else None,
                json.dumps(cutoffs,sort_keys=True),board_hash))
        solo_first=rank==1 and (len(scores)==1 or our_score>scores[1])
        streak=self._get('leader_streak',[]) if solo_first else []
        streak=(streak+[now])[-3:] if solo_first else []
        self._put('leader_streak',streak)
        if self.mode()=='ATTACK' and len(streak)==3 and streak[-1]-streak[0]>=60:
            self._put('mode','DEFEND');self._put('leader_baseline_observed',now)
            checkpoint=self.control.checkpoint('rank_controller',{'mode':'DEFEND'},
                {'our_score':our_score,'rank':rank,'leader_baseline':True})
            self.control.event('rank_controller','leader_defense_locked',{
                'checkpoint_id':checkpoint,'our_score':our_score})
        target,deadline=self._milestone(rank,now);cutoff=cutoffs.get(str(target),scores[-1])
        gap=max(0.0,cutoff-our_score+1e-12)
        rate30=self.rate(1800,now);rate2h=self.rate(7200,now)
        previous=self.control.db.execute(
            'SELECT observed,our_score FROM rank_observations WHERE observed<=? ORDER BY observed DESC LIMIT 1',
            (now-1800,)).fetchone()
        cutoff30=self.cutoff_rate(target,1800,now)
        closing=rate30-cutoff30 if rate30 is not None and cutoff30 is not None else None
        eta=now+gap/closing*3600 if closing is not None and closing>0 else math.inf
        lead=our_score-scores[1] if len(scores)>1 else math.inf
        threat=self.mode()=='DEFEND' and (rank!=1 or lead<.02)
        stalled=bool(previous and gap>0 and our_score<=previous['our_score'])
        result={'mode':self.mode(),'rank':rank,'our_score':our_score,'target_rank':target,
            'deadline':deadline,'gap':gap,'rate_30m':rate30,'rate_2h':rate2h,
            'cutoff_rate_30m':cutoff30,'closing_rate':closing,'eta':eta,
            'behind_schedule':eta>deadline,'stalled':stalled,'defend_threat':threat}
        self.control.event('rank_controller','rank_snapshot',result)
        return result

    def close(self):
        if self.owns_control:self.control.close()
