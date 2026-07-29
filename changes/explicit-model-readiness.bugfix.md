Report missing model artifacts in doctor and runtime readiness, restrict
downloads to explicit preparation jobs with live progress, and skip dialogue
cleanly for videos without audio.

Disclose pinned model download/cache sizes and require UI or CLI confirmation
before preparation. Use bounded HTTP downloads instead of Xet transfers that
can remain parked at zero bytes.
