#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, shutil, signal, subprocess, sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--timeout', type=int, required=True)
    parser.add_argument('--env', action='append', default=[])
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('a command is required')
    env = os.environ.copy()
    for item in args.env:
        if '=' not in item:
            parser.error(f'invalid --env value: {item}')
        key, value = item.split('=', 1)
        env[key] = value
    resolved = shutil.which(command[0]) or command[0]
    argv = [resolved, *command[1:]]
    windows = os.name == 'nt'
    if windows and resolved.lower().endswith(('.bat', '.cmd')):
        argv = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c', subprocess.list2cmdline(argv)]
    kwargs = {'env': env}
    if windows:
        kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs['start_new_session'] = True
    process = subprocess.Popen(argv, **kwargs)
    try:
        return process.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        if windows:
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)
        print(f'command timed out after {args.timeout}s', file=sys.stderr)
        return 124


if __name__ == '__main__':
    raise SystemExit(main())
