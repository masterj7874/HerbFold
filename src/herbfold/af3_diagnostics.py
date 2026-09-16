"""Read-only confidence diagnostics bound to one registered AF3 structure.

No inference, dataset search, output repair or parameter inspection happens here.
The output registry authenticates the structure hash, but older confidence JSONs
have no acquisition-time hash. Their consistency checks are explicitly narrower
than independent authentication of their provenance.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import PurePosixPath

import numpy as np
from fastapi import HTTPException

from . import molecular_selection

MAX_CONFIDENCE_BYTES = 32 * 1024 * 1024
MAX_PAE_TOKENS = 2048
OUTPUT_DOC = "https://github.com/google-deepmind/alphafold3/blob/v3.0.4/docs/output.md"


def _stats(values):
    values = np.asarray(values, dtype=float)
    if values.size == 0:
        return {"count": 0, "min": None, "mean": None, "max": None}
    return {
        "count": int(values.size),
        "min": float(values.min()),
        "mean": float(values.mean()),
        "max": float(values.max()),
    }


def _unavailable(reason):
    return {"status": "unavailable", "reason": reason, "token_count": None, "chain_pairs": []}


def _relative_artifact(relative):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError("Invalid registered output artifact")
    path = PurePosixPath(relative)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("Registered output artifact must stay inside this job output")
    return "output/" + path.as_posix()


def _pae(store, job_id, model, scene):
    relative = model.get("confidence_path")
    if not relative:
        return _unavailable("이 sample에는 등록된 전체 confidence JSON이 없습니다.")
    try:
        artifact = _relative_artifact(relative)
        path = store.artifact(job_id, artifact)
        structure = store.artifact(job_id, scene["metadata"]["artifact"])
        expected_name = structure.name.removesuffix("_model.cif") + "_confidences.json"
        if path.parent != structure.parent or path.name != expected_name:
            raise ValueError("Confidence JSON is not the registered structure's same-sample sibling")
        before = path.stat()
        if before.st_size > MAX_CONFIDENCE_BYTES:
            raise ValueError("Confidence JSON exceeds the 32 MiB diagnostics limit")
        with path.open("rb") as stream:
            raw = stream.read(MAX_CONFIDENCE_BYTES + 1)
        after = path.stat()
        if (
            len(raw) != before.st_size
            or len(raw) > MAX_CONFIDENCE_BYTES
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino)
        ):
            raise ValueError("Confidence artifact changed while reading")
        digest = hashlib.sha256(raw).hexdigest()
        registered_hash = model.get("confidence_sha256")
        if registered_hash and registered_hash != digest:
            raise ValueError("Confidence artifact checksum differs from its registered digest")
        data = json.loads(raw)
        atoms = scene["atoms"]
        atom_chains = data.get("atom_chain_ids")
        if atom_chains != [atom["chain_id"] for atom in atoms]:
            raise ValueError("Confidence atom chain order/count differs from the selected structure")
        atom_values = data.get("atom_plddts")
        if not isinstance(atom_values, list) or len(atom_values) != len(atoms):
            raise ValueError("Confidence pLDDT atom count differs from the selected structure")
        if any(type(value) not in (int, float) for value in atom_values):
            raise ValueError("Confidence pLDDT contains nonnumeric atom values")
        values = np.asarray(atom_values, dtype=float)
        observed = np.asarray([atom["confidence"] for atom in atoms], dtype=float)
        if (
            not np.isfinite(values).all()
            or not np.isfinite(observed).all()
            or (values < 0).any()
            or (values > 100).any()
            or not np.allclose(values, observed, rtol=0, atol=0.011)
        ):
            raise ValueError("Confidence atom pLDDT differs from the selected mmCIF values")
        chains, residues = data.get("token_chain_ids"), data.get("token_res_ids")
        if (
            not isinstance(chains, list)
            or not 1 <= len(chains) <= MAX_PAE_TOKENS
            or not all(isinstance(chain, str) and chain for chain in chains)
            or not isinstance(residues, list)
            or len(residues) != len(chains)
            or not all(type(residue) is int and residue > 0 for residue in residues)
        ):
            raise ValueError("Confidence token labels are invalid or exceed the 2048-token limit")
        expected_tokens, protein_residues = Counter(), set()
        for atom in atoms:
            key = (atom["chain_id"], atom["residue_id"])
            if atom["is_protein"]:
                protein_residues.add(key)
            elif atom["element"] not in {"H", "D"}:
                expected_tokens[key] += 1
        expected_tokens.update(protein_residues)
        if Counter(zip(chains, map(str, residues), strict=True)) != expected_tokens:
            raise ValueError("Confidence token chain/residue multiplicities differ from the structure")
        matrix = data.get("pae")
        count = len(chains)
        if (
            not isinstance(matrix, list)
            or len(matrix) != count
            or any(not isinstance(row, list) or len(row) != count for row in matrix)
            or any(type(value) not in (int, float) for row in matrix for value in row)
        ):
            raise ValueError("PAE must be a numeric square matrix matching the token labels")
        matrix = np.asarray(matrix, dtype=float)
        if not np.isfinite(matrix).all() or (matrix < 0).any():
            raise ValueError("PAE contains a nonfinite or negative predicted error")
        chain_order = list(dict.fromkeys(chains))
        chain_indices = {chain: np.flatnonzero(np.asarray(chains) == chain) for chain in chain_order}
        selected = scene["metadata"]["selection"]
        pairs = []
        for frame in chain_order:
            for target in chain_order:
                summary = _stats(matrix[np.ix_(chain_indices[frame], chain_indices[target])])
                pairs.append(
                    {
                        "frame_chain": frame,
                        "target_chain": target,
                        **summary,
                        "frame_is_target": frame in selected["target_chains"],
                        "target_is_ligand": target in selected["ligand_chains"],
                    }
                )
        summary_min = model.get("metrics", {}).get("chain_pair_pae_min")
        summary_chains = model.get("chain_ids")
        summary_checked = summary_min is not None and summary_chains is not None
        if summary_checked:
            if summary_chains != chain_order or not np.allclose(
                np.asarray(summary_min, dtype=float),
                np.asarray([pair["min"] for pair in pairs]).reshape(len(chain_order), -1),
                rtol=0,
                # AF3 writes full PAE at 1 decimal place, summary minima at
                # 2 decimals. Their independent rounding can differ by 0.055 Å.
                atol=0.055001,
            ):
                raise ValueError("PAE chain minima disagree with this sample's registered summary")
        return {
            "status": "available",
            "reason": None,
            "token_count": count,
            **_stats(matrix),
            "units": "angstrom",
            "aggregation": "Unweighted summaries of the serialized full PAE matrix (including diagonal cells); not contact-weighted interface scores",
            "serialized_pae_precision_angstrom": 0.1,
            "chain_pairs": pairs,
            "artifact": artifact,
            "sha256": digest,
            "size_bytes": len(raw),
            "identity_checks": {
                "atom_chain_order": True,
                "atom_plddt_matches_cif": True,
                "token_chain_residue_counts": True,
                "summary_chain_minima": summary_checked,
                "registered_confidence_sha256": bool(registered_hash),
            },
            "integrity_scope": (
                "Selected structure SHA verified against its registry; confidence bytes hashed on read, "
                "with same-sample filename, atom pLDDT and token mapping consistency checks. "
                + (
                    "Confidence SHA also matches its registry."
                    if registered_hash
                    else "No acquisition-time confidence SHA was registered; these checks do not independently authenticate its origin."
                )
            ),
        }
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
        return _unavailable(str(exc))


def selected_diagnostics(store, job_id, requested, *, file=None, smiles=None, target_accession=None):
    """Return one exact sample's diagnostics; mismatched UI selection is HTTP 409."""
    canonical = requested["canonical_smiles"]
    accession = requested["target_accession"]
    if smiles is not None and molecular_selection.exact_identity(smiles) != canonical:
        raise HTTPException(409, detail="선택 분자가 이 AF3 작업의 정확한 화학적 정체성과 다릅니다.")
    if (
        target_accession is not None
        and target_accession.strip().upper() != accession
        and molecular_selection.canonical_target_accession(store, target_accession) != accession
    ):
        raise HTTPException(409, detail="선택 표적이 이 AF3 작업의 표적과 다릅니다.")
    job = store.get(job_id)
    result = job.get("result") or {}
    response = {
        "schema_version": 1,
        "job_id": job_id,
        "status": "unavailable",
        "reason": None,
        "structure_sha256": None,
        "artifact": None,
        "requested": {
            key: requested.get(key)
            for key in ("canonical_smiles", "target_accession", "target_sequence_sha256")
        },
        "execution_verified": result.get("execution_verified") is True,
        "identity_verified": False,
        "summary_metrics": {},
        "chain_ids": [],
        "plddt": None,
        "pae": _unavailable("선택 구조의 정체성 검증 후에만 PAE를 표시합니다."),
        "notes": [
            "pLDDT는 원자별 0–100 구조 신뢰도이며 리간드 원자의 경우 폴리머와의 거리 오차를 대상으로 합니다. 리간드 내부 기하 검증 점수가 아닙니다.",
            "PAE는 행(frame_chain)에 정렬했을 때 열(target_chain)의 상대 위치·방향 예상 오차(Å)입니다. 방향별 평균과 최소값은 서로 다른 요약입니다.",
            "pTM/ipTM은 전체 구조/서브유닛 상대 배치 신뢰도입니다. 20 tokens 미만의 짧은 체인 pTM은 크기 효과가 커서 별도 품질 판정에 사용하지 않습니다.",
            "has_clash=false는 AF3의 큰 충돌 기준이 감지되지 않았다는 뜻이며 모든 원자 충돌이 없다는 증명이 아닙니다.",
            "ranking_score는 samples 순위용입니다. 최상위 파일은 같은 sample의 사본이며 추가 독립 예측이 아닙니다.",
            "MSA 행 수는 독립 상동 서열 수·Neff가 아닙니다. 구조 신뢰도는 결합 친화도·효능·안전성 검증이 아닙니다.",
        ],
        "sources": [{"title": "AlphaFold 3 v3.0.4 output definitions", "url": OUTPUT_DOC}],
    }
    if job["status"] != "completed" or result.get("prediction_eligible") is False:
        response["reason"] = "완료된 예측 사용 가능 작업에만 구조 진단을 제공합니다."
        return response
    sequence = molecular_selection.frozen_target_sequence(job, requested)
    if not sequence:
        response["reason"] = "이 작업에 저장된 표적 서열 해시와 원래 AF3 입력 서열이 일치하지 않습니다."
        return response
    models = result.get("models") or []
    if not models:
        response["reason"] = "등록된 AF3 구조가 없습니다."
        return response
    if file is not None:
        matches = [model for model in models if _relative_artifact(model["structure_path"]) == file]
        if len(matches) != 1:
            raise HTTPException(
                409, detail="선택 파일이 이 작업에 등록된 단일 구조 sample과 일치하지 않습니다."
            )
        model = matches[0]
    else:
        model = min(models, key=lambda row: not row.get("is_top_ranked_copy", False))
    # Limit the existing identity resolver to exactly this sample. It still reads
    # the original registry's hash and verifies the complete observed chain and
    # ligand atom/bond graph. It cannot fall back to another sample or job.
    scoped_job = {**job, "result": {**result, "models": [model]}}
    scene, _, _, _, _ = molecular_selection._resolve_saved(
        store, canonical, accession, sequence, jobs=[scoped_job]
    )
    if scene is None:
        response["reason"] = (
            "선택 sample의 원본 해시·전체 표적 서열·리간드 그래프 일치 검증을 통과하지 못했습니다."
        )
        return response
    selected_atoms = set(scene["metadata"]["selected_ligand_atom_ids"])
    atoms = scene["atoms"]
    selection = scene["metadata"]["selection"]
    groups = {
        "all": atoms,
        "protein": [
            atom for atom in atoms if atom["is_protein"] and atom["chain_id"] in selection["target_chains"]
        ],
        "selected_ligand": [atom for atom in atoms if atom["id"] in selected_atoms],
    }
    response.update(
        status="available",
        identity_verified=True,
        artifact=scene["metadata"]["artifact"],
        structure_sha256=scene["metadata"]["sha256"],
        summary_metrics=model.get("metrics", {}),
        chain_ids=model.get("chain_ids") or [chain["id"] for chain in scene["chains"]],
        selection=selection,
        geometry=scene["geometry"],
        plddt={
            name: _stats([atom["confidence"] for atom in group if atom["confidence"] is not None])
            for name, group in groups.items()
        },
        pae=_pae(store, job_id, model, scene),
        sample={key: model.get(key) for key in ("seed", "sample", "is_top_ranked_copy")},
        samples=[
            {
                "artifact": _relative_artifact(row["structure_path"]),
                **{
                    key: row.get(key)
                    for key in ("seed", "sample", "is_top_ranked_copy", "structure_sha256", "metrics")
                },
            }
            for row in models
        ],
    )
    return response
