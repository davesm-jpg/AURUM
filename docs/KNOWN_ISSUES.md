# AURUM known issues and limitations

| ID | Severity | Module | Reproduction / evidence | Status | Workaround | Resolution |
| --- | --- | --- | --- | --- | --- | --- |
| DATA-001 | High | Historical validation | Run the checkpoint in a fresh repository. It contains synthetic fixtures and no authorized market sample. | BLOCKED | Exercise the importer using synthetic fixtures. | Complete D04 with a licensed or otherwise authorized sample and matching schedule. |
| CAL-001 | Medium | Exchange calendar | Review the schedule interface. The caller supplies open windows and the current schema has no trade-date grouping. | OPEN | Independently review and clip a snapshot to the export range. | D02 will add explicit trading dates and normalize reviewed schedules. |
| DATA-002 | Medium | Import/quality | Import a vendor format that omits no-trade minutes. Open intervals with omitted rows produce gap incidents and block replay. | OPEN | Use complete minute exports with explicit vendor semantics. | Evaluate sparse-provider policies after D04. Keep quality decisions explicit. |
| DATA-003 | Medium | Import resources | Import a source too large for available memory. The CSV and normalized rows are currently buffered. | OPEN | Choose bounded export windows. | Profile representative imports and add bounded processing if justified. |
