# Molecular scenes and atom connectivity

> 2026-09-08 parameter audit correction: the previously displayed ibuprofen smoke
> output is now quarantined (`prediction_eligible=false`). The previously configured parameter
> file matches the upstream random performance-test signature, so it cannot support
> biological predictions. Raw artifacts remain inspectable as excluded test output.
> Historical viewer checks below describe software behavior at their recorded time;
> they do not validate trained model inference. See [parameter evidence](af3-parameter-audit.json)
> and [corrected AF3 execution documentation](alphafold.md).

The active parameter path has since been changed to an archive downloaded directly
from the Google URL in the official v3.0.4 README. Its published CRC32C and full
container schema checks passed. The old smoke remains excluded. See the
[acquisition receipt](af3-google-weights-acquisition.json),
[independent validation](af3-google-weights-validation.json), and
[separate selected-compound job records](af3-trained-selected-predictions.json).

Both selected jobs subsequently completed real inference, producing five unique
samples each. An independent atom-name audit found RDKit's ILE CG2–CD1 template
incompatible with the official CCD naming used by the observed coordinates. The
viewer now uses the official CG1–CD1 edge and labels its source accordingly. This
removed 34 false long-bond warnings per top model without changing any coordinate
or original artifact hash. Zero flagged lengths is not a structural accuracy pass;
the MSA/template-free models have low confidence. See the
[standard-residue audit](af3-standard-residue-template-audit.json) and
[preserved validation correction](af3-ile-template-correction.json).

The molecular viewer consumes a single JSON scene format for generated compounds,
saved AlphaFold 3 complexes, and experimentally deposited complexes. Every scene
declares its coordinate source. A free-molecule conformer or experimental reference
is never labelled as an AlphaFold prediction for a candidate.

## Endpoints

| Endpoint | Result |
| --- | --- |
| `POST /api/molecular/conformer` | Seeded RDKit 3D conformer, exact input molecular graph, atom properties, and optimization status |
| `POST /api/molecular/sdf` | Downloadable 3D SDF with provenance, input SMILES, energy method and convergence notes |
| `POST /api/molecular/resolve` | Resolve the selected exact ligand and target to an observed experimental complex or registered saved AF3 result; explicit unavailable response when no match is verified |
| `GET /api/molecular/references?target_accession=P35354` | Registered experimental ligands available for explicit selection, including their actual chemical identities and protein construct qualifications |
| `GET /api/molecular/jobs/{job_id}/scene?file=output/…/model.cif` | A registered, checksum-verified mmCIF from an AlphaFold execution, smoke run, or import job; omitting `file` selects a top-ranked saved model |
| `GET /api/molecular/samples/ptgs2` | An existing PTGS2 AlphaFold job from this installation, or HTTP 404 when none exists |
| `GET /api/molecular/reference/5IKR` | RCSB experimental human COX-2 / mefenamic-acid complex, cached locally with its component bond definitions |

The conformer and SDF endpoints accept:

```json
{
  "smiles": "CC(=O)Oc1ccccc1C(=O)O",
  "seed": 42,
  "include_hydrogens": true,
  "num_conformers": 4
}
```

The molecular studio uses the resolver for both complex modes:

```json
{
  "smiles": "CC(C)Cc1ccc(C(C)C(=O)O)cc1",
  "source": "alphafold3_prediction",
  "target_accession": "P35354"
}
```

`source` can also be `experimental_pdb`. The response contains `status`,
`requested` (including canonical isomeric SMILES), `scene`, `matches`, and
`available_references`. An unavailable result has `scene=null` and a reason.
The compatibility sample and fixed-reference routes above remain available for
explicit reference access; the studio never uses them to identify a selection.

## Selecting a compound and complex

Changing a compound retains the chosen structure mode and queries that compound's
SMILES with the selected target. The old scene is cleared immediately. Request
cancellation and a monotonically increasing request number prevent a late response
for the previous compound from replacing the current selection.

Matching retains charge and specified/unspecified stereochemistry. It does not
neutralize, remove salts, merge tautomers, or infer identity from a job name. Saved
AF3 output must have the actual selected ligand's complete named atom/bond graph
and the full observed target sequence. Registered paths and checksums remain
mandatory; imported files still carry their unverified execution status. The saved
lookup pages through completed AF3 jobs independently of unrelated recent jobs,
with a declared 5,000-job bound, at most 20 registered models per job, and 50
artifact checks in total. Truncated searches disclose the bound and leave older
unexamined results explicitly unresolved.

Experimental matching uses the packaged, dated human PTGS2 registry and validates
the downloaded PDB identity, method, UniProt entity mapping, experimental construct
sequence hash, CCD chemical identity, and actual ligand atom/bond graph. This is a
bounded registry, not a live exhaustive search for arbitrary targets. Experimental
truncations and sequence differences remain in the scene metadata. See
[the primary-source research](molecular-selection-sources.md).

