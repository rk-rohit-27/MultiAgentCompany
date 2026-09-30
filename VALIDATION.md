# Validation record

Date: 2026-09-30. Python 3.12.14 on Linux.

- 22 automated controller tests passed using real LangGraph 1.2.12 and real persistent AsyncSqliteSaver 3.1.1. Model, research, Telegram and container behavior were mocked in these tests.
- Ruff checks passed for controller, sandbox runner and tests.
- Python bytecode compilation passed.
- Vault initialization created the required folder structure, dashboard, six employee profiles and Company_Brain.md.
- Exact tested Python dependency versions are in requirements.lock.
- Direct runtime package versions for Next.js 16.3.7, React 19.3.0, FastAPI 0.142.1, Pydantic 2.13.5, pytest 9.1.1 and Ruff 0.16.9 were verified against official npm/PyPI registry metadata.

Not executed: real OpenAI calls, real Telegram delivery, Docker build/run, generated frontend TypeScript compilation or backend tests, Next.js production build, browser end-to-end tests, or vulnerability scanning. Docker was unavailable in the validation environment. This record is not certification of production readiness.
