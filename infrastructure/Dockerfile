FROM rocker/r-ver:4.5.2

# Install Python 3, Node.js, curl, git, and system libraries needed by R packages
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv curl git \
    libxml2-dev libcurl4-openssl-dev libssl-dev \
    libfontconfig1-dev libharfbuzz-dev libfribidi-dev libfreetype6-dev libpng-dev libtiff5-dev libjpeg-dev \
    zlib1g-dev libbz2-dev liblzma-dev \
    libuv1-dev libgit2-dev libssh2-1-dev libglpk-dev \
    && pip3 install --break-system-packages flask PyJWT cryptography \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*

# Install GitHub CLI (gh) for commaBot GitHub operations
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null && \
    apt-get update && apt-get install -y gh && \
    rm -rf /var/lib/apt/lists/*

# Install renv for reproducible R package management
RUN R -e "install.packages('renv', repos = 'https://cloud.r-project.org')"

# Set up renv cache path (persists across container recreations)
ENV RENV_PATHS_CACHE=/opt/renv/cache

# Copy renv files first for layer caching (don't rebuild R packages unless lockfile changes)
WORKDIR /opt/comma
COPY comma/renv.lock renv.lock
COPY comma/renv/settings.json renv/settings.json
COPY comma/renv/activate.R renv/activate.R

# Restore R packages from lockfile
RUN R -e "renv::restore(prompt = FALSE)"

# Install devtools + roxygen2 for package development (not in renv.lock since these are dev tools)
RUN R -e "install.packages(c('devtools', 'roxygen2', 'testthat', 'pkgdown'), repos = 'https://cloud.r-project.org')"

# Letta Code (provides the `letta` CLI and `letta server` command)
WORKDIR /app/sdk
COPY webhook-sdk/package.json webhook-sdk/package-lock.json ./
RUN npm ci --omit=dev

# GitHub webhook channel adapter (replaces the SDK relay)
# Plugin files go in ~/.letta/channels/github/ for auto-discovery
RUN mkdir -p /root/.letta/channels/github
COPY channels/github/channel.json /root/.letta/channels/github/
COPY channels/github/plugin.mjs /root/.letta/channels/github/
COPY channels/github/accounts.json /root/.letta/channels/github/
COPY channels/github/routing.yaml /root/.letta/channels/github/

# Webhook listener + recovery + token utility
WORKDIR /app
COPY webhook-listener.py .
COPY recovery.py .
COPY commabot-github-token.py .
COPY commabot.private-key.pem .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

# Add letta CLI to PATH (bundled with @letta-ai/letta-code dependency)
# Symlink so it's available in docker exec sessions too
RUN ln -s /app/sdk/node_modules/@letta-ai/letta-code/letta.js /usr/local/bin/letta
ENV PATH="/app/sdk/node_modules/@letta-ai/letta-code:${PATH}"

# renv::restore() installs packages to /usr/local/lib/R/site-library/ (already in .libPaths())
# The renv cache at /opt/renv/cache/ holds the underlying package files (symlinked from site-library)
# No Rprofile.site modification needed — packages are discoverable via the default library path

EXPOSE 8080

ENTRYPOINT ["./entrypoint.sh"]
