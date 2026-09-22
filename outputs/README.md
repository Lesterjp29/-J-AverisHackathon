# Local Runtime Outputs

The default batch command writes `report.json`, `report.md`, `review_queue.json`, and `submission.json` to `outputs/sample/`. The dashboard reads those results when present, otherwise it displays the checked-in sample snapshot in [`examples/reports/sample`](../examples/reports/sample).

Reviewer decisions are saved to `outputs/resolutions.json`. These runtime files are ignored by Git. Existing local resolutions were preserved here during the repository reorganization.

Reference reports are kept separately under [`examples/reports/`](../examples/reports/). Tests write temporary reports and do not overwrite the reference snapshots.
