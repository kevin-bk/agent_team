# Project-specific dependency layer on top of the generic Agent Team runtime.
# Build context: the repository's node_modules directory.
ARG BASE_IMAGE=agent-team/agent-team-sandbox:latest
FROM ${BASE_IMAGE}

ARG REPO_SLUG
LABEL org.agent-team.project-dependencies="${REPO_SLUG}"

USER root
COPY . /opt/agent-team/project-deps/${REPO_SLUG}/node_modules

WORKDIR /workspace
