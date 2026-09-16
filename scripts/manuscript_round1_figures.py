"""Round-1 labels/captions; all archived data values remain unchanged."""
from pathlib import Path
import json
import matplotlib
import manuscript_submission_figures as journal

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output/manuscript/figures/round1'

class Round1Figures(journal.SubmissionFigures):
    def save(self, fig, name, caption, data=None):
        for ax in fig.axes:
            old=[t.get_text() for t in ax.get_xticklabels()]
            new=[v.replace('No MSA','No MSA/\ntemplates').replace('MSA+T','MSA +\ntemplates') if v in {'No MSA','MSA+T'} else v for v in old]
            new=[v.replace('A / none','A / no MSA\nor templates').replace('Q / none','Q / no MSA\nor templates').replace('A / MSA','A / MSA +\ntemplates').replace('Q / MSA','Q / MSA +\ntemplates') for v in new]
            if new!=old:
                ax.set_xticks(ax.get_xticks(),new)
                for t in ax.texts:
                    if t.get_text() in {'Aspirin','Quercetin'} and t.get_position()[1]<0:
                        t.set_y(-.34)
        for t in fig.findobj(matplotlib.text.Text):
            v=t.get_text()
            v=v.replace('quercetin (M3)','quercetin (Q)').replace('measurement of M1','measurement of C1')
            if v=='No MSA':v='No MSA/\ntemplates'
            if v=='MSA+T':v='MSA +\ntemplates'
            v=v.replace('A / none','A / no MSA\nor templates').replace('Q / none','Q / no MSA\nor templates')
            v=v.replace('A / MSA','A / MSA +\ntemplates').replace('Q / MSA','Q / MSA +\ntemplates')
            t.set_text(v)
        if name=='figure-02-catalog-and-enumeration':
            caption+=' Timing components and their total are independently rounded from unrounded measurements. GQD is Gegen Qinlian decoction; Gegen/갈근, Huangqin/황금, Huanglian/황련 and Gancao/감초 correspond to the four taxa in panel a, respectively (taxon identifiers: Supplementary Table S14).'
        if name=='figure-04-quantum-kernel-comparison':
            caption+=' Verified stored row order: C1 (legacy M1) = brics-8d4157767bb0; C2 (M2) = brics-007ed18dee77; Q (M3) = quercetin; A (M4) = aspirin. These are selected cases, not a random chemical sample.'
        if name=='figure-05-observables-and-noise-controls':
            caption+=' The repeated input is C1 = brics-8d4157767bb0 (legacy M1); quercetin is Q (legacy M3). Bootstrap seed is 42 and percentile interpolation is linear; 64 resamples give coarse tail resolution.'
        if name=='figure-06-assay-performance-and-abstention':
            caption+=' Of 27 calibration structures, 25 met the hERG domain threshold and set the interval width. Tox21 domain coverage is 3,047 = 3,011 inferred-score recipients + 36 observed-only recipients; endpoint-specific observations can take precedence. Complete rules, exclusions and post-review grouped uncertainty appear in Supplementary Tables S16–S21 and Fig. S5.'
        super().save(fig,name,caption,data)

def main():
    journal.OUT=OUT
    journal.SubmissionFigures=Round1Figures
    journal.main()
    p=OUT/'figure-02-herbal-chemical-space.caption.txt'
    s=p.read_text().strip()
    s+=' GQD denotes Gegen Qinlian decoction. The herb/taxon and stable POWO identifier key is in Supplementary Table S14; occurrence counts describe local retrieval, not authenticated formula composition. Gegen: Pueraria montana var. lobata/P. lobata; Huangqin: Scutellaria baicalensis; Huanglian: Coptis chinensis; Gancao: Glycyrrhiza uralensis.'
    p.write_text(s+'\n')
    manifest=json.loads((OUT/'figure-manifest.json').read_text())
    manifest['revision']='R1, 2026-09-09; labels and captions revised without changing observations'
    (OUT/'figure-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')

if __name__=='__main__':main()
