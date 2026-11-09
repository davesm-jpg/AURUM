# AURUM known issues and limitations

| ID | Severity | Module | Reproduction / evidence | Status | Workaround | Resolution |
| --- | --- | --- | --- | --- | --- | --- |
| DATA-001 | High | Historical validation | Run the checkpoint in a fresh repository. It contains synthetic fixtures and no authorized market sample. | BLOCKED | Exercise the importer using synthetic fixtures. | Complete D04 with a licensed or otherwise authorized sample and matching schedule. |
| CAL-001 | Medium | Exchange calendar | Inspect a legacy snapshot with only a top-level `windows` array. It has no exchange trade dates. | RESOLVED | Re-import with a reviewed dated snapshot when trade dates are needed. | D02 added schema v2 with full windows grouped by date. Legacy import and replay are supported. |
| CAL-002 | Medium | Exchange calendar | Run the importer without a reviewed schedule. The CLI requires the caller's schedule and trusts its coverage and trade-date assignments. | OPEN | Review effective exchange hours and exceptions before importing. | Validate a production snapshot in D04. Automated schedule retrieval remains unimplemented. |
| DATA-002 | Medium | Import/quality | Import a vendor format that omits no-trade minutes. Open intervals with omitted rows produce gap incidents and block replay. | OPEN | Use complete minute exports with explicit vendor semantics. | Evaluate sparse-provider policies after D04. Keep quality decisions explicit. |
| DATA-003 | Medium | Import resources | Import a source too large for available memory. The CSV and normalized rows are currently buffered. | OPEN | Choose bounded export windows. | Profile representative imports and add bounded processing if justified. |
