"""Scientific Reports figure arrangement; original data and figures are retained."""
from __future__ import annotations
import hashlib
import json
import re
import shutil
from pathlib import Path
import matplotlib
from matplotlib.lines import Line2D
import manuscript_figures as original

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/manuscript/figures/scientific-reports'
MAP={
 'figure-01-evidence-architecture':'figure-01-evidence-architecture',
 'figure-02-catalog-and-enumeration':'figure-03-catalog-and-enumeration',
 'figure-03-alphafold-controlled-comparison':'figure-04-alphafold-controlled-comparison',
 'figure-04-quantum-kernel-comparison':'figure-05-quantum-kernel-comparison',
 'figure-05-observables-and-noise-controls':'figure-06-observables-and-noise-controls',
 'figure-06-assay-performance-and-abstention':'figure-07-assay-performance-and-abstention',
 'figure-S1-msa-and-coordinate-audit':'figure-S1-msa-and-coordinate-audit',
 'figure-S2-tox21-calibration-and-coverage':'figure-S2-tox21-calibration-and-coverage'}

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()


class SubmissionFigures(original.Figures):
    def __init__(self):
        super().__init__(OUT)
        self.preview=ROOT/'tmp/manuscript/q1-revision/figure-previews';self.preview.mkdir(parents=True,exist_ok=True)
        self.track('scripts/manuscript_submission_figures.py')

    def save(self,fig,name,caption,data=None):
        name=MAP[name]
        for t in fig.findobj(matplotlib.text.Text):
            label=t.get_text()
            if 'D/E are separate rigid views' in label:
                label=label.replace('D/E are separate rigid views','d/e are separate rigid views');t.set_text(label)
            if 'Bars in D:' in label:
                label=label.replace('Bars in D:','Bars in d:');t.set_text(label)
            if label.startswith('A: 10 circuits'):
                label=label.replace('A: 10','a: 10').replace('B: 17','b: 17').replace('A and B-D','a and b–d');t.set_text(label)
            if re.fullmatch('[A-F]',label) and t.get_fontsize()>=11 and t.get_fontweight()=='bold':t.set_text(label.lower())
            elif re.match(r'^[A-F]  ',label):t.set_text(r'$\mathbf{'+label[0].lower()+'}$'+label[1:])
        for line in fig.findobj(Line2D):
            if line.get_linewidth()<1:line.set_linewidth(1)
        for ax in fig.axes:
            for spine in ax.spines.values():spine.set_linewidth(1)
        caption=re.sub(r'\(([A-F](?:,[A-F])*)\)',lambda m:'('+m[1].lower()+')',caption)
        caption=caption.replace('Panel A uses', 'Panel a uses').replace('panels B-D', 'panels b–d')
        n=name.split('-')[1]
        if n.isdigit():caption=re.sub(r'^Figure \d+\.',f'Figure {int(n)}.',caption)
        display=self.output/(name+'.docx.png');fig.savefig(display,dpi=240)
        super().save(fig,name,caption,data)
        self.figures[-1]['document_image']={'path':str(display.relative_to(ROOT)),'sha256':digest(display),'bytes':display.stat().st_size,'dpi':240}


def main():
    f=SubmissionFigures()
    for method in [original.figure1,original.figure2,original.figure3,original.figure4,original.figure5,original.figure6,original.supplement1,original.supplement2]:method(f)
    additions={
      'figure-07-herbal-chemical-space':'figure-02-herbal-chemical-space',
      'figure-S3-ligand-contact-stability':'figure-S3-ligand-contact-stability',
      'figure-S4-quantum-sensitivity':'figure-S4-quantum-sensitivity'}
    for source,name in additions.items():
        entry={'id':name,'files':[],'png_dpi':600,'derived_from':source}
        for ext in ['svg','pdf','png','docx.png']:
            p=ROOT/'output/manuscript/figures'/f'{source}.{ext}'
            if ext=='docx.png' and not p.exists():continue
            assert p.exists(),p
            q=OUT/f'{name}.{ext}';shutil.copy2(p,q);f.track(p)
            record={'path':str(q.relative_to(ROOT)),'sha256':digest(q),'bytes':q.stat().st_size}
            if ext=='docx.png':entry['document_image']=record
            else:entry['files'].append(record)
        p=ROOT/'output/manuscript/figures'/f'{source}.caption.txt';caption=p.read_text().strip()
        if name.startswith('figure-02-'):caption=re.sub(r'^Figure \d+\.', 'Figure 2.',caption)
        if name=='figure-S3-ligand-contact-stability':
            caption=('Supplementary Figure S3. Predicted contact persistence and ligand-placement variation. '
                '(a,b) Per-sample protein-residue contact maps for standard aspirin and quercetin predictions, using minimum inter-chain heavy-atom distance. Cells are rounded to 0.1 Å; colors use unrounded distances and gray exceeds 5 Å. '
                '(c,d) Ligand heavy-atom RMSD matrices for aspirin and quercetin after alignment of all 604 receptor C-alpha positions. '
                '(e) Residue-set Jaccard similarity at 4 and 5 Å across all ten sample pairs per standard job. '
                '(f) Per-sample PAE means in both directions over all protein tokens, plus protein-frame to ligand means restricted to the 5 Å contact residues. '
                'Each condition contains five diffusion samples from seed 1; pairwise comparisons share samples. Contacts are geometric neighbourhoods, not assigned chemical interactions. Ligands receive the receptor transformation without a separate ligand fit; exact graph automorphism checks retain one atom mapping per ligand. The 2 Å screen is deliberately limited and is distinct from AF3’s large-clash flag. No panel measures ligand-pose accuracy against an experimental complex of the selected ligand. Full atom contacts, residue profiles, direction-specific PAE and sample-pair values are supplied in q1_extension/af3/.')
        if name=='figure-S4-quantum-sensitivity':
            caption=('Supplementary Figure S4. Sensitivity of the measured projected quantum kernel. '
                '(a) Off-diagonal kernel spread over 81 logarithmically spaced gamma values. '
                '(b) Centered alignment with matched ideal-block and encoded-input classical references. '
                '(c) Centered entropy effective rank; the four-input centered-rank ceiling is three. '
                '(d) Errors in prepared-zero and prepared-one controls. '
                '(e) Mean and maximum off-diagonal changes after excluding measured qubit coordinates using control errors. '
                '(f) Input-pair and repeated-input distances relative to their plug-in shot-noise floors. '
                'All panels reuse one acquisition of seventeen circuits with 1,024 shots each; they add no hardware repetitions or biological labels. Exclusion uses the larger observed zero/one-preparation error and renormalizes distances by the retained qubit count. It changes post-processing and descriptor weighting, not the executed circuit. All-qubit gamma one is the primary analysis. Increasing gamma changes contrast and can drive distinct-input kernels towards identity; equal gamma across feature spaces does not imply equal effective bandwidth. Thresholds and bandwidths are post hoc diagnostics, not independently validated mitigation or optimization. Six off-diagonal pairs share four inputs. Complete scenarios, eigenvalues, definitions and source hashes are in q1_extension/quantum/.')

        (OUT/f'{name}.caption.txt').write_text(caption+'\n');f.captions[name]=caption;f.track(p);f.figures.append(entry)
    f.finish()
    r=json.loads((OUT/'figure-manifest.json').read_text());r['target_journal']='Scientific Reports';r['main_figures']=7;r['supplementary_figures']=4;r['numerical_mutations']=False
    (OUT/'figure-manifest.json').write_text(json.dumps(r,indent=2)+'\n')


if __name__=='__main__':main()
