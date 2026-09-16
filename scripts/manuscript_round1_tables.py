"""Editable revision tables from audited machine-readable ledgers."""
import csv,json,hashlib

def extend(t,src):
    r1=src/'round1'
    def rows(name):return list(csv.DictReader((r1/name).open()))
    def add(key,title,cols,values,widths,note,size=8.7):
        assert sum(widths)==9360 and all(len(x)==len(cols) for x in values)
        t[key]={'title':title,'columns':cols,'rows':values,'widths_dxa':widths,'note':note,'size':size}
    t['S13']=dict(t['1']);t['S13']['title']='Supplementary Table S13. Separate evidence cohorts and analysis units'
    add('1','Table 1. Implemented evidence constraints and inspectable evaluation',
        ['Constraint','Concrete acceptance / rejection','Validator and evidence','Interpretive boundary'],[
        ['Selected structure','Accept matching ligand graph and full target sequence; reject either mismatch.','Molecular selection validator; controlled raw-CIF joins (Table S22).','Tests a join, not binding or experimental pose accuracy.'],
        ['Selected diagnostics','Bind confidence and contacts to the selected output sample; reject stale or mismatched diagnostics.','AF3 diagnostics and frontend regression tests; exact artifact hashes.','Internal confidence remains separate from physical plausibility.'],
        ['Generated lineage','Retain distinct standardized parents and replay recorded fragments to the exact product.','Generation audit; first 500 SHA-ordered products and selected fragment snapshot.','Subset reconstruction and full-table archived checks have different denominators.'],
        ['Quantum measurement','Bind ordered features, compiled circuits, bit order and counts; expose incomplete/error records.','Quantum validators, QPY/count audit and zero-return observations.','Measured zero, absent measurement and defined kernel diagonal differ.'],
        ['Assay reporting','Preserve assay/endpoint semantics; observed matches precede qualified in-domain estimates.','Curation, 59-rule ledger and all 9 assay / 12 endpoint decisions (Tables S16–S19).','Rules are retrospective; the PTGES description discrepancy was a manual audit.'],
        ['Current readiness','Reject missing or changed prerequisites before launching inference; archived jobs keep their own settings.','Readiness/parameter fixtures and execution-state regression tests.','Readiness is not proof of historical execution or biological validity.'],
    ],[1540,2840,2810,2170], 'This table maps the application-specific contribution to inspectable artifacts. Table S15 compares established alternatives, without assuming that an undocumented capability is absent. Table S22 and the delivered constraint ledger separate controlled injections, ordinary fixtures and naturally archived observations. Counts are test cases, not biological replicates.',8.8)
    # First-use labels also remain interpretable in standalone tables.
    t['S4']['vertical_padding_dxa']=45
    for row in t['S4']['rows']:
        row[1]={'Baseline':'No MSA/templates','Standard':'MSA + templates'}.get(row[1],row[1])
    for key in ('S6',):
        t[key]['note']+=' Only 25 of the 27 calibration structures met the domain cutoff. Tables S20–S21 and Fig. S5 add structural residuals, grouped uncertainty and the separately labelled post-review reconstruction.'
    tax=json.loads((r1/'related_work/powo_taxon_crosswalk.json').read_text())['taxa']
    names={'갈근':'Gegen / 갈근','황금':'Huangqin / 황금','황련':'Huanglian / 황련','감초':'Gancao / 감초','갈근 동의어':'Gegen synonym'}
    add('S14','Supplementary Table S14. Herb names, queried taxa and stable POWO identifiers',
        ['Herb context','Recorded query name','POWO / IPNI ID','Status'],
        [[names[x['korean_label_context']],x['name_without_authority'],x['ipni_id'],x['taxonomic_status']] for x in tax],
        [1800,3680,1740,2140], 'GQD: Gegen Qinlian decoction. Full stable links use https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names: followed by the ID. Accessed 9 September 2026. The occurrence export retains original taxon tokens alongside this crosswalk; nomenclatural resolution does not authenticate medicinal material or an occurrence experiment.')
    comp=json.loads((r1/'related_work/comparison.json').read_text())
    systems=list(dict.fromkeys(x['platform'] for x in comp['rows']))
    lookup={(x['platform'],x['dimension']):x['finding'] for x in comp['rows']}
    dims=comp['dimensions']
    for suffix,fields,labels in [('A',dims[:3],['Taxonomy / chemical identity','Original / generated lineage','Assay meaning']),('B',dims[3:],['Artifact binding','Unavailable / error states','Replay support'])]:
        add('S15'+suffix,'Supplementary Table S15'+suffix.lower()+'. Closest-system comparison: '+('identity and evidence' if suffix=='A' else 'execution and replay'),
            ['System']+labels,[[s]+[lookup[s,d] for d in fields] for s in systems],[1290,2690,2690,2690],
            'Documentary comparison of the specified papers/repositories, inspected 9 September 2026; no competitor was deployed or benchmarked. “Not described” denotes the inspected-source limit, not proven absence. Cell-level sources/sections/versions are in related_work/comparison.json. ENPKG, HERB 2.0, NP-KG, AiiDA, CWLProv, LOTUS and COCONUT are cited in the main Introduction. HerbFold entries are linked to source functions and tests in herbfold_code_evidence.json.',8.5)
    gates=rows('assays/gate_ledger.csv')
    for suffix,start,end,label in [('A',1,19,'identity, source curation and selection'),('B',20,40,'biochemical model eligibility and reporting'),('C',41,59,'Tox21 evaluation and reporting')]:
        subset=[x for x in gates if start<=int(x['execution_order'])<=end]
        add('S16'+suffix,'Supplementary Table S16'+suffix.lower()+'. Executable policy: '+label,
            ['Policy ID / stage','Criterion','Operator / setting','Operational rationale'],
            [[x['execution_order']+' / '+x['stage'].replace('curation/selection','curation / selection'),x['criterion'],x['comparison']+('\n'+x['threshold'] if x['threshold'] else ''),x['rationale']] for x in subset],
            [1550,2220,2850,2740], 'Source file, line, anchor, scope and chronology for every row are in assays/gate_ledger.csv. IDs identify policy rows, not a literal global trace; the accompanying Methods gives actual branch order (partitioning precedes partition-size checks). Numerical cutoffs are recorded settings, not independently optimized/validated boundaries. No timestamped preregistration or outcome-blind freeze was established. Training fits the model; tuning selects RF/ridge; calibration sets the residual width; held-out outcomes also qualify reporting, so qualification is retrospective.',8.5)
    groups=rows('assays/assay_groups.csv')
    add('S17','Supplementary Table S17. Complete ledger for nine count-selected assay groups',
        ['Target / endpoint','Assay ID / type','Structures / scaffolds','Gate disposition'],
        [[x['target']+' / '+x['endpoint'],x['assay_id']+' / '+x['assay_type'],x['unique_structures']+' / '+x['scaffolds'],x['quality_status']+('; '+x['semantic_caution'] if x['semantic_caution'] else '')] for x in groups],
        [1660,2430,1440,3830], 'The largest curated assay per target/endpoint/assay-type stratum was selected, breaking ties by assay ID. The complete selection pool (2,140 assay groups) is supplied separately. Eight displayed groups fail initial structure/scaffold support; subsequent model gates are not applicable. The qualified hERG group passes every recorded gate. Full descriptions, row IDs, pass/fail fields and reasons are in assays/assay_groups.csv; the PTGES description issue is a manual semantic warning, not an automated correction.')
    tox=rows('assays/tox21_endpoints.csv')
    add('S18','Supplementary Table S18. Class counts and reporting gates for all Tox21 endpoints',
        ['Endpoint','Train + / −','Test + / −','Test scaffolds','Unlabelled / conflicted','All quality gates'],
        [[x['endpoint'],x['train_positive']+' / '+x['train_negative'],x['test_positive']+' / '+x['test_negative'],x['test_scaffolds'],x['missing_or_conflicting_labels'],'Pass' if x['quality_gate_passed']=='True' else x['failed_criteria']] for x in tox],
        [1820,1630,1510,1220,1800,1380], 'Each endpoint starts from 7,617 curated structures. “Unlabelled / conflicted” is the total excluded endpoint-label count, not two additive categories; conflict-only counts are in the CSV. Tables S3a–b report performance; exact ROC, AP and Brier values and each quality result are in tox21_endpoints.csv. All twelve gates use the same held-out evaluations being reported.')
    sens=rows('assays/similarity_sensitivity.csv');sl={(x['endpoint'],x['threshold']):x for x in sens}
    vals=[]
    for threshold in ['0.3','0.35','0.4','0.45','0.5','0.55','0.6']:
        b=sl['KCNH2:IC50',threshold];q=sl['any_endpoint_union',threshold]
        vals.append([f'{float(threshold):.2f}',b['inside_domain_if_threshold_changed'],q['inside_domain_if_threshold_changed'],q['eligible_after_fixed_quality_and_observed_precedence']])
    add('S19','Supplementary Table S19. Candidate eligibility near the recorded similarity thresholds',
        ['Similarity cutoff','hERG inside domain','Tox21 inside ≥1 domain','Tox21 eligible for ≥1 inference'],vals,[1700,2210,2540,2910],
        'Denominator: 15,740 fixed candidates. Recorded cutoffs are 0.50 for hERG and 0.40 for Tox21; the other rows are post hoc cached-similarity scenarios. Fixed quality gates and observed-label precedence are retained. Lowering a cutoff creates no measured potency, new probability or validated alternative domain. At 0.40, Tox21 3,047 domain members = 3,011 inferred-score recipients + 36 observed-only. Exact per-endpoint and per-candidate profiles are distributed.')
    hg=rows('assays/herg_structure_predictions.csv');test=[x for x in hg if x['split']=='test']
    scaff={s:'S'+str(i+1).zfill(2) for i,s in enumerate(sorted({x['scaffold'] for x in test}))}
    vals=[];display=[]
    for i,x in enumerate(test,1):
        tag='H'+str(i).zfill(2);display.append({'display_id':tag,**x,'scaffold_display':scaff[x['scaffold']]})
        vals.append([tag,scaff[x['scaffold']],f"{float(x['actual_pactivity']):.3f}",f"{float(x['predicted_pactivity']):.3f}",f"{float(x['residual_predicted_minus_actual']):+.3f}",f"{float(x['nearest_training_similarity']):.3f}",'Yes' if x['in_domain']=='True' else 'No'])
    with (r1/'herg-display-key.csv').open('w') as h:
        w=csv.DictWriter(h,fieldnames=list(display[0]));w.writeheader();w.writerows(display)
    add('S20','Supplementary Table S20. All held-out hERG predictions and residuals',
        ['ID','Scaffold','Observed','Predicted','Residual','Similarity','In domain'],vals,[770,1070,1450,1450,1450,1780,1390],
        'Observed/predicted values and signed residuals use pIC50; residual = predicted − observed. N = 24 structures / 19 scaffold groups; in-domain n = 23 / 18 groups. H/S display keys are mapped to exact SMILES, activity IDs and scaffold strings in herg-display-key.csv. Identity is not inferred from a truncated digest. The fixed ±0.647691 interval covers 21/23 in-domain observations.',8.5)
    boot=rows('assays/herg_bootstrap.csv')
    keep={'mae':'MAE','rmse':'RMSE','r2':'R²','spearman':'Spearman ρ','interval_coverage_fixed_archived_half_width':'Interval coverage','paired_mae_improvement_over_median':'Baseline MAE − RF MAE','median_baseline_mae':'Training-median baseline MAE'}
    add('S21','Supplementary Table S21. Conditional hERG uncertainty from scaffold resampling',
        ['Subset / unit count','Metric','Estimate','95% percentile range'],
        [[x['subset'].replace('_',' ')+'\n'+x['structures']+' structures / '+x['scaffolds']+' groups',keep.get(x['metric'],x['metric'].replace('_',' ')),f"{float(x['point']):.3f}",f"[{float(x['lower95']):.3f}, {float(x['upper95']):.3f}]"] for x in boot],
        [2920,2660,1350,2430], '2,000 whole-scaffold draws with replacement, seed 20260909, linear 2.5th/97.5th percentiles. Each draw retains all molecules in selected groups, with multiplicity. Paired baseline differences use the same draw. The selected RF, split, domain and interval width are fixed: these intervals exclude model/gate-selection, training and calibration uncertainty. Coverage estimates are unstable with 18–19 independent groups. Ridge per-structure comparisons are not inferred from aggregate MAE. Full metric names and valid-draw counts remain in assays/herg_bootstrap.csv.')
    ledger=rows('constraints/contribution-artifact-ledger.csv')
    add('S22','Supplementary Table S22. Constraint-to-artifact ledger and enforcement scope',
        ['ID / constraint','Enforcement','Accepted / rejected connection','Evaluation unit / limitation'],
        [[x['invariant_id']+' / '+x['contribution'],x['enforcement'],x['accepted_join']+'\nReject/unavailable: '+x['rejected_or_unavailable_join'],x['denominator_scope']+'\n'+x['limitations']] for x in ledger],
        [1850,1570,3260,2680], 'Full file paths, function symbols, code hashes and individual test references are in constraints/contribution-artifact-ledger.csv. The separately tabulated 12-case diagnostic accepted 4 matching native joins and rejected 8 deliberately mismatched requests; the identity-oblivious comparator accepts all 12 by construction. Existing fixture suite: 89/89 passed in separate processes; two integrated Qiskit native crashes and an earlier environment-resolution failure are retained. Ligand bonds/stereochemistry come from the mmCIF declaration and compatible observed atoms, not independent coordinate-based recovery. These are neither a competitor benchmark nor an estimated field failure rate.',8.4)
    reconstruction=r1/'assays/postreview_reconstruction/summary.json'
    if reconstruction.exists():
        q=json.loads(reconstruction.read_text());b=q['calibration_width_bootstrap']
        add('S21B','Supplementary Table S21b. Separately labelled post-review calibration reconstruction',
            ['Quantity','Observed reconstruction / agreement','Scope'],[
            ['New RF fit','84 original training rows; seed 2026; 128 trees; leaf ≥3; max_features 0.33','2026-09-09 CPU replay of fixed model; no reselection.'],
            ['Archived test predictions','24/24 within atol 10⁻¹², rtol 10⁻¹¹; max difference '+f"{q['maximum_absolute_test_prediction_difference']:.2e}",'Agreement with saved predictions, not authentication of missing historical residuals.'],
            ['Calibration denominator','27 total; 25 in domain; 13 in-domain scaffold groups','All 27 new predictions are supplied with original row identities.'],
            ['90% nominal interval','rank min(n, ceil((n+1)×0.9)) = 24 of 25; half-width 0.647691 pIC50','Exactly matches archived half-width; full interval width 1.295382.'],
            ['Grouped width sensitivity',f"95% percentiles [{b['lower95']:.3f}, {b['upper95']:.3f}] pIC50",'2,000 resamples, seed 20260910; all members of sampled scaffolds retained.'],
            ['Instability at the quality cutoff','Upper percentile 1.029 exceeds original maximum half-width 1.0','Conditional sensitivity, not a guaranteed confidence or future-coverage statement.'],
            ],[2290,3800,3270], 'The historical archive did not contain individual calibration predictions. This new deterministic reconstruction is explicitly distinct, leaves historical files untouched and recreates the original split/model settings. Each bootstrap draw recomputes the finite-sample rank for its realized structure count. Model and observed calibration scaffold pool are fixed; training/model-selection and future-domain uncertainty remain outside this calculation.')
    return t
