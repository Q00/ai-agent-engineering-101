# External review record verification

2026-10-14 KST. Scope: existing AX Week06 assignment note, academic current state, academic source manifest and course RAW source manifest. No new course note or ledger was created.

Scenario: retain original live-test passes, distinguish Claude's conditional submission verdict from Gemini's official-submission-possible/research-conditional verdict, link the completed Gemini followup separately from its partial first response, and mark the report corrections as pending. Changes were made after the parent reported receipt of the completed Gemini followup, and source records were reread immediately before the exact patch to preserve concurrent changes.

Binary observable: the command below exited 0, returning PASS. It verifies four updated records, eight existing review links, four source-response hashes, the active review-followup state, and direct old-log counts. It does not claim a new paid test run.

Invocation:

```sh
python3 -c 'from pathlib import Path
import json,re,hashlib
paths=["/Users/sungjinho/Library/Mobile Documents/iCloud~md~obsidian/Documents/jin_icloud2V2/20_학업/03_과제_보고서/AX이해와활용/261013_AX이해와활용_6주차_A2A과제실험.md","/Users/sungjinho/Library/Mobile Documents/iCloud~md~obsidian/Documents/jin_icloud2V2/20_학업/current_state.md","/Users/sungjinho/Library/Mobile Documents/iCloud~md~obsidian/Documents/jin_icloud2V2/20_학업/source_manifest.md","/Users/sungjinho/Documents/Obsidian_Raw/jin_icloud2V2_raw/Study_Raw/10_Courses/11_AX이해와활용/source_manifest.md"]
ext=Path("/Users/sungjinho/Documents/Obsidian_Raw/jin_icloud2V2_raw/Vscode_Raw/20260529_cleaned_workspace/20_Study_Coursework/.work/ai-agent-engineering-101-week-06/submissions/25620027/week-06/.omo/evidence/external-review-20261014")
links=set()
for name in paths:
 text=Path(name).read_text()
 assert "조건부 제출" in text and "Gemini" in text and "PR 미생성" in text
 for target in re.findall(r"<file://([^>]+)>",text):
  if str(ext) in target: links.add(target)
assert all(Path(x).is_file() and Path(x).stat().st_size>0 for x in links)
note=Path(paths[0]).read_text();state=Path(paths[1]).read_text()
assert "status: locally_verified_review_followup" in note
assert "보고서 보완 미적용" in note and "보고서 보완 미적용" in state
assert "공식 제출 가능, 심화 평가·연구 확장은 조건부 가능" in note
assert "실제 `shop()`에서 실패 후 재계획" in note
assert "검증다." not in state
expect={"CLAUDE_RESPONSE.md":"2a7ca3c224f14eddbe7a3758cdd6b8cbdb979be93624a7734cf33de1d7e0f228","CLAUDE_CHECKS.log":"7c50dd662624a1abc24f762ce33a902651a2953feb7979bca33a76fe3b1e8234","GEMINI_RESPONSE.md":"a83c7e23078c674f9338ca1508006140236e7dfd081411d7996ed924af5b227d","GEMINI_FOLLOWUP.md":"61bc8d09836254711ab558f2dda2dd7097a711fe8ab741aa1141e990b301ff37"}
assert all(hashlib.sha256((ext/name).read_bytes()).hexdigest()==value for name,value in expect.items())
s=ext.parents[2]
c1=(s/"logs/challenge-1/attempt-3/server.log").read_text()
searches=re.findall(r"seller.search task=(\S+) candidates=(\d+)",c1)
assert len(searches)==7 and len({t for t,c in searches})==6 and all(c=="1" for t,c in searches)
guards=[(s/f"logs/buyer-under-test-buyer_holds_budget-{i}.log").read_text() for i in range(1,4)]
assert [x.count("Offer exceeds immutable budget") for x in guards]==[4,4,4]
assert sum(x.count("[BUYER WALK]") for x in guards)==0
assert (s/"logs/challenge-2/attempt-4/buyer.log").read_text().count("[SHOPPING PLAN]")==1
print(json.dumps({"result":"PASS","updated_records":4,"review_links":len(links),"source_hashes_match":True,"c1_unique_tasks":6,"c1_search_calls":7,"all_candidates_one":True,"budget_guard_calls":12,"walkaway_calls":0,"shopping_plans":1,"paid_reexecution":False,"report_corrections_applied":False,"status":"locally_verified_review_followup"},ensure_ascii=False,indent=2))
'
```

Captured output:

```json
{
  "result": "PASS",
  "updated_records": 4,
  "review_links": 8,
  "source_hashes_match": true,
  "c1_unique_tasks": 6,
  "c1_search_calls": 7,
  "all_candidates_one": true,
  "budget_guard_calls": 12,
  "walkaway_calls": 0,
  "shopping_plans": 1,
  "paid_reexecution": false,
  "report_corrections_applied": false,
  "status": "locally_verified_review_followup"
}
```

The earlier discovery command used one extra parent segment for the official README and returned an error for that path. The next complete lookup used s.parents[2]/weeks/week-06/README.md and directly confirmed its sentence allowing narrowing candidates through code search. No source was changed.

This evidence remains inside the assigned project directory. The known stop-hook inherited-cwd restriction is not bypassed by creating Documents-root artifacts.

## Receipt recheck after hook rejection

The same complete command above was executed again after the stop-hook rejected the project evidence location. Exit code: 0. Actual output:

```json
{
  "result": "PASS",
  "updated_records": 4,
  "review_links": 8,
  "source_hashes_match": true,
  "c1_unique_tasks": 6,
  "c1_search_calls": 7,
  "all_candidates_one": true,
  "budget_guard_calls": 12,
  "walkaway_calls": 0,
  "shopping_plans": 1,
  "paid_reexecution": false,
  "report_corrections_applied": false,
  "status": "locally_verified_review_followup"
}
```
