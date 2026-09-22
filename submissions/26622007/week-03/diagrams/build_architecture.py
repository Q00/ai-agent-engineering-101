"""Generate editable Excalidraw shapes for the implemented Week 03 harness."""
from pathlib import Path
import hashlib
import json
import subprocess
import time
import unicodedata

ROOT = Path(__file__).resolve().parent
STAMP = int(time.time() * 1000)
ELEMENTS = []
NODES = {}
INK = '#243247'
BLUE = '#4263eb'
GREEN = '#16866b'
PURPLE = '#7950b2'
ORANGE = '#bc681d'
GRAY = '#7b8796'


def base(kind, ident, x, y, w, h, **kwargs):
    seed = int(hashlib.sha256(ident.encode()).hexdigest()[:7], 16)
    item = dict(id=ident, type=kind, x=x, y=y, width=w, height=h,
                angle=0, strokeColor=INK, backgroundColor='transparent',
                fillStyle='solid', strokeWidth=1.8, strokeStyle='solid',
                roughness=0, opacity=100, groupIds=[], frameId=None,
                roundness=None, seed=seed, version=1, versionNonce=seed,
                isDeleted=False, boundElements=[], updated=STAMP,
                link=None, locked=False)
    item.update(kwargs)
    ELEMENTS.append(item)
    return item


def text(ident, x, y, value, size=21, color=INK, group=None):
    width = max(sum(1 if unicodedata.east_asian_width(c) in 'WF' else .6 for c in line)
                for line in value.splitlines()) * size
    return base('text', ident, x, y, width, len(value.splitlines()) * size * 1.35,
                text=value, originalText=value, fontSize=size, fontFamily=2,
                textAlign='left', verticalAlign='top', containerId=None,
                autoResize=True, lineHeight=1.35, strokeColor=color,
                groupIds=[group] if group else [])


def box(ident, x, y, w, h, title, body, color=BLUE, fill='#edf2ff', size=21):
    shape = base('rectangle', ident, x, y, w, h, strokeColor=color,
                 backgroundColor=fill, roundness={'type': 3}, groupIds=[ident+'-group'])
    NODES[ident] = shape
    text(ident+'-title', x+22, y+20, title, 25, color, ident+'-group')
    text(ident+'-body', x+22, y+66, body, size, INK, ident+'-group')
    return shape


def edge(ident, points, color=BLUE, dashed=False, source=None, target=None, arrow=True):
    x, y = points[0]
    p = [[px-x, py-y] for px, py in points]
    e = base('arrow' if arrow else 'line', ident, x, y,
             max(q[0] for q in p)-min(q[0] for q in p),
             max(q[1] for q in p)-min(q[1] for q in p),
             points=p, strokeColor=color, strokeStyle='dashed' if dashed else 'solid',
             strokeWidth=2.2, startArrowhead=None, endArrowhead='arrow' if arrow else None,
             startBinding=None, endBinding=None, lastCommittedPoint=None, elbowed=False)
    for field, node in [('startBinding',source),('endBinding',target)]:
        if node:
            e[field] = {'elementId':node, 'focus':0, 'gap':8}
            NODES[node]['boundElements'].append({'id':ident,'type':'arrow'})
    return e


# Overview header.
text('title',70,45,'Week 03 · Contract Net',44)
text('subtitle',72,108,'출시 준비팀의 업무 배정 하네스  /  현재 구현',27,GRAY)
box('scope',1690,48,550,115,'구현 범위: 담당자 선정까지',
    '선정 후 업무 수행·리뷰 호출은 아직 없음',ORANGE,'#fff4e6',21)
text('source-heading',72,194,'입력 데이터',19,GRAY)
text('manager-heading',492,194,'관리자 · Python 코드',19,BLUE)
text('network-heading',1422,372,'전송 계층 · 공유 클라이언트',19,GREEN)
text('judge-heading',1892,210,'검증과 선정 · Python 코드',19,BLUE)

# Configuration and task source.
box('config',70,240,330,180,'config.json',
    '모델 · 온도 · 출력 한도\n공급자 자동 라우팅 정책\n타임아웃 · 재시도 제한')
