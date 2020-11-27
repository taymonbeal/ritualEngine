# See here for image contents: https://github.com/microsoft/vscode-dev-containers/tree/v0.145.1/containers/ubuntu/.devcontainer/base.Dockerfile

# [Choice] Ubuntu version: bionic, focal
ARG VARIANT="focal"
FROM mcr.microsoft.com/vscode/devcontainers/base:0-${VARIANT}

COPY requirements.txt ./
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get -y install --no-install-recommends python3-venv \
    && python3 -m venv ~/venv \
    && . ~/venv/bin/activate \
    && echo '. ~/venv/bin/activate' >>~/.bashrc \
    && pip install -r requirements.txt
