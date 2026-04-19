# Contributing

Contributions are welcome — bug fixes, security improvements, documentation, and thoughtful feature additions.

## What to contribute

**Great contributions:**
- Security fixes (please report via email first — see SECURITY.md)
- Bug fixes with a clear reproduction case
- Documentation improvements
- New example configs (reverse proxies, init systems)
- Test coverage improvements
- Performance improvements that don't add complexity

**Think carefully before contributing:**
- New features that add UI complexity (the minimal design is intentional)
- Dependencies that expand the attack surface
- Features that require server-side state beyond what SQLite handles
- Anything that stores user data beyond what's needed for file delivery

**Not accepted:**
- Analytics or tracking
- User accounts or registration
- Paywalled features
- Breaking changes to the download URL format

## Getting started

```bash
git clone https://github.com/majmohar4/yeet
cd yeet

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
pip install httpx pytest

# Set up environment
cp .env.example .env
# Edit .env — set SECRET_KEY (any 32+ char string for local dev)
# Set CLAMAV_ENABLED=false if you don't have ClamAV running locally

# Create data directories
mkdir -p data/uploads data/archive

# Run the server
uvicorn app.main:app --reload --port 8000
```

## Running tests

```bash
# All security tests (server must be running)
pytest tests/test_security.py -v

# Specific test category
pytest tests/test_security.py -v -k "password"
pytest tests/test_security.py -v -k "header"
```

Tests require a running server. They hit real endpoints — no mocking.

## Code style

- Python: follow PEP 8, 4-space indentation, max line length 100
- Use type hints for all function signatures
- No docstrings needed unless the function has a non-obvious contract
- No comments unless the *why* is genuinely surprising
- Prefer `async/await` throughout — this is an async app
- Validate all user input at the boundary; trust internal data

## Submitting changes

1. Fork the repo and create a branch: `git checkout -b fix/what-you-fixed`
2. Make your changes
3. Run the test suite — all 73 tests must pass
4. Commit with a clear message: `fix: describe what changed and why`
5. Open a pull request with a description of what changed

Keep pull requests focused — one logical change per PR. Large refactors should be discussed in an issue first.

## Security contributions

If your contribution touches security-sensitive code (auth, file handling, input validation), please call that out explicitly in the PR description and explain how you tested it. Security PRs get extra scrutiny.
