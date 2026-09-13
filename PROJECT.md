# False Floor

I'm building False Floor to study a simple question: does an AI model answer differently when it is told that its test score could affect whether it gets deployed?

Capability evaluations help inform decisions about AI systems. If the setting of a test changes how a model responds, the score may tell us less about its abilities than we expect. False Floor gives researchers a way to examine that problem through controlled comparisons.

[Explore the project](https://false-floor.vercel.app) · [Read the method](PREREGISTRATION.md)

## How it works

The current dataset contains 20 question pairs. Each pairs an ordinary question with a safety-relevant question intended to require similar reasoning. Both use a multiple-choice format.

The evaluation asks the same questions under five conditions: a baseline, general evaluation pressure, a safety-report cue, a stated deployment consequence, and a statement that reasoning will be audited. It also changes the order of the answer options and repeats the baseline to check for variation.

The analysis compares how much accuracy changes on the two sides of each pair. This helps distinguish a general performance change from one concentrated on safety-relevant questions. The reports include uncertainty estimates and a separate analysis of responses that could not be scored.

A performance shift is a reason to look more closely at the answers. Refusal, confusion, wording, and ordinary variation can also affect a score, so the experiment alone cannot establish deliberate underperformance.

## What is here

The repository includes the evaluation code, question pairs, prompts, analysis, and reporting tools. It runs on Inspect AI and saves the settings and responses behind each experiment so the results can be checked.

The current release is a research prototype. The next stage is to review and expand the question pairs, complete experiments across model families, and publish findings with the evidence behind them. The aim is a practical tool that other researchers can use when assessing the reliability of AI evaluations.

## Get involved

To try the evaluation, start with the [running guide](docs/RUNNING.md). You can check the workflow with a mock model before using a paid API.

Feedback on the question pairs is especially useful. The two questions need to be comparable in reasoning and difficulty for the experiment to be informative.

- [Experimental protocol](PREREGISTRATION.md)
- [Dataset design](false_floor/data/SCHEMA.md) and [review notes](docs/DATASET_REVIEW.md)
- [Current progress](docs/BENCHMARK_STATUS.md)
- [Related research](REFERENCES.md)
- [Contributing](CONTRIBUTING.md)

Released under the MIT license.
