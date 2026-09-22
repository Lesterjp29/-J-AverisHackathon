# Shipping document verification report

**10 emails** — BL_COMPARISON: 9, INVOICE_QUERY: 1

**9 comparison requests** — MISMATCH: 2, NEEDS_REVIEW: 3, OK: 4

| Email | Result | What needs attention |
|---|---|---|
| email_t01_format_only | No mismatch detected | — |
| email_t02_mt_units | No mismatch detected | — |
| email_t03_count_off_by_one | MISMATCH | **container_count** SI: 6 x 40'HC / BL: 7 x 40'HC |
| email_t04_bl_deleted | NEEDS REVIEW (missing_attachment) | comparison needs an SI and a draft BL; only 1 attachment(s) found (SI) |
| email_t05_swapped_files | No mismatch detected | — |
| email_t06_blank_value | NEEDS REVIEW (missing_value) | consignee: BL value is blank/placeholder ('') |
| email_t07_garbage_pdf | NEEDS REVIEW (unreadable) | email_t07_garbage_pdf_BL.pdf: PDF could not be parsed (corrupt or truncated) |
| email_t08_misleading_subject | No mismatch detected | — |
| email_t10_one_real_defect | MISMATCH | **consignee** SI: EAST BRIGHT FZ-LLC / BL: UAB Novakopa |
