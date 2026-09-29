# A pinned OpenHands, so the client-conformance check is reproducible.
#
# Why a container: the check is "does a real client load our generated skills?",
# and without pinning it answers a question about whatever version happened to be
# installed. A venv on the host drifts, disappears, and silently changes the
# answer. The image is the version, so the assertion is about the version too.
#
# Why this is NOT what unblocks VS Code: code-server has the agent-plugins system
# (chat.pluginLocations, agentPluginsHome, componentPaths are all present) but its
# workbench is lazy-loaded and only starts on a browser connection. Driving that
# headlessly needs browser automation AND the chat subsystem to activate, which
# needs the Copilot extension and an authenticated session. The blocker is
# authentication, not installation — a container does not help.
FROM python:3.12-slim

RUN pip install --no-cache-dir "openhands-ai==1.11.0" "openhands-sdk==1.34.0"

# The generated plugin, mounted at runtime. Read-only: the check must not be able
# to modify what it is checking.
ENV OPENHANDS_SUPPRESS_BANNER=1
WORKDIR /check
