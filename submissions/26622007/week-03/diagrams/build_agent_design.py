"""Draw current multi-agent roles, message exchange, authority and context boundaries."""
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


# Collaboration view: central allocation authority and three independent contractors.
text('title',70,48,'현재 멀티 에이전트 설계',44)
text('subtitle',72,112,'Contract Net · 중앙 조정자 + 역할별 입찰자 3명 · 작업당 한 번의 입찰',24,GRAY)

base('rectangle','team-boundary',470,210,1320,995,strokeColor='#ced9e6',
     backgroundColor='#fafbfd',strokeStyle='dashed',roundness={'type':3})
text('team-title',497,226,'협상에 참여하는 주체와 의사결정 권한',22,GRAY)
text('same-model',1280,216,'같은 기반 모델 · 서로 다른 역할',21,GRAY)
text('model-note',1280,245,'DeepSeek V4.1 Flash / A → B → C 순차 호출',18,GRAY)

box('protocol',70,250,345,280,'주고받는 메시지',
    '① 공고\n업무 설명 · 참여 조건\n응답 규칙\n\n② 입찰 또는 거절\n참여 여부 · 확신도 · 이유',BLUE,'#f1f5ff',21)
box('task',70,640,300,190,'현재 업무',
    '모두에게 같은 공고\n\n예: 결제 중복 원인 분석',GRAY,'#f2f4f7',20)

box('manager',540,615,380,285,'조정자 · Manager',
    '규칙 기반 · 최종 배정 권한\n\n① 같은 업무를 세 명에게 공고\n② 현재 작업의 입찰을 모음\n③ 가장 높은 확신도로 선정\n\n업무와 세 입찰을 모두 볼 수 있음',BLUE,'#edf2ff',20)
text('manager-knowledge',548,546,'관리자의 정보 범위',21,BLUE)
text('manager-knowledge-body',548,577,'현재 업무 + 현재 입찰들 + 배정 기록',19,GRAY)


def specialist(ident, letter, title, domain, x, y, color, fill):
    w,h=440,240
    shape=base('rectangle',ident,x,y,w,h,strokeColor=color,backgroundColor=fill,
               roundness={'type':3},groupIds=[ident+'-group'])
    NODES[ident]=shape
    base('ellipse',ident+'-avatar',x+20,y+20,46,46,strokeColor=color,
         backgroundColor=color,groupIds=[ident+'-group'])
    text(ident+'-letter',x+33,y+27,letter,26,'#ffffff',ident+'-group')
    text(ident+'-title',x+83,y+28,title,25,color,ident+'-group')
    text(ident+'-expertise',x+23,y+85,domain,20,INK,ident+'-group')
    base('rectangle',ident+'-context',x+20,y+127,w-40,90,strokeColor=color,
         backgroundColor='#ffffff',strokeStyle='dashed',strokeWidth=1,
         roundness={'type':3},groupIds=[ident+'-group'])
    text(ident+'-private',x+35,y+139,'자신만의 컨텍스트',18,color,ident+'-group')
    text(ident+'-context-text',x+35,y+169,'자기 역할 + 공통 규칙 + 현재 업무',19,INK,ident+'-group')


specialist('agent-a','A','제품·기술 에이전트','오류 원인 · 테스트 · 배포 위험',1300,292,BLUE,'#edf2ff')
specialist('agent-b','B','사업·분석 에이전트','손익 · 수요 · 사업 우선순위',1300,612,GREEN,'#eaf7f0')
specialist('agent-c','C','운영·소통 에이전트','고객 안내 · 일정 · 팀 간 조율',1300,932,ORANGE,'#fff3e5')

