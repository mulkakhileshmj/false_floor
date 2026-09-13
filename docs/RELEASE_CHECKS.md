# Release checks

Verified locally on Windows with Python 3.12.14 on 2026-09-13.

- 66 tests passed, including regression cases for misleading flags, Holm correction,
  missing models, repeated controls, incompatible settings, duplicate cells,
  incomplete logs, and agreement between HTML and Markdown reporting.
- Dataset validation passed with no warnings.
- A complete mock panel finished: 480 responses across 12 experiment cells.
- Resume reused all 12 completed cells without launching new evaluations.
- The v0.2 wheel built and installed into an isolated project-local target.
  Importing from that target loaded the packaged task, all 40 items, and five conditions.
- The public export uses an explicit file allowlist and a credential-pattern check.
  Local logs, credentials, downloaded papers, environments, and caches are excluded.

The mock provider's default replies are deliberately not valid answer letters.
That run exercised the unusable-response path through scoring and reporting.
Synthetic regression fixtures cover parsed answers and significant comparisons.
Neither is evidence of model behaviour in the main research study.

The original virtual environment launcher could not start, so local checks used
an available Python runtime with the installed project dependencies. The separate
wheel import check verified package contents without importing the source checkout.
Inspect's optional control-server surface emitted an AF_UNIX warning under that
Windows runtime; evaluation and report generation still completed.

The GitHub Actions workflow is configured for Windows/Linux and Python 3.11/3.12.
Those remote jobs have not run yet. Fresh dependency resolution on another machine,
paid-provider integration, statistical calibration, and dataset validity remain
separate checks. This release is a public research prototype.