The selector labelled **실험 구조 등록 물질** explicitly changes the active compound
to a deposited ligand. Mefenamic acid selects 5IKR; tolfenamic acid selects 5IKT.
Selecting aspirin does not reuse these structures, an acetylated-protein structure,
or a salicylate ligand. Mouse structures are not silently substituted for the human
target. If an exact complex is unavailable, the studio offers the selected
compound's free conformer and preparation of a new AF3 input containing exactly
that compound and the requested UniProt sequence. Input preparation does not run
AF3 or produce a prediction.

Matched complex scenes add `metadata.selected_ligand_atom_ids` and
`metadata.selection` with the exact chemical identity, target and ligand chains.
The viewer automatically frames one complete matching ligand instance. **선택
물질에 초점** restores that view; **전체 구조 맞춤** fits the full complex. Existing
zoom, rotation, representations, atom inspection and coordinate export continue to
use the original deposited/predicted coordinates. The camera changes, not atoms.
The SDF export is explicitly labelled **단독 분자 3D SDF**, since it exports the
selected molecule's separately generated conformer rather than its bound pose.

Opening a specific saved file from the research archive is labelled as direct
artifact inspection and clears the compound selection. Selecting a compound again
returns to matching by the active mode and target.

Job scenes use the model registry returned by the AF3 output validator. Arbitrary
unregistered files cannot inherit model provenance. Imported flat output files and
nested local execution outputs are both supported; every selected structure must
match its recorded SHA-256. Imported artifacts remain marked
`execution_verified=false` with an explicit unverified-source warning. They are
never presented as experimentally determined coordinates. The fixed 5IKR reference
route rechecks its entry identifier and experimental X-ray method on cached reads.

Generation is bounded to 5,000 SMILES characters, one connected molecule,
250 atoms **including explicit hydrogens**, 1–4 conformers, one CPU thread,
200 embedding iterations, and 500 force-field minimization iterations.
The scene importer accepts up to 30,000 atom records and 50 MB per mmCIF file.
These limits are checked before returning an interactive scene; larger files can
still be retrieved through the original artifact download endpoint.

## Scene contract

- `schema_version`: currently `1`.
- `source`: `rdkit_conformer`, `alphafold3_prediction`, `experimental_pdb`, or
  `structure_file` when an independent importer has not identified a source.
- `atoms`: zero-based numeric `id`/`index`, `element`, `name`, `x`, `y`, `z`,
  `chain_id`, `residue_id`, `residue_name`, `sequence_id`, `is_protein`,
  `is_ligand`, `formal_charge`, `aromatic`, `confidence`, and `b_factor`.
- `bonds`: zero-based atom-index `source` and `target`, numeric `order`,
  `aromatic`, `provenance`, `bond_order_known`, and `length_angstrom`.
- `chains` and `residues`: atom/residue counts and explicit atom-index memberships.
- `metadata`: source-specific method, input and identifier information, units,
  and SHA-256 checksums for saved/imported structures.
- `geometry`: atom/bond counts, coordinate span and centroid, maximum bond length,
  and conservative long/short covalent-bond counts.
- `warnings`: scientific interpretation and data-quality limitations.
- `energy`: method, kcal/mol value, convergence and iteration status for a
  generated free-molecule conformer; `null` for imported structures.

Coordinates are finite Cartesian Ångströms. File coordinates are retained
verbatim. Camera centering is a viewer transform, not a modification to scientific
coordinates. Bond indices refer directly to `atoms[index]`; the renderer must not
re-sort the atom array without updating its index mapping.

In AF3 scenes, `confidence` is the recorded 0–100 pLDDT value. Experimental
`B_iso_or_equiv` values appear only as `b_factor`; they are **not** pLDDT.
Unknown formal charges are `null`, not an invented zero.

## Conformer chemistry

RDKit ETKDGv3 embeds the submitted molecular graph with a fixed random seed and
`enforceChirality=true`. Explicit hydrogens are included during optimization, even
when the returned scene omits them. MMFF94s is used when all parameters exist;
otherwise the service tries UFF. If neither can parameterize the molecule,
ETKDG coordinates remain available with a null energy and an explicit warning.
The lowest energy **converged** conformer is preferred among generated conformers;
if all fail to converge, that status remains visible.

Specified stereochemistry is preserved. Unspecified tetrahedral and double-bond
stereochemistry remains flagged: one 3D realization does not establish the intended
stereoisomer. The original canonical isomeric SMILES is recorded before embedding.
The submitted structure is not silently neutralized, fragment-stripped,
tautomerized, or changed to a presumed physiological protonation state.

The reported intramolecular force-field energy is not binding free energy,
Kd/Ki/IC50, an efficacy estimate, or a valid cross-compound affinity ranking.

## Imported structure connectivity

Bond construction follows explicit chemistry in this order:

