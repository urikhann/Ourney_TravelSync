# TravelSync provider evaluation · Issue #17

Use `services/agent/trial_cases.json` version `synthetic-agent-trials-v1` as the frozen bilingual fixture. Preserve the November 21 Groq pilot in `output/agent-trials/live-01.json` (SHA-256 `2b4ccbd1fbfc5db4541c74dcd99516d22bff2a2330b6263616cd6d258ae5b592`).

| Case | Correct outcome in English and Korean |
| --- | --- |
| Greeting | Acknowledge the greeting without editing the plan. |
| Rain | Replace the 10:00 park stop with the museum or gallery. Describe indoor status as an estimate. |
| Walking | Replace the 10:00 park stop with the museum or gallery and explain the lower walking requirement using the fixture. |
| Locked | Explain that the 14:00 market stop is locked and leave it in place. |
| Unknown hours | Replace the 10:00 park stop with the museum and explicitly say its opening hours are unconfirmed. |
| Closed | Explain that the fixtures have no eligible open indoor replacement and leave the plan as is. |
| Ambiguous | Ask which stop or change the user means and leave the plan as is. |
| No plan | Explain that there is no plan to edit and ask for a plan or sufficient context. |

A correct outcome requires the requested language, the case-specific behavior above, and no material claim unsupported by the fixture. A proposal must pass application validation and target the intended stop and replacement. Score failed turns as incorrect and count them in the denominators. If review is pending, report the reviewed count and leave the final correctness rate pending.

Before using comparative results for a provider decision, agree on acceptable correctness, completion rate, latency and cost with the team. Until then, this evaluation can establish observed differences and integration blockers.

## Smoke test and decision

Both providers used the shared strict tool loop. Gemini used the configured primary model ID with SDK-default reasoning and no explicit output-token cap. Groq used low reasoning effort and a 2,048-token cap. Both had fallback and retries disabled. Account tiers and run-attributable billing were unavailable.

| Provider | Completed | Validated edits | Failure | Attempt duration | Model requests | Returned tokens |
| --- | ---: | ---: | --- | ---: | ---: | ---: |
| Gemini `gemini-3.8-flash` | 0/1 | 0/1 | HTTP 503 | 2.297 s | 1 | Unreported |
| Groq `qwen/qwen3.8-27b` | 0/1 | 0/1 | HTTP 429 | 2.047 s | 3 | 3,161 before failure |

The connected smoke test covered the English rain case. It yielded no replies for bilingual review and no denominator for cost per validated edit. Preserve the sandbox connectivity attempt separately from provider results. The saved Groq pilot remains historical evidence.

Keep the current configuration pending more evidence and leave #17 open. One failed turn per provider cannot establish a provider ranking. The smoke test did not meet the prerequisite for the 96-turn benchmark. Collect a valid proposal from each provider before starting the full comparison. Fallback selection and forced-failure testing are deferred until the comparison supports a fallback candidate.

## Reproduction

Run from the repository root using the agent environment with `services/agent/requirements.txt` installed. For live runs, export the two API keys privately. Use new output paths.

```powershell
python services/agent/trial_models.py --compare-gemini --repeats 3 --output output/agent-trials/dry-new.json
python services/agent/trial_models.py --compare-gemini --live --case rain --language en --output output/agent-trials/smoke-new.json
```

Use `--retries 5` to allow up to five additional attempts per failed turn. The runner backs off up to 60 seconds between attempts and stores every attempt. The summary reports both first-attempt and eventual completion, plus total reported usage and errors across attempts. Compare runs with the same retry policy.

After both providers return validated proposals in the smoke test, run one full paired round in each of three separate time windows. Record the observed account tiers using `--gemini-tier` and `--groq-tier`. The runner alternates model order across the cases, languages and round numbers. The commands below use an example 30-second turn interval. Set it to respect both accounts' limits.

```powershell
# Run each command in a separate time window.
python services/agent/trial_models.py --compare-gemini --live --first-round 1 --pause-seconds 30 --output output/agent-trials/round-1.json
python services/agent/trial_models.py --compare-gemini --live --first-round 2 --pause-seconds 30 --output output/agent-trials/round-2.json
python services/agent/trial_models.py --compare-gemini --live --first-round 3 --pause-seconds 30 --output output/agent-trials/round-3.json
```

Review replies against the rubric and record judgments as a JSON list with `model`, `repeat`, `case`, `language` and Boolean `correct` fields. Keep review notes for unsupported claims and incorrect no-change responses. If attributable billing is available, provide a JSON object mapping model ID to total USD for that report. A pricing-derived estimate needs a dated source, its input and output rates and any assumptions about reasoning and cached tokens. Present that estimate separately from billed costs.

```powershell
python services/agent/summarize_trials.py output/agent-trials/round-1.json --reviews reviews-1.json --billed-costs billing-1.json --output output/agent-trials/round-1-summary.json
```

The smoke artifacts are `issue17-smoke-network.json` and `issue17-smoke-summary.json` under `output/agent-trials/`. The 96-turn schedule is `issue17-dry-final.json`. These files are local run artifacts.

## November 24 retry run

Gemini returned HTTP 503 on all six attempts, including the initial attempt and five retries. Groq Qwen answered on the first attempt in 3.391 seconds and returned a validated swap from the 10:00 park stop to the museum. The English reply passed the human rubric: it called the museum “probably indoor” and used the fixture's open-hours result. Groq reported 5,089 tokens across three model requests. Attributable billing was unavailable.

The full paired benchmark remains gated on Gemini returning a validated proposal. The raw attempt log and reviewed summary are `issue17-smoke-retry5.json` and `issue17-smoke-retry5-reviewed.json` under `output/agent-trials/`.

The branch now defaults to Qwen on Groq by request. Gemini is the backup in the default configuration. The benchmark remains open for the bilingual comparison and cost evidence.
