# Week 03: Who should review a generated image? (26520024)

## 1. Setup

Six synthetic **text-only review requests**: A sexual safety, B violence safety,
C character/logo similarity. No image classification or legal judgments. Gold
is balanced (two each), committed before runs (`b06ac13`). OpenAI via Codex CLI
0.153.0/ChatGPT login, `gpt-6-astra`, reasoning `low`; existing conda base Python
3.8.19. Temperature/max output tokens: **not directly configurable; internal
values unknown**. Fresh tool-disabled calls embed system/user strings, not
direct API roles. Exact [prompts](prompts.json): baseline has distinct skills;
homogeneous changes only skills to identical generalists; overconfident tells
only C to always bid with confidence >=95. Same tasks/order/model/manager; highest
confidence wins, ties A/B/C order. Invalid JSON means no bid, without retry.
Codex-assisted implementation. Run here in authenticated base:
`python run_experiment.py --repetitions 3`; [README](README.md) gives verification.

## 2. Results

From [results.csv](results.csv): messages = announcements + positive bids + awards,
**excluding declines**; PF = parse failures. Each run uses 18 model calls;
CSV notes additionally retain tokens and wall time.

| Run | Condition | Tasks | Correct | Messages | Unassigned | Misawards | PF |
|---|---|---:|---:|---:|---:|---:|---:|
| 001 | baseline | 6 | 6 | 30 | 0 | 0 | 0 |
| 002 | homogeneous | 6 | 2 | 42 | 0 | 4 | 0 |
| 003 | overconfident | 6 | 6 | 34 | 0 | 0 | 0 |
| 004 | baseline | 6 | 6 | 30 | 0 | 0 | 0 |
| 005 | homogeneous | 6 | 2 | 42 | 0 | 4 | 0 |
| 006 | overconfident | 6 | 6 | 34 | 0 | 0 | 0 |
| 007 | baseline | 6 | 6 | 30 | 0 | 0 | 0 |
| 008 | homogeneous | 6 | 2 | 42 | 0 | 4 | 0 |
| 009 | overconfident | 6 | 6 | 34 | 0 | 0 | 0 |

162 actual calls; no crashes or discarded trials. Raw-event replay passed.

## 3. Smith 1980 comparison

Source: [Smith, distributed sensing example and Sections III/VI](https://reidgsmith.com/The_Contract_Net_Protocol_Dec-1980.pdf).

| Element | Smith's distributed sensing | This reproduction |
|---|---|---|
| Nodes | Sensor/processor nodes; dynamic roles | Fixed manager, three prompt-defined bidders |
| Bid production | Programmed selection: location/sensor descriptors | Generated confidence/reason JSON |
| Bid honesty | Cooperative descriptions; no truth guarantee specified (inference) | Syntax checked, capability unverified |
| Allocation quality | Spatial coverage and sensor mix | Frozen-owner agreement, not review accuracy |
| Negotiation cost | Traffic; eligibility/direct contracts reduce it | Protocol messages, plus calls/tokens recorded |
| Failure modes | Node failures; reannouncement aids recovery | Observed tie bias/extra bids; parse/crash handling tested offline |

## 4. Interpretation

Baseline achieved 18/18 gold matches; homogeneous 6/18, with messages rising
30 to 42/run (+40%). On T02, baseline's B bid 100 and A/C declined
([001:29-40](logs/001-baseline.log#L29)); homogeneous produced three 100s, so A
won the tie ([002:29-40](logs/002-homogeneous.log#L29)). Overconfidence changed
bids, not awards: 18/18 correct, 34 messages/run (+13.3%). C acknowledged T05
was outside its specialty yet bid 95 ([003:99](logs/003-overconfident.log#L99));
B's 100 won ([003:94-100](logs/003-overconfident.log#L94)). All 12 out-of-specialty
C bids were 95, below matching specialists' 100. Thus **no award degradation was
observed**, not evidence of robustness: the protocol does not verify capability
claims. Homogeneous correctness measures ownership, not generalist competence.
Six easy tasks, fixed task/condition/tie order, unknown sampling settings and
three repeats limit generalization; image-detection performance is unmeasured.
