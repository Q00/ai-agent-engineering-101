# Week 08 Midterm — Risk-Aware, Memory-Augmented A2A Market Agents

**Student ID:** 25522018  
**Course:** AI Agent Engineering 101 (Q00), Seoul National University of Science and Technology  
**Status:** Implementation and offline validation completed **as of 2 November 2026**; Round 3 outcomes not yet reported  
**LLM provider / model:** GroqCloud / `openai/gpt-oss-20b`  
**Runtime:** Python, Windows PowerShell; instructor-provided Market Kit v4

## 1. Problem and design objective

The Week 08 midterm extends the earlier A2A negotiation and signed-receipt system into an inventory-constrained marketplace. The seller must choose what to stock, sell units without violating its cost floor, and handle signed A2A agreements. The buyer must select a suitable in-stock SKU and trustworthy shop, negotiate within its budget, and submit acceptable purchase terms. The market kit—not the agent—implements the payment/settlement boundary. My design is a **risk-aware, memory-augmented, hybrid LLM/rule-based trading agent** with two different optimization objectives:

- **Seller:** balance sales opportunity, purchase cost, cash exposure, quality and expiry; protect a minimum asking/acceptance price.
- **Buyer:** maximize plausible *realized* surplus while maintaining affordability, product eligibility, payment reliability and a finite negotiation horizon.

Compared with the starter, the extension adds settlement-conditioned shop/SKU scoring, bounded adaptive concessions, a demand-learning inventory policy, grade-aware price quotation, and a shared rate-limit circuit breaker. These are implemented heuristic policies, **not** learned predictive models or a mathematically proven optimum.

## 2. Implementation and architecture

The architecture (`architecture.tex` and `architecture.pdf`) separates **shared context**, **seller decisions**, **buyer decisions**, and the **instructor-controlled settlement boundary**. The active `agent.json` configuration is:

| Hook | Command target | Responsibility |
|---|---|---|
| `seller` | `seller_r3.py` | A2A server, inventory-unit matching, negotiation, signed receipts |
| `buyer` | `buyer_adaptive.py` | Budget-safe, adaptive A2A negotiation |
| `market_buyer` | `market_buyer_r3_v2.py` | Select a SKU/shop using eligibility, past payments and expected surplus proxy |
| `restock` | `restock_r3.py` | Decide stock purchases based on settled demand and capital constraints |
| `review` | `review_r3.py` | Form post-settlement star/review output |

**Model/harness boundary.** GroqCloud proposes `offer`, `counter`, `accept`, or `reject` decisions as applicable. Application code separately validates message/task state, numerical offers, budgets, seller reserves, receipt contents and output schema. `llm_harness.py` validates JSON actions. `groq_quota_guard.py` shares a time-limited HTTP-429 cooldown across buyer and seller processes via `.market/groq_cooldown.json`; `harness.py` and `buyer_adaptive.py` explicitly label their deterministic fallbacks. The guard does **not** enlarge model quota or make fallback outputs into LLM decisions.

**Persistent context.** The system consumes `.market/catalog.json`, `.market/demand.json`, `.market/inventory.json`, the kit's `.market/payments.jsonl`, public ratings, and a bounded window of recent observations. Negotiated A2A receipts are kept distinct from kit-confirmed payments. The buyer writes an audit trace to `logs/buyer-negotiations-v3.jsonl`, including `paid_settlement: unverified` until independent confirmation. Restock decisions can be logged under `.market/restock-decisions.jsonl`. Untrusted seller messages and forum posts are treated as data, not executable instructions.

## 3. Decision policies and quantitative formulation

### 3.1 Seller: settlement-aware restocking and price floor

For a candidate SKU \(i\), the restocking heuristic uses local *settled* seller sales \(n^S_i\), confirmed buyer payments \(n^B_i\), and their category-level analogues:

\[
D_i = 3.2\log(1+n^S_i)+2.1\log(1+n^B_i)
     +0.65\log(1+n^S_{c(i)})+0.40\log(1+n^B_{c(i)})
     +0.10\min(m_i,3),
\]

where \(m_i\) is a capped public-forum SKU mention count. The actual ranking also includes a cold-start price preference, estimated capital cost, and penalties for already stocked categories/SKUs. These coefficients are **manually selected**. A buyer's own purchases are a biased demand signal, not evidence of population-wide preferences. The published demand file identifies orderable categories rather than revealing actual SKU popularity.

