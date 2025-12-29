# Testing Guide

This project’s CI runs tests on Linux with Python 3.11.

## Quick Commands

- Python unit tests: `pytest`
- CLI tests: `cd cli && go test ./...`
- Admin UI build: `cd admin_ui/frontend && npm ci && npm run build`

## References

- Test overview: [`tests/README.md`](../../tests/README.md)
- CI workflow (staging/main): [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
- Regression hardening workflow: [`.github/workflows/regression-hardening.yml`](../../.github/workflows/regression-hardening.yml)

