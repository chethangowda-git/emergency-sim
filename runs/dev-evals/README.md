# Development-seed evaluations (seeds 101 to 120)

Diagnosis runs for docs/FINDINGS.md; never the evaluation seeds 1 to 20. Only `settings.json`,
`results.jsonl` and `summary.json` are committed; the run folders (with recorded LLM calls) stay local.

| Evaluation | Policies | LLM | Code |
|---|---|---|---|
| eval-001 | greedy, agents | rules only | tag `v1-live-eval` |
| eval-002 | greedy, agents | live, gpt-4o-mini, 1 repeat | tag `v1-live-eval` |
| eval-003 | greedy, agents | rules only | fixes B1 and B2 |
| eval-004 | greedy, agents | live, gpt-4o-mini, 1 repeat, `llm_guard` on | fixes B1 and B2, guard G1 and G2 |
