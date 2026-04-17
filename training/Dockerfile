FROM python:3.12-slim

# System deps for common ML packages (numpy, opencv, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ \
    && rm -rf /var/lib/apt/lists/*

# Install the vswe_checkpoint package
COPY . /opt/vswe-checkpoint
RUN pip install --no-cache-dir /opt/vswe-checkpoint

# The runner auto-detects and installs script dependencies at startup
ENTRYPOINT ["python", "-m", "vswe_checkpoint.runner"]
