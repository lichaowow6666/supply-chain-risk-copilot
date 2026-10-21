"""PE Investment Copilot package initializer.
This package groups agents, analytics, and UI helpers.
"""

__version__ = "0.1.0"

# Do not auto-import subpackages to avoid heavy initialization on simple imports.
# Users may import specific submodules as needed, e.g. `from pe_copilot.agents import MarketAgent`.
__all__ = ["agents", "analytics", "ui"]
