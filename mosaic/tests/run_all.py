"""Run every test suite in this folder; exit non-zero if any fails.

    python mosaic/tests/run_all.py                 every suite
    python mosaic/tests/run_all.py golden lrgb     only suites whose file name contains one of these words

Each suite runs as its own process, because they are scripts with their own entry points (review_regressions
keeps its print-as-you-go runner). New suites are picked up automatically; list library modules in NOT_SUITES.
"""
from pathlib import Path
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
NOT_SUITES = {'run_all.py', 'synthetic.py'}


def suites(words):
    found = sorted(p for p in HERE.glob('*.py') if p.name not in NOT_SUITES and not p.name.startswith('_'))
    return [p for p in found if not words or any(w in p.stem for w in words)]


def main():
    chosen = suites(sys.argv[1:])
    if not chosen:
        sys.exit('No suite matches ' + ' '.join(sys.argv[1:]))
    failed = []
    for path in chosen:
        t0 = time.time()
        done = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, cwd=str(HERE.parents[1]))
        ok = done.returncode == 0
        print(f'{"ok    " if ok else "FAILED"}  {path.stem:22s} {time.time() - t0:6.1f} s', flush=True)
        if not ok:
            failed.append(path.stem)
            print('\n'.join('        ' + line for line in (done.stdout + done.stderr).strip().splitlines()[-25:]), flush=True)
    print(f'\n{len(chosen) - len(failed)} of {len(chosen)} suites passed' + (f'; failed: {", ".join(failed)}' if failed else ''))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
