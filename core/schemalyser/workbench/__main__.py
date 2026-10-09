"""python -m schemalyser.workbench --project FOLDER [--port 8765]"""
import argparse
import sys
from pathlib import Path

# The file that Docker writes at the root of every container it runs. The workbench listens on 0.0.0.0 only where this
# file exists, which is inside the image of tools/workbench, whose port compose publishes on 127.0.0.1 alone.
CONTAINER_MARKER = Path("/.dockerenv")
REFUSED_HOST = ("The workbench listens on 0.0.0.0 only inside its container, where Docker writes /.dockerenv, and this "
                "computer has no such file. Run it with --host 127.0.0.1, or run the image in tools/workbench.")


def host_allowed(host):
    """Whether the workbench may listen on host: 127.0.0.1 always, and 0.0.0.0 only inside a container."""
    return host == "127.0.0.1" or (host == "0.0.0.0" and CONTAINER_MARKER.is_file())


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m schemalyser.workbench",
                                     description="The local workbench over a project folder, served on this computer only.")
    parser.add_argument("--project", required=True, type=Path, help="the project folder, which is made if it does not exist")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "0.0.0.0"),
                        help="127.0.0.1, or 0.0.0.0 inside the container only, which /.dockerenv marks and whose port "
                             "compose publishes on 127.0.0.1")
    parser.add_argument("--describe", type=Path, help="a built copy of the page Describe the record, served at /describe/")
    args = parser.parse_args(argv)
    if not host_allowed(args.host):
        print(REFUSED_HOST, file=sys.stderr)
        return 2
    import uvicorn

    from .app import create_app
    app = create_app(args.project, describe_folder=args.describe)
    print(f"The workbench is serving {args.project.resolve().name} at http://127.0.0.1:{args.port}/", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", server_header=False, proxy_headers=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
