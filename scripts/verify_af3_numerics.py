"""Compare eager/JIT arithmetic on the configured AF3 GPU, without model inference.

Diagnostic motivated by https://github.com/jax-ml/jax/issues/39336. Tiny synthetic
arrays test numerical consistency; they are not molecular prediction artifacts.
No global environment, model weights or installed dependencies are modified.
"""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from herbfold.alphafold import AF3Config, execution_environment

DIAGNOSTIC = r'''
import json
import jax
import jax.numpy as jnp
import numpy as np

rng = np.random.default_rng(42)
shapes = [(32, 15), (32,), (32, 32), (32,), (3, 32), (3,)]
parameters = tuple(jnp.asarray(rng.normal(size=s).astype('float32') * .1) for s in shapes)
times = jnp.linspace(0., 1., 100)

def network(weights, value):
    state = jnp.repeat(value, 15)
    for layer in range(3):
        state = weights[2 * layer] @ state + weights[2 * layer + 1]
        if layer < 2:
            state = jax.nn.gelu(state)
    return state

records = []
for multiplier in (.1, .5, 2., 1.):
    def evaluate(weights, inputs):
        return jax.vmap(lambda item: network(weights, item))(inputs) * multiplier
    eager = np.asarray(evaluate(parameters, times))
    compiled = np.asarray(jax.jit(evaluate)(parameters, times))
    error = float(np.max(np.abs(compiled - eager)))
    records.append({'multiplier': multiplier, 'maximum_absolute_error': error,
                    'passed': bool(np.allclose(compiled, eager, atol=1e-5, rtol=1e-4))})
print(json.dumps({'jax': jax.__version__, 'devices': [str(d) for d in jax.devices()],
                  'checks': records, 'passed': all(r['passed'] for r in records)}))
'''


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    config = AF3Config.from_env()
    results = []
    for label, flag in (("current_environment", None), ("autotune_level_3", "--xla_gpu_autotune_level=3")):
        env = execution_environment()
        if flag:
            env["XLA_FLAGS"] = flag
        process = subprocess.run([config.python_bin, "-c", DIAGNOSTIC], env=env,
                                 cwd=config.repo_dir, capture_output=True, text=True, timeout=90)
        result = {"configuration": label, "xla_flags": env.get("XLA_FLAGS"), "return_code": process.returncode}
        if process.returncode == 0:
            result.update(json.loads(process.stdout.splitlines()[-1]))
        else:
            result.update(passed=False, error=process.stderr[-2000:])
        results.append(result)
        print(json.dumps(result), flush=True)
    report = {"verified_at": datetime.now(UTC).isoformat(), "scope": "synthetic_arithmetic_diagnostic_only",
              "issue": "https://github.com/jax-ml/jax/issues/39336", "records": results}
    (root / "docs/af3-numerics-verification.json").write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
