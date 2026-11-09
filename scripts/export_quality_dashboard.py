"""Export the two-sheet CMC quality dashboard from a finished Fast/Slow batch."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


BUILDER = Path(__file__).with_name('build_quality_dashboard.mjs')
FILENAME = 'CMC_quality_dashboard.xlsx'


def dashboard_runtime():
    bundled = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    executable = 'node.exe' if os.name == 'nt' else 'node'
    node = Path(os.environ.get('CMC_DASHBOARD_NODE', bundled/'bin'/executable)).expanduser()
    modules = Path(os.environ.get('CMC_DASHBOARD_NODE_MODULES', bundled/'node_modules')).expanduser()
    if not node.is_file() or not (modules/'@oai/artifact-tool').is_dir():
        raise FileNotFoundError('Load the workspace spreadsheet runtime or set CMC_DASHBOARD_NODE '
                                'and CMC_DASHBOARD_NODE_MODULES to its executable and node_modules paths.')
    return [str(node.resolve()), str(BUILDER.resolve()), str(modules.resolve())]


def check_dashboard_runtime():
    runtime = dashboard_runtime()
    subprocess.run(runtime, check=True, timeout=60)
    return runtime


def export_dashboard(batch, runtime, preview_dir=None):
    destination = batch/FILENAME
    # A failed export leaves the previous workbook and the saved simulation files intact.
    with tempfile.TemporaryDirectory(prefix='cmc_quality_dashboard_') as scratch:
        temporary = Path(scratch)/FILENAME
        args = [*runtime, str(batch.resolve()), str(temporary)]
        if preview_dir is not None:
            preview_dir.mkdir(parents=True, exist_ok=True)
            args.append(str(preview_dir.resolve()))
        subprocess.run(args, check=True, timeout=180)
        with tempfile.NamedTemporaryFile(dir=batch, suffix='.tmp', delete=False) as stream:
            staging = Path(stream.name)
        try:
            shutil.copyfile(temporary, staging)
            staging.replace(destination)
        finally:
            staging.unlink(missing_ok=True)
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('batch', type=Path)
    parser.add_argument('--preview-dir', type=Path)
    args = parser.parse_args()
    print(export_dashboard(args.batch.resolve(strict=True), dashboard_runtime(), args.preview_dir))
