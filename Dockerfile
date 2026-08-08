# Hugging Face Spaces (Docker SDK) image.
# Local equivalent: `make demo` after `make install` in a venv.
FROM python:3.11-slim

WORKDIR /app

# Install only what the runtime needs (no dev deps).
COPY pyproject.toml README.md LICENSE ./
COPY src/ ./src/

RUN pip install --no-cache-dir -e .

# HF Spaces (Docker SDK) routes traffic to $PORT; default 7860 to match
# the Space frontmatter `app_port: 7860`.
ENV PORT=7860
EXPOSE 7860

# Reduce demo cost when running on a public URL.
ENV MAX_USD_PER_SESSION=0.20
ENV MAX_PLANS_PER_HOUR=10

CMD ["sh", "-c", "python -m adaptivetouragent.app --host 0.0.0.0 --port ${PORT}"]
