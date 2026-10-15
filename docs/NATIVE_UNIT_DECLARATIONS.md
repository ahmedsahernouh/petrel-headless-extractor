# Native unit declarations and surface-mask preparation checks

Added 2 October 2026 for version 1.0.0.

Native log and grid metadata now includes `native_unit_evidence`, version
`geoviewer.native-unit-declarations/1`. It preserves exact linked template
declarations, customization flags, measurement codes, scalar factors/offsets when
present, project unit preferences and direct object overrides. Each record has
its Model locator and serialization version. Missing, nested, duplicate,
non-scalar and over-bound fields remain explicitly uninterpreted. Reading these
declarations does not apply factors or change the existing unit-resolution gate.

The source catalogue now carries this evidence, `project_units` and
`unit_resolution_gaps`. Project display preferences are labelled separately from
stored sample units. Even a project matching the existing Metric gate does not
resolve QA scalar semantics; the evidence reports the object's final unit status.

Retained metadata verification compared 1,354 exact native objects against
the prior resolver. Existing fields and all unresolved unit outcomes were
unchanged. All 1,354 declaration records reached a metadata-only catalogue with
zero claimed exports. An independent .NET XML reader confirmed the exact selected
template and ProjectSubject unit fields, using shared bounded outer framing.
The `ms` template declaration and `Field-UTM` display settings do not resolve
stored physical units.

The comparison covers 1,354 retained metadata records; its detailed fixtures are not included in this public repository.

Surface-property preparation now requires currently available, accepted full
node masks for both property and parent geometry, for every registration method.
Reviewed assertions cannot waive these masks. Historical node-registration
evidence remains intact when bulk artifacts are pruned. `NODE_MASK_UNAVAILABLE`
blocks source prerequisites and receiving preparation while leaving unrelated
geometry assessment unaffected. This checks accepted artifact availability;
recorded numerical QC and fresh content hashing remain separate evidence axes.

Source prerequisite revision is now 5; OpendTect and PaleoScan preparation
revisions are 6. Older source catalogues remain readable, but preparation
decisions must bind to the current profile revision. A nonempty method label is
not physical-unit calibration; exact source declarations and attributed
reviewed assertions retain their recorded provenance and scope.

A before/after fixture reproduced the false qualification before the mask requirement was added.
Receiving import and complete-project delivery have not been tested for these
changes.


Current release scope is in [the Field Viewer 1.0.0 release notes](RELEASE_NOTES.md).
