# Python coding rules — PIMC

The Coding Agent must follow these rules when writing or modifying Python in
the pimc project.

## 1. Tensor shape comments

Every tensor op must have a shape comment immediately before or after:

```python
# x: (B, T, D)
y = self.proj(x)            # (B, T, D')
```

This makes review possible without re-deriving shapes.

## 2. Type annotations

All public functions and methods are fully annotated. `mypy --strict` must pass.

## 3. Logging

Use `loguru` (`from loguru import logger`). Never use `print` in research
modules.

## 4. Configuration

Hyperparameters live in `configs/*.yaml`. Hard-coded magic numbers in
`libs/` modules are forbidden.

## 5. Frozen surfaces

Classes and signatures listed in `projects/pimc/AGENTS.md` are baseline-
protected. Gate 5 enforces these.
