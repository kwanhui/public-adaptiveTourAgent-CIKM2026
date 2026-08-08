"""`python3 -m adaptivetouragent.app`: start the demo UI server."""

import argparse

import uvicorn


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="adaptivetouragent.app")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--reload", action="store_true")
    args = p.parse_args(argv)

    uvicorn.run(
        "adaptivetouragent.ui.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
