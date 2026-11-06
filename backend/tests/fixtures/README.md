# Test fixtures

Everything here is synthetic. Never add real activity files: they contain personal health and location data.

- `synthetic_run.fit`: one made-up 10 km trail run (2026-10-18 07:30 UTC, 50 min, avg HR 148). It was written with the
  [`fit-tool`](https://pypi.org/project/fit-tool/) encoder, which is not a project dependency: a FileId message and a
  Session message, nothing else (no GPS or samples).
