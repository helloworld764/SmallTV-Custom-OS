"""Read-only CLI capability probe. Never starts a task or reads Codex auth/config."""

import json
import re
import shutil
import subprocess


TIMEOUT_SECONDS = 10
MAX_OUTPUT_CHARS = 65536
VERSION = re.compile(r'^codex(?:-cli)?\s+(\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?)\s*$', re.MULTILINE)


def probe_cli():
    report = {
        'schema_version': 1,
        'status': 'not_found',
        'cli_version': None,
        'checks': {'version': False, 'exec_help': False, 'json_flag': False,
                   'sandbox_flag': False, 'read_only_mode': False},
        'real_run_checked': False,
    }
    executable = shutil.which('codex')
    if executable is None:
        return report
    report['status'] = 'probe_failed'
    outputs = []
    for arguments in (['--version'], ['exec', '--help']):
        try:
            result = subprocess.run(
                [executable, *arguments], stdin=subprocess.DEVNULL,
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=TIMEOUT_SECONDS, shell=False, check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return report
        # Never copy stdout, stderr, executable paths or exception text to reports.
        if result.returncode != 0 or len(result.stdout) > MAX_OUTPUT_CHARS or len(result.stderr) > MAX_OUTPUT_CHARS:
            return report
        outputs.append(result.stdout)
    version = VERSION.search(outputs[0])
    if version is not None:
        report['cli_version'] = version.group(1)
        report['checks']['version'] = True
    report['checks']['exec_help'] = True
    help_text = outputs[1]
    for name, flag in [('json_flag', '--json'), ('sandbox_flag', '--sandbox')]:
        report['checks'][name] = bool(re.search(r'(?<![\w-])' + re.escape(flag) + r'(?![\w-])', help_text))
    report['checks']['read_only_mode'] = bool(re.search(r'(?<![\w-])read-only(?![\w-])', help_text))
    report['status'] = 'manual_verification_required' if all(report['checks'].values()) else 'incompatible_or_unknown'
    return report


def main():
    report = probe_cli()
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return 0 if report['status'] == 'manual_verification_required' else 1


if __name__ == '__main__':
    raise SystemExit(main())
