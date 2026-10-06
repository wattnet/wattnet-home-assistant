FROM ghcr.io/home-assistant/home-assistant:stable

# wattnet-client is not on PyPI yet: install the local checkout instead. Its
# version is raised so that it satisfies the requirement in manifest.json and
# Home Assistant does not try to download it. The build backend (poetry-core)
# is only on PyPI, not on Home Assistant's wheel index, hence the strategy.
ARG CLIENT_VERSION=1.1.0
ENV UV_INDEX_STRATEGY=unsafe-best-match
COPY .client-src /tmp/wattnet-client
RUN sed -i "s/^version = \".*\"/version = \"${CLIENT_VERSION}\"/" /tmp/wattnet-client/pyproject.toml \
    && uv pip install --system /tmp/wattnet-client \
    && rm -rf /tmp/wattnet-client