box('tasks',70,530,330,210,'tasks.json → load_tasks()',
    '합성 출시 업무 6개\n공개: Task(id, desc)\n분리: golds 정답표')
box('runner',490,240,340,180,'run.py · 실행 초기화',
    '조건 / run ID / 설정 해시\n프롬프트 구성 · 통계 초기화\n작업 목록을 순서대로 처리')
box('manager',490,530,340,235,'관리자 · 작업 공고',
    'run_round()\nTASK-ANNOUNCEMENT\n작업 설명 + 참여 조건\n응답 규칙 + 즉시 응답 요청',size=20)

# Independent contractor contexts: each is one stateless call, executed sequentially.
base('rectangle','contexts-frame',890,204,430,710,strokeColor='#b7c8ec',
     backgroundColor='#f8faff',strokeStyle='dashed',roundness={'type':3})
text('contexts-title',912,219,'독립 컨텍스트 · A → B → C 순차',21,BLUE)
box('a',930,270,350,160,'A · 제품 / 기술',
    '오류 원인 · 테스트 · 롤백\nsystem: 역할 + 공통 규칙\nuser: 이번 작업 공고',BLUE,'#edf2ff',20)
box('b',930,485,350,160,'B · 사업 / 분석',
    '손익 · 수요 · 우선순위\nsystem: 역할 + 공통 규칙\nuser: 이번 작업 공고',BLUE,'#edf2ff',20)
box('c',930,700,350,160,'C · 운영 / 커뮤니케이션',
    '고객 안내 · 일정 · 팀 조율\nsystem: 역할 + 공통 규칙\nuser: 이번 작업 공고',BLUE,'#edf2ff',20)
text('contexts-footer',912,880,'이전 대화·다른 입찰·gold 미전달',19,GRAY)

box('router',1420,440,350,330,'OpenRouterClient',
    'openrouter_client.py\nPOST /chat/completions\n\nOpenRouter → 가능한 공급자\nDeepSeek V4.1 Flash\n\ntemperature=0 · max_tokens=512\nreasoning.enabled=false',GREEN,'#e8f6f0',19)
box('parser',1890,270,350,260,'입찰 검증 · parse_bid()',
    'JSON 객체 + 정확한 3개 필드\nbid: boolean\nconfidence: 0–100\nreason: 비어 있지 않은 문자열\n\n거절 / 파싱 실패는 별도 집계',BLUE,'#edf2ff',20)
box('award',1890,670,350,230,'담당자 선정 · award',
    '유효한 bid=true 중\n최고 confidence 선택\n동점: 먼저 입찰한 A → B → C\n입찰 없음: unassigned\n선정 결과는 내부 이벤트로 기록',BLUE,'#edf2ff',20)

# Data and request flow.
edge('config-runner',[(408,325),(482,325)],source='config',target='runner')
edge('runner-manager',[(660,428),(660,522)],source='runner',target='manager')
edge('tasks-manager',[(408,610),(482,610)],source='tasks',target='manager')
text('public-task-label',415,555,'id\ndesc',18,BLUE)
edge('announce-a',[(838,565),(860,565),(860,350),(922,350)],source='manager',target='a')
edge('announce-b',[(838,635),(880,635),(880,565),(922,565)],source='manager',target='b')
edge('announce-c',[(838,710),(865,710),(865,780),(922,780)],source='manager',target='c')
edge('a-http',[(1288,350),(1370,350),(1370,510),(1412,510)],source='a',target='router',color=GREEN)
edge('b-http',[(1288,565),(1412,605)],source='b',target='router',color=GREEN)
edge('c-http',[(1288,780),(1370,780),(1370,705),(1412,705)],source='c',target='router',color=GREEN)
text('request-label',1340,821,'매 호출 2개 메시지\nsystem + user',19,GREEN)
edge('response-parser',[(1778,575),(1830,575),(1830,400),(1882,400)],source='router',target='parser',color=GREEN)
text('response-label',1785,602,'JSON 입찰\n또는 거절',19,GREEN)
edge('parsed-award',[(2065,538),(2065,662)],source='parser',target='award')
text('after-three',2092,578,'3명 응답 후',20,BLUE)
edge('next-task',[(1882,820),(1845,820),(1845,962),(660,962),(660,773)],source='award',target='manager')
text('reset-bids',890,980,'다음 작업으로 반복 · 입찰 목록 bids=[] 초기화',22,BLUE)

