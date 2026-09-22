# Example Data

| Folder | Contents | Use |
| --- | --- | --- |
| `sample/` | 520 inbox records, attachments, and a submission-shape template | Default batch input |
| `stress/` | 10 deliberately difficult emails and their fixtures | Regression testing |
| `demo/` | Document pairs, an email, and simulated phone photos | Interactive upload demonstrations |

Attachment references inside inbox JSON remain relative to their dataset root (`attachments/...`). Pass the entire `sample/` or `stress/` directory to the pipeline, not its `inbox/` subdirectory.

Some fixtures deliberately omit attachments or contain corrupt data. Preserve those cases when adding tests. `sample_submission.json` is an output-shape template, not ground-truth evidence of prediction accuracy.

Keep real customer data outside these committed fixture directories.
