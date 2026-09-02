"""Runs every probe and says, in one exit code, whether the pages still work.

⚠ THIS IS THE SAME COMMAND CI RUNS AND THE ONE A PERSON RUNS. That is the
whole point of it existing instead of two lines of YAML: a bench that runs
something slightly different from what the release runs is a bench that lies,
and this repository has already paid for one of those.

    export PYTHONPATH=<mcp-common-engine, from the pinned tag>
    python3.12 probes/run.py

What it refuses to call a pass:

  - a probe that exits non-zero — the obvious one;
  - a probe that exits ZERO having printed fewer PASS lines than it has `ok()`
    calls in its own source. A control that does not count what it watches can
    go green for lack of work, and a probe killed halfway prints a screen of
    passes before it dies. The floor is READ FROM THE FILE, never written down
    here: loops make the real count higher than the source count, so the
    comparison is `printed >= defined` and it still catches a probe that
    stopped early or was gutted;
  - a run that found fewer than two probes. A glob that matches nothing
    succeeds at everything, which is the quietest way for this job to become
    decorative.

`shots.py` is deliberately NOT picked up: only `probe_*.py` is. It renders the
pages in a browser for a person to look at and has no verdict to give, so it
has no business in a gate.
"""
import ast
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FLOOR = 2                      # probe_ui and probe_link. Fewer means something
                               # was removed, or the glob stopped matching.


def declared(path):
    """How many cases the file CLAIMS, counted in its source and not trusted
    from its output. Loops can print more than this; nothing can honestly
    print less."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    return sum(1 for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "ok")


def main():
    probes = sorted(glob.glob(os.path.join(HERE, "probe_*.py")))
    if len(probes) < FLOOR:
        print(f"FAIL  found {len(probes)} probes in {HERE}, expected at least "
              f"{FLOOR} — a glob that matches nothing passes everything")
        return 1

    verdicts = []
    for path in probes:
        name = os.path.basename(path)
        print(f"\n=== {name} " + "=" * (60 - len(name)))
        run = subprocess.run([sys.executable, path], capture_output=True,
                             text=True)
        sys.stdout.write(run.stdout)
        sys.stderr.write(run.stderr)

        want = declared(path)
        got = sum(1 for l in run.stdout.splitlines() if l.strip().startswith("PASS"))
        tail = [l for l in run.stdout.splitlines() if l.strip()]
        said = tail[-1].strip() if tail else ""

        why = []
        if run.returncode != 0:
            why.append(f"exit={run.returncode}")
        if said != "all green":
            why.append(f"last line was {said!r}, not 'all green'")
        if got < want:
            why.append(f"printed {got} passes but the source has {want} cases "
                       f"— it did not reach the end")
        verdicts.append((name, want, got, why))

    print("\n" + "=" * 68)
    for name, want, got, why in verdicts:
        mark = "ok  " if not why else "FAIL"
        print(f"  {mark}  {name}: {got} passed, {want} cases in source"
              + ("" if not why else "  — " + "; ".join(why)))
    bad = [n for n, _, _, why in verdicts if why]
    print(f"\n{'PROBES FAILED: ' + ', '.join(bad) if bad else 'probes all green'}")
    return 1 if bad else 0


sys.exit(main())
