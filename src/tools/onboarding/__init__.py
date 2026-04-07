"""Onboarding tools: user profile management + secretary workspace/dispatch tools."""

from src.tools.secretary.tools import manage_workspaces, ptc_agent
from src.tools.user_profile import USER_PROFILE_TOOLS

ONBOARDING_TOOLS = [*USER_PROFILE_TOOLS, manage_workspaces, ptc_agent]

__all__ = [
    "manage_workspaces",
    "ptc_agent",
    "ONBOARDING_TOOLS",
]
