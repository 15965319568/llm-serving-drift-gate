"""Additive runtime subcommands; legacy evidence CLI remains supported."""
import argparse
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in {"serve", "worker", "inspect"}:
        from ..cli import main as offline
        return offline(argv)
    parser = argparse.ArgumentParser(description="Local serving gateway and recovery laboratory")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("serve", "worker"):
        item = sub.add_parser(command)
        item.add_argument("--config", required=True)
        item.add_argument("--state", required=True)
        item.add_argument("--host", default="127.0.0.1")
        item.add_argument("--port", type=int, default=0)
        item.add_argument("--ready-file")
    inspect = sub.add_parser("inspect")
    inspect.add_argument("--state", required=True)
    inspect.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.command == "inspect":
        from .reporting import export_state
        export_state(args.state, args.output)
    elif args.command == "serve":
        from .server import serve
        serve(args.config, args.state, args.host, args.port, args.ready_file)
    else:
        from .worker import serve_worker
        serve_worker(args.config, args.state, args.host, args.port, args.ready_file)
    return 0