Key capital controls in `restock_r3.py`: minimum cash reserve **3,000,000** credits, maximum **16** inventory units, maximum **2** per SKU, **3** per category/root, maximum **8** purchase lines, and estimated spending at most **250,000** credits per restock cycle (also limited by balance). Perishable items are filtered by shelf life and prior category evidence. The system abstains from buying when the market round is not 3.

For a specific in-stock unit with procurement cost \(c_u\), grade \(g\), and catalog list price \(L_u\), `pricing_r3.py` uses

\[
p_{\min}=\left\lceil 1.08c_u\right\rceil,\qquad
p_{\mathrm{ask}}=\max\!\left[p_{\min},\left\lceil\left\lceil L_u\,q(g)\right\rceil e(u)\right\rceil\right],
\]

where \(q(A,B,C,D)=(1.00,0.93,0.82,0.72)\) and \(e(u)=0.92\) for the implemented short-expiry case, otherwise \(1\). The server prevents model-proposed acceptance below \(p_{\min}\). The **8% is markup over purchase cost**, not a guaranteed net profit margin after storage, fees, returns or warranty costs.

### 3.2 Buyer: surplus-aware selection and adaptive bargaining

The `market_buyer_r3_v2.py` hook intersects the requested acceptable SKUs with advertised in-stock offers. For a shop \(s\) and candidate SKU \(i\), it estimates a paid/list price ratio \(\hat r_{si}\) using **confirmed** payment records, backed off to shop-level history or a conservative prior. Let \(\hat p_{si}=\hat r_{si}L_i\), \(B\) be the hard order budget, \(\hat f_{si}\) the heuristic affordability factor, \(\hat q_s\) the smoothed settlement rate, and \(R_s\) the smoothed public rating. The implementation scores

\[
U(s,i)=\hat f_{si}\,\hat q_s\,
       [L_i-\min(\hat p_{si},B)]_+
       +0.004B(R_s-3.7).
\]

Here \(\hat f\) is a **rule-based feasibility proxy**, not a statistically calibrated probability. The offer must still satisfy the actual A2A budget and checkout constraints. The acceptance policy requests a certificate and grades A/B; for food orders with `use_within_hours`, it also sets a minimum remaining shelf-life threshold. These strict conditions may reduce settlement rate and must be evaluated in the live market.

Once a shop is chosen, `buyer_adaptive.py` obtains the seller ask \(a_t\) and computes a bounded concession window. The recommended price initially follows roughly **84%, 90%, 96%, then 100%** of \(\min(a_t,B)\) on affordable asks, with adjusted rules for over-budget asks and prior rejected offers. GroqCloud can propose an action **inside** this window; code clamps offers to the permissible range. The buyer never offers above \(B\), validates completed receipts, and **checks the final seller reply after the last permitted offer** (fixing the original off-by-one termination problem). An environment switch (`A2A_BUYER_STRATEGY=baseline`) enables controlled comparison with the previous policy.

### 3.3 Transaction and trust boundary

`buyer.py` supplies the existing A2A v1.0 transport, Agent Card discovery, task continuation and receipt validation. The seller signs receipts using its existing Ed25519 machinery. The kit verifies conditions, signatures and payment, then records settlements. **A negotiated receipt, a signed receipt, a positive review, or a Round 2 `verified True` line is not itself evidence of a Round 3 paid settlement.** Only kit-confirmed Round 3 payment and seller-settlement evidence is used in the final trade counts.

## 4. Experiments completed before Round 3

All results below are **offline or synthetic**, as observed in the local PowerShell outputs on **2 November 2026**, unless clearly marked otherwise.

| Experiment | Observed result | What it demonstrates / does not demonstrate |
|---|---:|---|
| `test_buyer_adaptive.py` | **9/9 passed** | Budget ceiling, bounded concessions, final-response check, 429 fallback, error handling, receipt guard |
| `test_market_buyer_r3_v2.py` | **4/4 passed** | Surplus-aware ranking, settled-vs-unpaid evidence, acceptance policy, mocked kit JSON output |
| `test_groq_quota_guard.py` | **4/4 passed** | Shared 429 cooldown, non-429 error propagation, normal model path, cooldown expiry |
| `test_restock_r3.py` | **2/2 passed** | Settled evidence drives priority; unpaid events are ignored; Round 2 buys nothing |
| `python -m compileall -q .../week-08` | **Passed** | Syntax only, not full runtime compatibility |
| Adaptive vs. immediate-ask buyer (fixed-ask mock sellers) | **6 matched completions; mean 9.33 credits saved** | A controlled synthetic result; not a measured leaderboard gain |
| Restock preview (actual catalog, simulated Round 3) | **8 products; 8 top-level categories; max estimated spend 207,675** | Conservative cold start; 4,792,325 estimated remaining credits; **no purchases made** |

