"""Provisioning: `memcp up` and the stacks it stands up.

Nothing in here runs inside the memcp server. It is an operator's command on a
host, which is why no generated file mounts the Docker socket and why the server
image holds no path to the Docker daemon (G1).
"""

from memcp.deploy.model import Deployment
from memcp.deploy.stacks import BACKENDS, DEFAULT_BACKEND, StackOptions, build

__all__ = ["BACKENDS", "DEFAULT_BACKEND", "Deployment", "StackOptions", "build"]