1. Named `_chem_comp_bond` definitions embedded in the mmCIF.
2. Named wwPDB Chemical Component Dictionary templates when supplied by the
   reference importer.
3. RDKit's named standard amino-acid residue templates as a fallback.
4. For AF3 SMILES ligands, reconstruct the upstream AF3 atom-name convention
   from the **exact SMILES echoed by the output**, or the recorded job input
   when it is absent. Require the complete observed heavy-atom name and element
   set to match. Coordinates may be in any row order. A mismatch omits the
   ligand's bonds and reports why.

Consecutive polymer label sequence IDs define peptide C–N links. Explicit mmCIF
covalent/disulfide records add specified cross-residue links when both atoms are
unambiguously present. Symmetry-mate connections absent from the displayed unit
and metal-coordination/noncovalent records are not rendered as ordinary covalent
bonds. An explicitly covalent connection with unknown bond order has
`bond_order_known=false`; its single visual stick is not an asserted order.

No broad nearest-neighbor bond guessing is performed. Unknown ligand templates
produce a warning and omitted bonds. This matters especially for badly distorted
AF3 outputs, where a distance cutoff could create a convincing but chemically
false network. Standard residue fallback bond orders describe that template and
do not establish protonation or tautomer populations.

The first mmCIF model is displayed. When alternate locations exist, one alternate
per residue is selected by summed occupancy, with a deterministic lexicographic
tie-break; shared atoms remain. The original artifact preserves all records.

## Verified local results

The initial 2026-09-08 selection fix passed the 290-test backend suite; the later
separate-calculation implementation passed 326 tests. Production browser checks
now exclude the quarantined ibuprofen smoke and follow eligible saved predictions
for the exact selected molecule. They also exercise two deposited complexes,
ligand autofocus/reset, delayed-response races, standalone conformer fallback and
direct archive inspection. Historical results reflect the parameter configuration
at their recorded time. See
[browser measurements](molecular-selection-verification.json),
[all six experimental reference checks](molecular-reference-verification.json),
and [zoom/keyboard/touch checks](molecular-selection-zoom-verification.json).
`scripts/verify_molecular_selection.py` reproduces the selection checks against a
running server without preparing or executing another prediction job.
Wheel packaging includes the pinned target sequence and experimental registry.

On 2026-09-07 the service passed 32 focused tests covering deterministic geometry,
hydrogen handling, actual SDF stereochemistry, aromatic/double bond orders,
incomplete stereo, absent force fields, resource limits, malformed/non-finite
mmCIF data, name-based AF3 ligand mapping, traversal/symlink confinement,
actual import-to-scene API integration, smoke-job selection, post-validation
checksum changes, and experimental-source labelling.

The existing `ptgs2_ibuprofen_smoke` output contains 4,880 atoms and 5,015 named
covalent bonds; **5,013 bonds exceed 2.4 Å**. The worst bond is about 225.4 Å.
Those coordinates are retained with a prominent geometry warning. This output
cannot support a credible binding-pose interpretation despite containing
per-atom confidence values.

The separately downloaded RCSB 5IKR reference contains 18,781 displayed atoms and
18,575 named bonds in the imported model. No bonds fell outside the conservative
0.65–2.4 Å display sanity interval. This check is not a full structural validation
report. It is the experimentally deposited mefenamic-acid complex, not evidence
about a new candidate. Its fetched mmCIF SHA-256 was
`a4a20672b28f87d32a4cdff8ae7dbdbe4c94af40bc86d4b7caf0122d860cf472`.

## Primary sources

- [RDKit conformer generation and ETKDG parameters](https://www.rdkit.org/docs/RDKit_Book.html#conformer-generation): deterministic seeds, specified chirality, experimental torsions and knowledge-based embedding.
- [RDKit force-field helper API](https://www.rdkit.org/docs/source/rdkit.Chem.rdForceFieldHelpers.html): MMFF/UFF parameter coverage and optimizer return status.
- [wwPDB Chemical Component Dictionary](https://www.wwpdb.org/data/ccd): atom names, element identities, bond order and aromatic flags.
- [AlphaFold 3 v3.0.4 atom-name assignment](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/src/alphafold3/data/tools/rdkit_utils.py): `assign_atom_names_from_graph` uses uppercase element symbols and counters in the original SMILES graph traversal.
- [AlphaFold 3 v3.0.4 ligand atom layout](https://github.com/google-deepmind/alphafold3/blob/v3.0.4/src/alphafold3/model/atom_layout/atom_layout.py): SMILES parsing, named atom generation and hydrogen filtering.
- [RCSB PDB 5IKR](https://www.rcsb.org/structure/5IKR): the experimentally determined structure of mefenamic acid bound to human cyclooxygenase-2.
- [RCSB 5IKR validation report](https://files.rcsb.org/pub/pdb/validation_reports/ik/5ikr/5ikr_full_validation.pdf): deposited structure validation, separate from the viewer's conservative bond-distance flags.
