"""Journal supplement tables derived from archived secondary-analysis summaries."""
import json


def extend_tables(t, source):
    def add(key, title, columns, values, widths, note, size=8.5):
        assert sum(widths) == 9360
        assert all(len(row) == len(columns) for row in values)
        t[key] = dict(title=title, columns=columns, rows=values,
                      widths_dxa=widths, note=note, size=size)

    for old, new in [('2', 'S7'), ('3', 'S8')]:
        t[new] = t.pop(old)
        t[new]['title'] = t[new]['title'].replace('Table '+old+'.', 'Supplementary Table '+new+'.')
    chem = json.loads((source/'q1_extension/chemistry/summary.json').read_text())
    names = dict(gegen='Gegen', huangqin='Huangqin', huanglian='Huanglian',
                 gancao='Gancao', herb_union='Herb union', drug_reference='Drug reference')
    values = []
    for r in chem['groups']:
        values.append([names.get(r['group'], r['group']), str(r['structures']),
                       f"{r['mw_median']:.2f}", f"{r['tpsa_median']:.2f}",
                       str(r['nonempty_scaffolds']), str(r.get('exact_drug_structures') or '—'),
                       '—' if not isinstance(r.get('nearest_drug_tanimoto_median'), (float,int)) else f"{r['nearest_drug_tanimoto_median']:.3f}"])
    add('S9', 'Supplementary Table S9. Chemical properties and reference overlap',
        ['Group', 'n', 'Median MW\n(Da)', 'Median TPSA\n(Å²)', 'Nonempty\nscaffolds', 'Exact drug\nmatches', 'Median nearest\nTanimoto'],
        values, [1620, 740, 1420, 1420, 1420, 1420, 1320],
        'Herb groups overlap. Scaffolds are distinct nonempty achiral Bemis–Murcko structures; the herb union and reference contain 65 and 274 acyclic structures, respectively. Reference label: archived chembl-approved import, without new regulatory verification. Fingerprints: radius 2, 2,048 bits, chirality enabled. The combined fit contains 4,697 unique stored structures. All eight descriptors, quartiles, nearest neighbours and PCA loadings are in q1_extension/chemistry/.')

    af = json.loads((source/'q1_extension/af3/contact_analysis.json').read_text())
    values = []
    for r in sorted(af['groups'], key=lambda x: (x['molecule'], x['condition'])):
        c4, c5 = r['contacts_4A'], r['contacts_5A']
        def span(key):
            v = r[key]
            return f"{v['min']:.2f}–{v['max']:.2f}"
        values.append([r['molecule'].capitalize(), 'Baseline' if r['condition']=='baseline' else 'Standard',
                       f"{c4['union_count']} / {c4['all_five_count']}",
                       f"{c5['union_count']} / {c5['all_five_count']}",
                       f"{c5['pairwise_jaccard']['mean']:.3f}",
                       span('ligand_rmsd_receptor_604CA_fixed_names_angstrom'),
                       span('ligand_rmsd_receptor_551CA_fixed_names_angstrom'),
                       str(r['samples_with_interchain_pair_below_2A'])])
    add('S10', 'Supplementary Table S10. Contact persistence and ligand-placement variation',
        ['Ligand', 'Condition', '4 Å residues\nunion / all 5', '5 Å residues\nunion / all 5',
         'Mean 5 Å\nJaccard', 'Ligand RMSD\n604 Cα (Å)', 'Ligand RMSD\n551 Cα (Å)', 'n\n<2 Å'],
        values, [1300, 1160, 1280, 1280, 1000, 1280, 1280, 780],
        'Each row comprises five diffusion samples from seed 1 and ten dependent sample pairs. Contacts use any inter-chain heavy-atom pair within the cutoff. RMSD ranges follow receptor alignment only, with the indicated C-alpha set and fixed ligand-atom mapping. All-five means the intersection across five residue sets. The <2 Å count is a limited inter-chain proximity screen, not a full physical-validity assessment. No ligand is compared with a matching experimental pose. Source: q1_extension/af3/contact_analysis.json.', 8.0)

    q = json.loads((source/'q1_extension/quantum/summary.json').read_text())
    import csv
    with (source/'q1_extension/quantum/gamma_sensitivity.csv').open() as f:
        gamma_rows = list(csv.DictReader(f))
    values = []
    for gamma in [.01, 1., 100.]:
        r = next(r for r in gamma_rows if r['scenario']=='all' and r['method']=='measured' and abs(float(r['gamma'])-gamma)<1e-9)
        values.append(['Bandwidth', '156', f'{gamma:g}', f"{float(r['off_diagonal_spread']):.6f}",
                       f"{float(r['centered_effective_rank']):.3f}", '—'])
    for r in q['exclusion_at_gamma_one']:
        if r['max_control_error_threshold'] is None:
            continue
        values.append([f"Control ≤{r['max_control_error_threshold']:.0%}", str(r['retained_qubits']), '1',
                       f"{r['off_diagonal_spread']:.6f}", f"{r['centered_effective_rank']:.3f}",
                       f"{r['off_diagonal_max_absolute_change_from_all']:.6f}"])
    add('S11', 'Supplementary Table S11. Post hoc bandwidth and control-exclusion sensitivity',
        ['Scenario', 'Qubits', 'γ', 'Off-diagonal\nspread', 'Centered\neffective rank', 'Maximum |ΔK|\nfrom all qubits'],
        values, [2250, 850, 850, 1830, 1780, 1800],
        'All rows reuse one hardware acquisition for four inputs; they are not independent experiments. The complete bandwidth series has 81 values. Spread is maximum minus minimum across six dependent off-diagonal pairs. Centered effective rank has ceiling 3. Control thresholds use the larger of zero- and one-preparation errors, remove measured coordinates post hoc, and renormalize by the retained count. Delta values compare gamma-one kernels; no readout correction or biological-label optimization is applied. Source: q1_extension/quantum/.')

    add('S12', 'Supplementary Table S12. Controls, analysis units and remaining validation gaps',
        ['Analysis', 'Reported unit', 'Retained control / comparison', 'Not established'], [
        ['Herbal chemistry', '1,293 structures', 'Exact taxon tokens; 3,417 drug references', 'Prepared-material composition or exposure'],
        ['Enumeration', '5 million attempts', 'Full uniqueness/parent audit; 500 reconstructions', 'Synthesis, patent novelty or potency'],
        ['AF3 confidence', '2 ligand–target pairs', 'Four matched jobs; ±MSA and templates', 'Independent MSA effect or blind generalization'],
        ['Ligand placement', '5 samples per job', '4/5 Å contacts; 551/604 Cα fits', 'Experimental pose accuracy or affinity'],
        ['Quantum features', '4 input vectors', 'Repeat; prepared states; ideal/classical references', 'Quantum advantage or predictive benefit'],
        ['Assay inference', '15,740 candidates', 'Scaffold holdout; baselines; domain abstention', 'Prospective efficacy or human safety'],
        ['Software identity', 'Artifact selection', 'Input, file-hash and stale-response checks', 'Biological validity of displayed results'],
        ], [1700, 1640, 3100, 2920],
        'The same molecule or file may contribute to multiple summaries. Counts must not be pooled into an end-to-end validation cohort. Existing observations, inferred scores, failed computations and unavailable results are reported separately. Formal population tests are not reported for the two-ligand/four-input pilots.', 8.4)
    return t
