import argparse
import json
import os
from pathlib import Path


def main():
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser(description="HerbFold research workstation")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default=os.getenv("HERBFOLD_HOST", "127.0.0.1"))
    serve.add_argument("--port", type=int, default=os.getenv("HERBFOLD_PORT", "9018"))
    commands.add_parser("doctor")
    demo = commands.add_parser("demo")
    demo.add_argument("--output", default="runtime/demo.json")
    from .discovery_cli import add_parser

    add_parser(commands)
    args = parser.parse_args()
    if args.command == "discovery":
        from .discovery_cli import run

        run(args)
    elif args.command == "serve":
        if args.host not in ("127.0.0.1", "localhost", "::1") and not os.getenv("HERBFOLD_API_TOKEN"):
            parser.error("Set HERBFOLD_API_TOKEN and HERBFOLD_ALLOWED_HOSTS before binding remotely")
        import uvicorn

        uvicorn.run("herbfold.api:create_app", host=args.host, port=args.port, factory=True)
    elif args.command == "doctor":
        from . import alphafold, quantum

        print(
            json.dumps(
                {"alphafold": alphafold.capabilities(), "quantum": quantum.inspect_backends()}, indent=2
            )
        )
    else:
        from . import chemistry, quantum

        compounds = chemistry.load_catalog()
        herbal = next(x for x in compounds if x["category"] == "herbal")
        drug = next(x for x in compounds if x["category"] == "drug")
        result = {
            "comparison": chemistry.compare_compounds([herbal, drug]),
            "candidates": chemistry.generate_candidates([herbal["smiles"], drug["smiles"]], 8),
            "quantum": quantum.local_kernel([[0.1, 0.2], [0.2, 0.3]], n_qubits=4),
            "note": "Real local computations. No AF3 inference, measured affinity, or quantum advantage is claimed.",
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"Wrote {output}: {len(result['candidates'])} candidate structures")


if __name__ == "__main__":
    main()