In the synthetic buyer experiment, the seller reserves were 75, 80, 85, 90, 95 and 100 credits with an ask/budget of 100. Adaptive prices were **84, 84, 90, 90, 96 and 100**, versus 100 for the immediate-ask policy; at reserve 102, neither strategy bought. More turns and strict acceptance criteria can offset the observed synthetic savings in real competition.

**Observed reliability issue.** During the pre-Round-3 run, Groq returned HTTP 429 responses reporting an effective **2,000-token/day** limit on the selected model; repeated retries stalled negotiations. A logged, cross-process cooldown and bounded deterministic fallback were implemented and unit-tested. This is a model-availability mitigation, not evidence of successful live trade completion.

## 5. Round 3 results and leaderboard evidence — to fill from actual logs

**As of 2 November 2026, Round 3 has not yet started; no Round 3 performance result is claimed here.** After the kit reports `round: 3`, record actual evidence below. The operational requirement is at least **20 settled seller** and **20 settled buyer** transactions during Round 3; the competition is scheduled to end **17 November 2026 at 23:59 KST**.

| Round 3 outcome (kit-confirmed only) | Measured value | Evidence location |
|---|---|---|
| Seller paid settlements | **PENDING** | `logs/market-kit.log`, `[acp] settled` and ledger IDs |
| Buyer paid settlements | **PENDING** | `.market/payments.jsonl` / `[acp] paid` |
| Seller net profit and revenue | **PENDING** | Market ledger / leaderboard |
| Buyer realized surplus | **PENDING** | Settled item list price minus actually paid price |
| Public rating / leaderboard percentile | **PENDING** | Dated market snapshot |
| Model vs. fallback decision counts | **PENDING** | Agent/market logs, `[LLM]`, `[quota-guard]`, `[fallback]` |

Keep the original logs, record the exact market round and snapshot time, and deduplicate paid ledger IDs. Compare adaptive vs. baseline on **settled** price, completion rate, turns and 429 incidence; never use negotiated-but-unpaid receipts as paid observations.

## 6. Reproducibility, failure modes, and provenance

From the repository root (`D:\\ai-agent-week08` on Windows), activate the Python environment and execute:

```powershell
$dir = "submissions/25522018/week-08"
python "$dir/test_buyer_adaptive.py"
python "$dir/test_market_buyer_r3_v2.py"
python "$dir/test_groq_quota_guard.py"
python "$dir/test_restock_r3.py"
python "$dir/compare_buyer_policies.py"
python -m compileall -q $dir
python "$dir/install_groq_guard.py" --check
```

To join **only after Round 3 is active**, use the existing kit entrypoint with private credentials in environment variables:

```powershell
$env:GROQ_RETRIES = "1"
python scripts/market_kit.py join --dir submissions/25522018/week-08 `
  --market https://market.ourolabs.ai --name "Raksmey A2A Shop" --tunnel
```

**Security:** Never commit `shop_key.pem`, `buyer_key.pem`, API keys, `.market/` state, secrets, or virtual environments. Keep submitted code and architecture in `submissions/25522018/week-08/`, and include the required market log only after checking that it contains no secrets. The private-key exclusions were verified with `git check-ignore -v` in the local environment.

**Limitations:** Heuristic scores are uncalibrated; the SKU inventory is cold-started before paid history exists; public reviews and forum mentions can be manipulated; the quota guard cannot create Groq capacity; strict A/B certificate filters can decrease purchase count; the 8% seller markup does not cover all economic costs; simulated buyer savings do not imply superior Round 3 rank. The instructor's Market Kit provides ACP/AP2 checkout and ledger semantics and is distinguished from student-created decision policies in the diagram.

**References:** Course Week 08 materials: <https://wpti.dev/ai-agent-engineering-101/week-08-en.html>. Market agent contract: <https://market.ourolabs.ai/agent.md>. The implemented behaviors above are grounded in the submitted Python modules and the tests named in this report.
