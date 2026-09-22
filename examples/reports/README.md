# Reference Reports

These reports were preserved from the original repository during reorganization. They are snapshots, not live results or externally scored benchmarks.

| Folder | Original directory | Purpose |
| --- | --- | --- |
| `sample/` | `out/` | Default classification of the 520-email bundle |
| `alternative/` | `out_alt/` | Alternative treatment of requests to send draft BL documents |
| `stress/` | `stress_out/` | Results for the 10 difficult fixtures |

The dashboard uses `sample/` when no runtime batch report exists. New runs write under `outputs/`; regression tests use temporary storage so these reference files are not overwritten.
