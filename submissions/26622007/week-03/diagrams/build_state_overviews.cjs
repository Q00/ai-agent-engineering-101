/* Two levels of abstraction: peer protocol, then task scheduling.
 * No runtime behavior is changed. SVG source is editable and rendered locally.
 */
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');
const ROOT = __dirname;
const C = { ink:'#172C43', muted:'#6B7B8F', line:'#8DA0B5', paper:'#F7F9FC',
  a:'#417CBE', ap:'#EAF2FC', b:'#298B74', bp:'#E7F5EF', c:'#8663B5', cp:'#F1EBFA',
  rule:'#253A51', ready:'#EAF2FC', waiting:'#EEF1F5', complete:'#E7F5EF', fail:'#B65555', failp:'#FCEEEE' };
const esc = v => String(v).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
const t = (x,y,s,size=22,color=C.ink,weight=500,anchor='middle') => `<text x="${x}" y="${y}" text-anchor="${anchor}" font-size="${size}" fill="${color}" font-weight="${weight}">${esc(s)}</text>`;
const rect = (x,y,w,h,fill='white',stroke='#D5DFEA',r=18,extra='') => `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${r}" fill="${fill}" stroke="${stroke}" stroke-width="2" ${extra}/>`;
const line = (d,color=C.line,dash=false,arrow=true,width=3) => `<path d="${d}" fill="none" stroke="${color}" stroke-width="${width}" stroke-linecap="round" stroke-linejoin="round" ${dash?'stroke-dasharray="7 7"':''} ${arrow?`marker-end="url(#${color===C.fail?'red':color===C.b?'green':color===C.a?'blue':'arrow'})"`:''}/>`;
const card = (x,y,w,h,title,fill='white',stroke='#D5DFEA',color=C.ink,size=24) => `<g data-node="${esc(title)}">${rect(x,y,w,h,fill,stroke,14)}${t(x+w/2,y+h/2+size*.34,title,size,color,650)}</g>`;
function peer(x,y,id,r=21){ const col=C[id.toLowerCase()]; return `<circle cx="${x}" cy="${y}" r="${r}" fill="${col}"/>${t(x,y+r*.36,id,r*1.05,'white',700)}`; }
function check(x,y,color=C.b,size=12){return line(`M${x-size*.6} ${y} l${size*.45} ${size*.45} l${size*.85} -${size*.95}`,color,false,false,3);}
function lock(x,y,color=C.muted){ return `<path d="M${x-7} ${y} v-6 a7 7 0 0 1 14 0 v6" fill="none" stroke="${color}" stroke-width="2.6"/>${rect(x-11,y,22,18,'white',color,4)}`; }
function artifact(x,y,color=C.b){return `<path d="M${x} ${y} h48 l20 20 v72 h-68 z" fill="white" stroke="${color}" stroke-width="2.5"/><path d="M${x+48} ${y} v20 h20" fill="none" stroke="${color}" stroke-width="2.5"/>${line(`M${x+15} ${y+43} h36 M${x+15} ${y+56} h29 M${x+15} ${y+69} h33`,color,false,false,2.5)}`;}
function header(title,number){return t(54,55,`WEEK 03   /   ${number}`,16,C.muted,700,'start')+t(54,109,title,35,C.ink,750,'start');}
function frame(title,content,height){
  return `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="${height}" viewBox="0 0 1600 ${height}" role="img" aria-label="${esc(title)}"><style>text{font-family:'Apple SD Gothic Neo','Noto Sans CJK KR',sans-serif}</style><defs>${[['arrow',C.line],['green',C.b],['red',C.fail],['blue',C.a]].filter(([id])=>content.includes(`url(#${id})`)).map(([id,c])=>`<marker id="${id}" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0 0 L8 4 L0 8" fill="${c}"/></marker>`).join('')}</defs><rect width="1600" height="${height}" fill="${C.paper}"/>${content}</svg>`;
}
function proposal(x,y,id,kind){
  let p='';
  if(kind===0)p=line(`M${x+64} ${y+72} h79`,C[id.toLowerCase()],false,true,2);
  if(kind===1)p=line(`M${x+64} ${y+72} h24 v-12 h44 M${x+88} ${y+72} v12 h44`,C[id.toLowerCase()],false,true,2);
  if(kind===2)p=line(`M${x+64} ${y+72} h26 v-12 h24 v12 h30 M${x+90} ${y+72} v12 h24 v-12`,C[id.toLowerCase()],false,true,2);
  return rect(x,y,206,112,'white',C[id.toLowerCase()],16)+peer(x+39,y+34,id)+t(x+120,y+40,'계획 제안',23,C.ink,650)+p;
}
function recursiveTask(x,y,title){
  return rect(x,y,188,170,'white','#B8CADC',16)+t(x+94,y+30,title,21,C.ink,650)+
    peer(x+56,y+61,'A',14)+peer(x+94,y+61,'B',14)+peer(x+132,y+61,'C',14)+
    line(`M${x+94} ${y+79} V${y+95}`,C.line,false,true,2)+
    card(x+24,y+96,68,29,'검토','white','#A7B8CC',C.ink,17)+
    card(x+96,y+96,68,29,'선정',C.rule,C.rule,'white',17)+
    line(`M${x+94} ${y+127} V${y+141}`,C.line,false,true,2)+
    t(x+94,y+160,'수행 / 다시 분담',17,C.ink,600);
}
const selection = frame('동료가 계획하고, 같은 규칙으로 맡긴다',[
  header('동료가 계획하고, 같은 규칙으로 맡긴다','01'),
  t(161,163,'A · B · C',23,C.ink,700),t(161,190,'요청자 포함',17,C.muted),
  t(161,655,'계획 · 이유 · 확신도',20,C.muted),
  proposal(58,201,'A',0),proposal(58,361,'B',1),proposal(58,521,'C',2),
  line('M264 257 H311 V418',C.line,false,false),line('M264 417 H311',C.line,false,false),line('M264 577 H311 V418',C.line,false,false),
  rect(345,227,244,400,'#EBF0F6','#D5DFEA',22),
  line('M311 418 H333 V305 H367'),
  card(367,276,200,58,'형식 · 계획 검사',C.rule,C.rule,'white',22),
  line('M467 335 V365'),
  rect(367,368,200,82,'white','#A7B8CC',14),
  t(467,394,'요청자 심사',21,C.ink,650),
  t(467,418,'충족 · 실행 · 검증 (0~2)',16,C.muted),
  t(467,440,'자기 계획 포함',15,C.muted),
  line('M467 451 V472'),
  rect(367,476,200,126,C.rule,C.rule,14),
  t(467,500,'모든 항목 1점 이상',18,'white',600),
  t(467,533,'① 심사 합계',20,'white',650),
  t(467,559,'② 확신도',20,'white',650),
  t(467,585,'③ 고정 순서',20,'white',650),
  rect(650,181,749,553,'white','#B6C9DD',26),
  t(680,223,'선정된 동료',24,C.ink,700,'start'),
  line('M568 540 H616 V376 H704',C.line,false,false),
  line('M704 376 V307 H789'),
  line('M704 376 V416 H902'),
  `<path d="M704 365 l11 11 l-11 11 l-11 -11 z" fill="white" stroke="${C.line}" stroke-width="2.5"/>`,
  card(791,271,345,72,'직접 수행',C.ap,'#A9C4E2'),
  card(905,383,172,62,'작업 분담',C.ap,'#A9C4E2'),
  line('M990 446 V462 H851 V478'),line('M990 462 H1066 V478'),
  recursiveTask(757,481,'하위 작업 1'),recursiveTask(972,481,'하위 작업 2'),
  line('M851 652 V681 H1182',C.line,false,false),
  line('M1066 652 V681',C.line,false,false),
  `<rect x="1181" y="654" width="7" height="54" rx="3" fill="${C.rule}"/>`,
  line('M1188 681 H1204 V608 H1218'),
  card(1219,573,141,70,'결과 통합',C.ap,'#A9C4E2',C.ink,23),
  line('M1137 307 H1380 V433 H1430'),
  line('M1361 608 H1380 V433',C.line,false,false),
  rect(1412,408,30,52,C.rule,C.rule,6),check(1427,434,'white',12),
  t(1427,390,'형식 검사',16,C.muted),
  artifact(1471,389),t(1505,520,'결과 제출',22,C.ink,650),
  t(467,716,'■',18,C.rule),t(490,716,'실행 코드',18,C.muted,500,'start'),
].join(''),778);

// The same task module is literally drawn inside itself. Blue descends into calls;
// green ascends from child results. Adjacent collapsed modules are leaf siblings.
function collapsedLeaf(x,y){
  return `<g data-collapsed-task="leaf">${rect(x,y,125,170,'white','#AFC5DC',17)}
    ${t(x+62.5,y+30,'작업 처리',19,C.ink,700)}
    ${peer(x+29,y+61,'A',13)}${peer(x+62.5,y+61,'B',13)}${peer(x+96,y+61,'C',13)}
    ${t(x+62.5,y+94,'…',24,C.muted)}
    ${t(x+62.5,y+124,'직접 수행',19,C.ink,650)}
    ${check(x+62.5,y+148,C.b,13)}</g>`;
}
function nestedTask(x,y,w,h,depth){
  const leaf=depth===2;
  const row=y+85, mid=row+41, resultX=x+w-215, returnX=x+w-20;
  let body=rect(x,y,w,h,depth%2?'#F4F8FC':'white','#AFC5DC',23)+
    t(x+30,y+41,'작업 처리',27,C.ink,700,'start')+
    card(x+w-117,y+17,91,33,`깊이 ${depth}`,C.ap,C.ap,C.a,17)+
    rect(x+30,row,155,82,'white','#AFC5DC',14)+
    peer(x+65,row+28,'A',16)+peer(x+108,row+28,'B',16)+peer(x+151,row+28,'C',16)+
    t(x+107.5,row+67,'계획 제안',20,C.ink,650)+
    t(x+107.5,row+108,'요청자 포함',16,C.muted)+
    line(`M${x+186} ${mid} H${x+232}`,C.a)+
    card(x+235,row+10,88,62,'심사','white','#AFC5DC',C.ink,22)+
    card(x+327,row+10,88,62,'선정',C.rule,C.rule,'white',22)+
    t(x+279,row+98,'요청자',16,C.muted)+t(x+371,row+98,'코드',16,C.muted)+
    line(`M${x+416} ${mid} H${x+507}`,C.a)+
    card(x+510,row+10,140,62,leaf?'직접 수행':'분담',leaf?C.bp:C.ap,leaf?'#9ACAB9':'#AFC5DC',C.ink,23)+
    card(resultX,row,150,82,leaf?'결과':'결과 통합',C.bp,'#9ACAB9',C.b,23);
  if(leaf){
    body+=line(`M${x+651} ${mid} H${resultX-3}`,C.b);
  }else{
    const nextY=y+265, siblingX=x+40, siblingCenter=x+102.5, nextMid=nextY+126;
    body+=nestedTask(x+195,nextY,w-240,h-320,depth+1)+collapsedLeaf(siblingX,nextY);
    // Delegate to an independent leaf and one expanded recursive child.
    body+=line(`M${x+580} ${row+74} V${y+210} H${siblingCenter} V${nextY-3}`,C.a)+
      line(`M${x+180} ${y+210} V${nextMid} H${x+222}`,C.a)+
      `<circle cx="${x+180}" cy="${y+210}" r="4" fill="${C.a}"/>`+
      // Results return around the enclosing module, never to another sibling's input.
      line(`M${siblingCenter} ${nextY+171} V${y+h-22} H${returnX} V${row+116} H${resultX+75} V${row+85}`,C.b)+
      line(`M${x+w-109} ${nextMid} H${returnX}`,C.b,false,false)+
      `<circle cx="${returnX}" cy="${nextMid}" r="4" fill="${C.b}"/>`+
      t(resultX+75,row-15,'모두 완료 후',17,C.b,600);
  }
  return `<g data-recursion-depth="${depth}">${body}</g>`;
}
const agent=frame('재귀 위임 · 같은 구조의 반복',[
  header('재귀 위임 · 같은 구조의 반복','01'),
  line('M1192 97 h44',C.a),t(1252,104,'위임',18,C.a,650,'start'),
  line('M1336 97 h44',C.b),t(1396,104,'결과 복귀',18,C.b,650,'start'),
  nestedTask(55,155,1490,870,0),
].join(''),1060);

function status(x,y,label,fill,color,w=100){ return `<g data-node="${esc(label)}">${rect(x,y,w,32,fill,fill,16)}${t(x+w/2,y+22,label,17,color,700)}</g>`; }
function task(x,y,w,title,who,state){
  const colors={ '진행':[C.ap,C.a], '완료':[C.bp,C.b], '대기':[C.waiting,C.muted], '실패':[C.failp,C.fail], '차단':[C.failp,C.fail] };
  const [fill,col]=colors[state];
  return rect(x,y,w,111,'white',state==='진행'?C.a:'#CDD8E5',18)+
    t(x+22,y+37,title,24,C.ink,650,'start')+(who?peer(x+35,y+77,who,16):t(x+22,y+83,'담당 미정',18,C.muted,500,'start'))+
    status(x+w-114,y+61,state,fill,col,92);
}
const collaboration = frame('독립 작업은 나란히, 다음 작업은 결과 뒤에',[
  header('독립 작업은 나란히, 다음 작업은 결과 뒤에','02'),
  rect(54,153,1492,517,'white','#B7C7D9',24),
  t(82,195,'실행 코드가 보관하는 작업 상태',25,C.ink,700,'start'),
  // The parent remains running while child solves and prerequisite waits are managed below it.
  rect(81,220,1438,419,'#FAFCFE','#D8E2ED',18),
  peer(115,253,'C',18),t(150,261,'상위 작업',23,C.ink,650,'start'),
  status(280,237,'진행',C.ap,C.a,89),
  t(1393,262,'하위 결과 대기',19,C.muted,500),
  line('M167 455 H258 V362 H348'),line('M258 455 V541 H348'),
  card(102,419,122,72,'분담',C.cp,'#BDA9D7'),
  task(351,307,288,'하위 작업 1','B','완료'),
  task(351,486,288,'하위 작업 2','C','진행'),
  t(495,460,'병렬',20,C.muted,650),
  line('M640 363 H731',C.b),artifact(756,317,C.b),
  line('M640 542 H742',C.line,true),
  rect(756,496,68,92,'none','#C7D1DC',1,'stroke-dasharray="6 6"'),
  t(790,550,'…',29,C.muted),
  t(790,615,'완료 결과',18,C.muted),
  line('M825 363 H923 V430 H982',C.b),
  line('M825 542 H923 V475 H982',C.line,true),
  rect(985,404,86,101,C.rule,C.rule,12),
  check(1010,432,'#91D7BB',12),lock(1039,463,'#C0CDDB'),
  t(1028,388,'모두 완료',19,C.ink,650),
  line('M1072 455 H1147',C.line,true),
  task(1150,399,325,'의존 작업',null,'대기'),
  lock(1450,351,C.muted),
  // Compact state transitions, using the same labels/colors as the task cards.
  t(81,723,'작업 상태 전이',23,C.ink,700,'start'),
  card(81,753,156,57,'대기',C.waiting,C.waiting,C.muted,22),
  line('M239 782 H413'), t(326,742,'선행 작업 완료',17,C.muted),
  card(415,753,156,57,'진행',C.ap,C.ap,C.a,22),
  line('M573 782 H747'),
  card(749,753,156,57,'완료',C.bp,C.bp,C.b,22),
  line('M493 812 V871 H747',C.fail),
  card(749,842,156,57,'실패',C.failp,C.failp,C.fail,22),
  line('M907 871 H1138',C.fail),t(1024,841,'결과가 필요한 후속 작업',17,C.muted),
  card(1141,842,170,57,'차단',C.failp,C.failp,C.fail,22),
  t(1410,768,'독립 작업',18,C.muted),
  peer(1380,812,'A',16),check(1425,812,C.b,16),
  line('M1360 846 H1465',C.b), t(1410,882,'계속 진행',18,C.b,650),
].join(''),939);

(async () => {
  const browser = await chromium.launch({ headless:true, ...(process.env.DIAGRAM_CHROME?{executablePath:process.env.DIAGRAM_CHROME}:{}) });
  try {
    const page = await browser.newPage({viewport:{width:1600,height:1000},deviceScaleFactor:1.5});
    const checks=[];
    for(const [name,svg] of [['agent-state-overview',agent],['agent-selection-detail',selection],['collaboration-state-overview',collaboration]]){
      fs.writeFileSync(path.join(ROOT,name+'.svg'),svg+'\n');
      await page.setContent(`<!doctype html><html lang="ko"><meta charset="utf-8"><style>body{margin:0}svg{display:block}</style>${svg}</html>`);
      await page.evaluate(()=>document.fonts.ready);
      const overflow=await page.locator('svg').evaluate(svg=>{
        const errors=[]; const canvas=svg.viewBox.baseVal;
        for(const text of svg.querySelectorAll('text')){
          const b=text.getBBox();
          if(b.x<0||b.y<0||b.x+b.width>canvas.width||b.y+b.height>canvas.height)errors.push({text:text.textContent,scope:'canvas'});
        }
        for(const node of svg.querySelectorAll('[data-node]')){
          const r=node.querySelector('rect').getBBox();
          for(const text of node.querySelectorAll('text')){
            const b=text.getBBox();
            if(b.x<r.x+5||b.x+b.width>r.x+r.width-5||b.y<r.y||b.y+b.height>r.y+r.height)errors.push({text:text.textContent,scope:'card'});
          }
        }
        return errors;
      });
      await page.locator('svg').screenshot({path:path.join(ROOT,name+'.png')});
      checks.push({name,width:1600,overflow});
    }
    fs.writeFileSync(path.join(ROOT,'state-overviews-render-check.json'),JSON.stringify(checks,null,2)+'\n');
    console.log(JSON.stringify(checks));
    if(checks.some(c=>c.overflow.length))throw new Error('Diagram text overflow');
  } finally {await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