# Gold flows only into the evaluator, after all tasks have been allocated.
edge('private-gold',[(235,748),(235,1055),(1840,1055),(1840,1215),(1882,1215)],
     color=PURPLE,dashed=True,source='tasks',target=None)
text('gold-label',450,1078,'golds 정답표: 모델 입력·담당자 선정에 사용하지 않음',22,PURPLE)
box('evaluator',1890,1120,350,235,'평가자 · 실행 완료 후',
    'RoundResult.evaluate(golds)\n전체 6개 작업의 배정 대조\ncorrect / misawards\nunassigned / messages',PURPLE,'#f3edfc',20)
# Bind the gold path to the evaluator after the node exists.
ELEMENTS[[e['id'] for e in ELEMENTS].index('private-gold')]['endBinding'] = {'elementId':'evaluator','focus':0,'gap':8}
NODES['evaluator']['boundElements'].append({'id':'private-gold','type':'arrow'})
edge('awards-evaluator',[(2065,908),(2065,1112)],source='award',target='evaluator',color=PURPLE)
text('after-all',2090,1000,'모든 작업\n배정 완료',20,PURPLE)
box('records',1420,1120,350,235,'기록 · Recorder',
    '모든 단계의 emit() 수집\nlogs/: 원문 응답 · 입찰 · 선정\n실제 provider/model/usage\nresults.csv: 실행별 결과\n실패도 보존 · 키는 제외',GRAY,'#f1f3f5',19)
edge('evaluation-records',[(1882,1260),(1778,1260)],source='evaluator',target='records',color=GRAY)

# Compact explanatory cards; everything stays editable.
box('protocol-card',70,1430,510,270,'01  메시지 프로토콜',
    '공고: 작업 ID / 설명 / 참여 조건\n입찰: {bid, confidence, reason}\n선정: {task_id, contractor, confidence}\n\n공고·입찰은 모델과 송수신\naward는 코드 내부에서 기록',BLUE,'#f3f6ff',21)
box('memory-card',625,1430,510,270,'02  컨텍스트 수명',
    '호출마다: 역할 + 현재 작업만 전달\n작업마다: 입찰 목록 새로 생성\n실행마다: 통계·배정 결과 새로 생성\n\n파일 로그는 분석용으로만 보존\n장기 기억·이전 결과 피드백 없음',GREEN,'#edf9f3',21)
box('conditions-card',1180,1430,510,270,'03  실험 조건',
    'baseline: A/B/C 전문 역할\nhomogeneous: 모두 같은 일반 역할\noverconfident: C에 항상 ≥95 입찰 지시\n\n작업·모델·온도·라우팅 정책 고정\n조건별 최소 3회가 제출 요건',PURPLE,'#f6f0fc',20)
box('result-card',1735,1430,505,270,'04  현재 확인한 결과',
    '자동 라우팅 baseline 1회 완료\n6/6 정답 배정 · 메시지 35\n미배정 / 오배정 / JSON 실패: 0\n18회 호출 → 실제 공급자 9개\nAPI 응답 비용: $0.003295322\n추가 반복 실험·개인 해석은 남음',ORANGE,'#fff5e9',20)
sha = subprocess.check_output(['git','rev-parse','--short','HEAD'],cwd=ROOT,text=True).strip()
text('footer',72,1740,f'구현 근거: contract_net.py · run.py · openrouter_client.py · config.json  |  소스 {sha}',19,GRAY)
scene = {'type':'excalidraw','version':2,'source':'https://excalidraw.com',
         'elements':ELEMENTS,'appState':{'viewBackgroundColor':'#ffffff','gridSize':None},'files':{}}
output = ROOT/'week-03-architecture.excalidraw'
output.write_text(json.dumps(scene,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'file':str(output),'elements':len(ELEMENTS),'source_commit':sha},ensure_ascii=False))
