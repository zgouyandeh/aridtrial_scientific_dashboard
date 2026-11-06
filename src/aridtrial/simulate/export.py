"""Backward-compatible entry point for synthetic dataset generation.

New code should import ``build`` from ``aridtrial.simulate.builder``. This module
remains as a stable import path for the CLI, existing scripts and test suite.
"""
from __future__ import annotations

from .builder import build

__all__ = ["build"]