# Two separate directions express calls for proposals and independent bids.
edge('task-in',[(378,720),(532,720)],color=GRAY,source='task',target='manager')
edge('call-a',[(928,646),(1292,375)],source='manager',target='agent-a',color=BLUE)
edge('bid-a',[(1292,460),(928,710)],source='agent-a',target='manager',color=GREEN)
text('call-a-label',960,445,'① 같은 업무 공고',20,BLUE)
text('bid-a-label',1080,630,'② 입찰 / 거절',20,GREEN)
edge('call-b',[(928,745),(1292,682)],source='manager',target='agent-b',color=BLUE)
edge('bid-b',[(1292,774),(928,804)],source='agent-b',target='manager',color=GREEN)
text('call-b-label',1000,670,'① 같은 업무 공고',20,BLUE)
text('bid-b-label',1078,814,'② 입찰 / 거절',20,GREEN)
edge('call-c',[(928,843),(1292,1007)],source='manager',target='agent-c',color=BLUE)
edge('bid-c',[(1292,1103),(928,883)],source='agent-c',target='manager',color=GREEN)
text('call-c-label',1044,845,'① 같은 업무 공고',20,BLUE)
text('bid-c-label',1082,1060,'② 입찰 / 거절',20,GREEN)

box('decision',550,1005,370,170,'③ 담당자 결정',
    'A / B / C 중 한 명 또는 미배정\n관리자 내부에 기록하고 라운드 종료',BLUE,'#edf2ff',20)
edge('manager-decision',[(730,908),(730,997)],source='manager',target='decision')
text('decision-label',758,944,'세 명의 응답을 확인한 뒤',19,BLUE)
edge('next-work',[(542,1090),(442,1090),(442,795),(378,795)],
     source='decision',target='task',color=BLUE,dashed=True)
text('next-work-label',220,958,'다음 업무\n작업 기억 초기화',21,BLUE)
text('no-peer-exchange',1310,1181,'각자 판단하며, 응답은 관리자에게만 전달',18,GRAY)

box('scope',70,1120,345,170,'협상의 결과',
    '이번 업무의 담당자를 선정\n선정 후 실제 업무 수행은 미구현',ORANGE,'#fff5e9',20)

# Information and memory boundaries are first-class properties of the team design.
box('memory',70,1360,540,260,'정보와 기억의 경계',
    '공유 정보: 현재 업무 공고\n개별 정보: 자기 역할과 현재 업무\n관리자 기억: 현재 입찰 + 실행별 배정 기록\n\n업무 전환 시 입찰·대화 초기화\n과거 결과·다른 에이전트의 입찰은 미전달',GREEN,'#eef8f2',20)
box('authority',650,1360,560,260,'자율성과 선정 규칙',
    '에이전트: 참여 여부·확신도·이유를 자율 판단\n관리자: 최고 확신도로 담당자 한 명 결정\n확신도는 검증되지 않은 자기 평가\n\n동점은 A → B → C 순서로 선택\n유효한 입찰이 없으면 미배정',BLUE,'#f2f5ff',20)
box('observer',1250,1360,540,260,'협상 외부 · 평가자',
    '실행 종료 후 배정 결과와 사전 정답을 대조\n정답표는 협상 참여자에게 공개하지 않음\n배정이 정답 담당자와 일치하는지 평가\n\n평가 결과는 다음 입찰에 피드백하지 않음\n참여자 간 협상이나 선정에 개입하지 않음',PURPLE,'#f4eefb',20)
NODES['observer']['strokeStyle']='dashed'
edge('observe-decisions',[(735,1183),(735,1298),(1510,1298),(1510,1352)],
     color=PURPLE,dashed=True,source='decision',target='observer')
text('observe-label',922,1251,'전체 업무 배정 후 · 결과 관찰',21,PURPLE)

text('footer',72,1666,'현재 협업 방식: 전문 역할별 독립 입찰 → 중앙 선정 → 다음 업무에서 새 컨텍스트',21,GRAY)
scene={'type':'excalidraw','version':2,'source':'https://excalidraw.com',
       'elements':ELEMENTS,'appState':{'viewBackgroundColor':'#ffffff','gridSize':None},'files':{}}
output=ROOT/'multi-agent-design.excalidraw'
output.write_text(json.dumps(scene,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'file':str(output),'elements':len(ELEMENTS),'textElements':sum(e['type']=='text' for e in ELEMENTS)},ensure_ascii=False))
