FROM ghcr.io/home-assistant/home-assistant:stable

# Install the local checkout of wattnet-client instead of the release on PyPI,
# to try changes of the client before they are published. Its version satisfies
# the requirement in manifest.json, so Home Assistant does not download it. The build backend (poetry-core) is only on PyPI,
# not on Home Assistant's wheel index, hence the strategy.
ENV UV_INDEX_STRATEGY=unsafe-best-match
COPY .client-src /tmp/wattnet-client
RUN uv pip install --system /tmp/wattnet-client \
    && rm -rf /tmp/wattnet-client
