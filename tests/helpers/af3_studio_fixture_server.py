"""Isolated browser contract server: synthetic CPU outputs, never AF3 predictions.

Only runs in a new directory under project tmp/. Never points at production data.
The conspicuous page banner and fixture endpoint identify the test environment.
"""

import argparse
import hashlib
import runpy
import sys
from pathlib import Path

import uvicorn
from fastapi.responses import HTMLResponse

from herbfold import af3_databases, af3_parameters, alphafold, molecular_selection
from herbfold.api import create_app


def main():
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, default=8875)
    args = parser.parse_args()
    directory = Path(args.data_dir).resolve()
    if not directory.is_relative_to(root / "tmp") or directory.exists():
        parser.error("Fixture data must use a NEW directory inside project tmp/")
    runner_dir = directory / "SYNTHETIC_CPU_TEST_NOT_AF3"
    runner_dir.mkdir(parents=True)
    runner = runpy.run_path(str(root / "tests/test_af3_studio.py"))["SYNTHETIC_RUNNER"]
    runner = runner.replace('mode = os.environ.get("SYNTHETIC_TEST_RUNNER_MODE", "ok")',
                            'mode = "failed" if data["modelSeeds"] == [13] else "delay"')
    runner = runner.replace("time.sleep(30)", "time.sleep(6)")
    (runner_dir / "run_alphafold.py").write_text(runner)
    config = alphafold.AF3Config(repo_dir=runner_dir, python_bin=sys.executable,
                                 model_dir=runner_dir, database_dir=runner_dir, device="cpu")
    alphafold.AF3Config.from_env = classmethod(lambda cls: config)
    alphafold.capabilities = lambda *a, **kw: {
        "ready": True, "runnable": True, "blockers": [],
        "warnings": ["SYNTHETIC CPU CONTROL-FLOW TEST — NOT A MOLECULAR PREDICTION"],
        "provenance": {"test_fixture": True, "scientific_results": False,
                       "parameters": {"stat_fingerprint_sha256": "synthetic-browser-test-only"},
                       "databases": {"fingerprint_sha256": "f" * 64}},
    }
    af3_parameters.inspect_parameters = lambda config: {
        "status": "test_fixture_only", "runnable": True, "blockers": [],
        "provenance": {"stat_fingerprint_sha256": "synthetic-browser-test-only"},
    }
    af3_databases.inspect_databases = lambda config: {
        "status": "test_fixture_only", "runnable": True, "blockers": [],
        "provenance": {"fingerprint_sha256": "f" * 64},
    }
    molecular_selection._target_sequence = lambda accession: "ACD" if accession == "P35354" else None
    molecular_selection.registered_target = lambda store, accession: {
        "accession": "P35354", "sequence": "ACD",
        "sequence_sha256": hashlib.sha256(b"ACD").hexdigest(),
        "name": "Synthetic CPU browser fixture target", "length": 3,
        "source": "synthetic_test_fixture",
    } if accession == "P35354" else None
    app = create_app(directory / "journal")

    @app.middleware("http")
    async def fixture_banner(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" and response.status_code == 200:
            body = b"".join([chunk async for chunk in response.body_iterator]).decode()
            banner = (
                '<div id="af3-test-banner" style="position:fixed;top:0;left:0;right:0;z-index:99999;'
                'background:#ffe25e;color:#241d00;font:700 13px sans-serif;text-align:center;padding:8px;'
                'pointer-events:none">SYNTHETIC CPU CONTROL-FLOW TEST · NOT A SCIENTIFIC PREDICTION</div>'
            )
            return HTMLResponse(body.replace("<body>", "<body>" + banner))
        return response

    @app.get("/api/test-fixture")
    def fixture_identity():
        return {"test_fixture": True, "scientific_results": False, "gpu_execution": False}

    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
