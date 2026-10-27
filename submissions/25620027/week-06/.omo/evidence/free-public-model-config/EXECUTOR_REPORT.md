# Public model configuration executor report

Five assigned files changed: `market_a2a/harness.py`, `public_seller.py`,
`deployment/compose.yaml`, `tests/test_public_seller.py`, and new
`tests/test_harness_model.py`.

Original experimental model default retained. Public runtime requires explicit
Cloudflare endpoint, model, and SecretStr token. It rejects ambient custom headers,
ignores paid organization/project defaults, and disables redirects.

Final recorded checks: full offline suite **117 passed** (`pytest.txt`), Ruff
**All checks passed!** (`ruff.txt`), source formatting **49 files already formatted**
(`format-source.txt`), strict basedpyright **0 errors, 0 warnings, 0 notes**
(`types.txt`). The stop-hook receipt rerun passed **26 focused tests** with the exact
command, stdout, exit code 0, and judgment captured in `RECEIPT.txt`.

`REPORT.md` maps each criterion to exact scenario, invocation, binary observable,
and artifact. `header-regression-before.txt` captures the demonstrated pre-fix
ambient Authorization regression. Skill audit retains two preexisting match
warnings; AST comparison to HEAD is in `scope-and-audit.txt`. Archived Markdown
formatting issues were preserved. No live-provider or local Docker success is
claimed; credentials and cloud resources were not accessed by this executor.
