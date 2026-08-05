"""Durable, per-installation customer preferences (aliases + events) --
see design/user-pref.md. The first feature in this codebase that persists
across process restarts, and the first with no hub REST endpoint behind it
at all -- purely an app-level concept, so it never touches
NuCoreInterface/IoXWrapper.
"""
