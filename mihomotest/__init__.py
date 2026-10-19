"""Test every server in a running mihomo, exactly as mihomo uses it.

Modules: system (platform helpers), mihomo (finding and driving mihomo), tests (the per-server test and
the run loop), report (Markdown/CSV), cli (the terminal interface). The run loop reports progress as
events, so any interface can show it.
"""

__version__ = "0.1.0"
