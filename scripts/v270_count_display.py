"""Retired one-procurement display override.

V352 now computes distinct saved legal episodes for every procurement. A
hard-coded 2 -> 1 rewrite would suppress a second genuine event and disagree
with the filter total. Keep the installer for old startup imports, but never
rewrite counters or evidence. Deduplication belongs to the shared reconciler.
"""
def install(ns):
    print('V270_OVERRIDE_RETIRED count_basis=unique_saved_legal_episodes',flush=True)
