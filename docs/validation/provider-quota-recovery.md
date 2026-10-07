# Provider quota error visibility

A real GLM request failed with HTTP 429 and code 1113. A separate minimal health
request confirmed the provider message: insufficient balance or no available
resource package. This is an external prerequisite, not a local hanging process.

Recovery now explains this specific failure using the terminal native receipts,
checking provider identity and exact HTTP/error codes. Unknown codes, another
provider, later success or a newer request do not inherit a quota diagnosis. No
response body, credentials, approval bypass or automatic replay is introduced.
Large prompt-bearing request records require a bounded expanding read window.

A successful recovery submission notice now clears once fresh task status arrives,
so it cannot hide a later failure. Unknown-outcome notices still require checking.

47 targeted checks passed, including the actual archived quota rejection; five
checks requiring an unavailable historical archive were explicitly skipped.
Frontend type checking passed. The zero-default active-time field was added to
the existing backward-compatible fingerprint regression expectation.

Full-flow acceptance remains incomplete until a usable API account is configured.
The current run's cumulative usage and candidate were preserved; no failed stage
was relabeled as passed and no simulation/report was manufactured.
