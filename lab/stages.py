"""Stage entry points for lab.py, plus the whole-repo selftest."""
import importlib
import os

from lab import core

_MODS = {"ablate": "ablate", "autofreeze": "autofreeze replan", "dashboard": "dashboard", "data": "snapshot",
         "citations": "citations", "concepts": "concepts", "helix": "helix_citations helix_sync", "kanban": "kanban_sync",
         "kanban_up": "kanban_down kanban_up", "venue": "venue venue_profile", "methods": "methods_scan methods_review",
         "lineage": "lineage", "kg": "kg", "defects": "defects defect_set", "combine": "combine",
         "papers": "paper_pivot paper_set paper_status venue_fit", "read": "read", "meta": "meta_adopt meta_improve meta_init",
         "campaign": "campaign", "critic": "critic", "discriminate": "discriminate hypotheses",
         "experiment": "analyze experiment plan", "fetchdata": "fetch_data", "final": "final", "ideate": "ideate novelty preregister", "inspect_run": "inspect",
         "panel": "panel", "paper": "paper", "posthoc": "posthoc",
         "program": "program_add program_board program_digest program_init program_next program_run",
         "readfield": "read_field", "report": "review write", "revise": "revise", "survey": "screen survey", "tree": "explore"}
_STAGE = {fn: mod for mod, fns in _MODS.items() for fn in fns.split()}


def __getattr__(name):  # stage functions load on first use, so `next` and `status` skip the stage modules
    if name not in _STAGE:
        raise AttributeError(name)
    return getattr(importlib.import_module(f"lab.{_STAGE[name]}"), name)


def selftest():
    os.environ["LAB_KANBAN_AUTO"] = "0"  # selftests must not touch the real board
    os.environ["LAB_REPLAY"] = "0"  # nor write snapshots into the real workspace (the replay selftest turns it on itself)
    from lab import roles, data, meta, dashboard as dash, kanban, read as reading, papers, helix, methods, defects as defects_mod, combine as combine_mod, predict as predict_mod
    from lab import lineage as lineage_mod
    from lab import kg as kg_mod
    from lab import replay as replay_mod
    from lab import isolate, kanban_up as kanban_up_mod, venue as venue_mod
    from lab import (ablate, autofreeze, campaign, critic, experiment, final, ideate, inspect_run, panel, paper, posthoc,
                     program, report, revise, survey, tree, readfield, fetchdata)
    from lab import discriminate as disc
    from lab import cli
    from lab import attach as attach_mod
    print(core.rloop("selftest").strip())
    for m in (roles, data, survey, readfield, fetchdata, ideate, critic, autofreeze, experiment, tree, ablate, final, posthoc, disc, report, paper, panel, revise,
              inspect_run, program, campaign, papers, dash, kanban, reading, helix, methods, lineage_mod, kg_mod, replay_mod, defects_mod, combine_mod, predict_mod,
              isolate, kanban_up_mod, venue_mod, core, cli, attach_mod):
        m.selftest()
    print("all selftests ok")
