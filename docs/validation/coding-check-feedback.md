# Coding check feedback from a real product journey

The Coding executor guessed shell strings and `default` as configured command IDs.
An unknown ID now returns the actual available IDs/argv and `executed=false`,
rather than incorrectly reporting that no commands are configured. Coding context
lists configured check IDs before its first model request. All command execution
continues to use the host allowlist; arbitrary model shell commands are not admitted.

Long failure output now retains its beginning and final summary, with an explicit
truncation marker. Previously a warnings block obscured the actual failed assertion.
Two pure formatting regressions passed, together with eight interpreter and budget
checks. A real invalid-ID call against the connected research project confirmed the
error lists `pytest_quick` without executing it.

The real research project tests also exposed temporary diagnostic code leaking
global config and a config test retaining the previous scheduler. Private diagnostic
files were preserved with the journey evidence, and the config test now isolates
and restores config state. All 86 actual research repository tests passed with the
same Git/runtime PATH as the backend. No frozen model/metric/training code was changed.

The first research run honestly stopped at its cumulative 100-call budget; its failed
state and ledger were preserved. A separate fresh UI task is the full-flow regression,
not a reset of the failed run or a substitute success record.
