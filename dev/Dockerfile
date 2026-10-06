FROM ghcr.io/home-assistant/home-assistant:stable

# wattnet-client is not on PyPI yet: install the local checkout instead. Its
# version already satisfies the requirement in manifest.json, so Home Assistant
# does not try to download it. The build backend (poetry-core) is only on PyPI,
# not on Home Assistant's wheel index, hence the strategy.
ENV UV_INDEX_STRATEGY=unsafe-best-match
COPY .client-src /tmp/wattnet-client
RUN uv pip install --system /tmp/wattnet-client \
    && rm -rf /tmp/wattnet-client
