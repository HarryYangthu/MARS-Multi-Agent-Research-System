# V3.0 migration plan before public release

This checklist is intentionally evidence-bounded. Exact offending lines live
in the generated private audit report; the public plan records only path
classes and required actions.

| Path class | Required action | Owner boundary |
|---|---|---|
| Root product and acceptance prose | Replace project-specific examples with Synthetic Pack examples | Core documentation |
| Agent prompts, examples, and research defaults | Move vertical knowledge into an external Project Pack | Agent maintainers |
| Execution defaults and local paths | Replace machine paths with environment-driven configuration | Runtime maintainers |
| Frontend default task copy | Use domain-neutral sample content | Frontend maintainers |
| Legacy project directory and stand-in repository | Exclude from public Core or publish a separately reviewed generic fixture | Project Pack owner |
| Interview, phase, and implementation reports | Keep outside the public source allowlist | Documentation owner |
| Reachable history | Decide between a reviewed clean-root public repository or separately approved history migration | Repository owner |

W8 does not perform any of these cross-module edits. The release workflow stays
blocked until a new audit returns `decision=pass` and the external secret scan
also passes.
